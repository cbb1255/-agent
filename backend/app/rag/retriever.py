"""Hybrid retrieval bound to one published vector/BM25 generation per request."""
from functools import lru_cache
from ipaddress import IPv6Address
from pathlib import Path
from threading import RLock
import re
from urllib.parse import urlencode, urlsplit

from app.config import get_settings
from app.rag import reranker
from app.rag.bm25 import BM25Index
from app.rag.embedder import embed_query
from app.rag.indexing import current_index
from app.rag.vectorstore import VectorStore


_INDEX_LOAD_LOCK = RLock()


@lru_cache(maxsize=4)
def _cached_indexes(path: str, collection_name: str):
    """Reuse both indexes for immutable generations, with a bounded Python cache."""
    root = Path(path)
    return (
        VectorStore(root / "chroma", collection_name, create=False),
        BM25Index.load(str(root / "bm25.pkl")),
    )


def _load_indexes(path: str, collection_name: str):
    # Lock before entering the cache so concurrent misses cannot initialize twice.
    with _INDEX_LOAD_LOCK:
        return _cached_indexes(path, collection_name)


_load_indexes.cache_clear = _cached_indexes.cache_clear
_load_indexes.cache_info = _cached_indexes.cache_info


def warmup_indexes():
    index = current_index()
    if index is not None and index["manifest"]["chunk_count"]:
        _load_indexes(str(index["path"]), index["manifest"]["collection_name"])


def deduplicate_sources(items: list[dict], overlap: float) -> list[dict]:
    """Remove exact repeats and strongly overlapping spans from the same revision."""
    if overlap == 0:
        return list(items)
    selected = []
    for item in items:
        duplicate = False
        metadata = item.get("metadata") or {}
        for previous in selected:
            other = previous.get("metadata") or {}
            if not metadata.get("doc_id") or not metadata.get("revision_id"):
                continue
            if (metadata["doc_id"], metadata["revision_id"]) != (other.get("doc_id"), other.get("revision_id")):
                continue
            if item["text"].strip() == previous["text"].strip():
                duplicate = True
                break
            spans = (metadata.get("char_start"), metadata.get("char_end"), other.get("char_start"), other.get("char_end"))
            if any(type(value) is not int for value in spans):
                continue
            start, end, other_start, other_end = spans
            shorter = min(end - start, other_end - other_start)
            if shorter > 0 and max(0, min(end, other_end) - max(start, other_start)) / shorter >= overlap:
                duplicate = True
                break
        if not duplicate:
            selected.append(item)
    return selected


def rrf_fuse(vector_hits: list[dict], bm25_hits: list[dict], k: int = 60) -> list[dict]:
    fused = {}
    for hits in (vector_hits, bm25_hits):
        for rank, hit in enumerate(hits, 1):
            chunk_id = hit["chunk_id"]
            if chunk_id not in fused:
                fused[chunk_id] = {
                    "chunk_id": chunk_id,
                    "text": hit["text"],
                    "metadata": dict(hit["metadata"]),
                    "rrf_score": 0.0,
                }
            fused[chunk_id]["rrf_score"] += 1.0 / (k + rank)
    return sorted(fused.values(), key=lambda chunk: chunk["rrf_score"], reverse=True)


def _external_url(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if any(ord(character) <= 32 or ord(character) == 127 for character in value):
        return None
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        if (parsed.scheme not in ("http", "https") or not hostname
                or parsed.username is not None or parsed.password is not None):
            return None
        # Accessing port validates its syntax and numeric range.
        parsed.port
        if ":" in hostname:
            IPv6Address(hostname)
        else:
            hostname = hostname.encode("idna").decode("ascii").removesuffix(".")
            if len(hostname) > 253 or any(
                re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label) is None
                for label in hostname.split(".")
            ):
                return None
    except (ValueError, UnicodeError):
        return None
    return value


def to_citations(chunks: list[dict]) -> list[dict]:
    citations = []
    for index, chunk in enumerate(chunks, 1):
        metadata = chunk.get("metadata") or {}
        source_type = metadata.get("source_type") or "local"
        external_url = _external_url(metadata.get("url")) if source_type == "external" else None
        doc_id = metadata.get("doc_id")
        snapshot_id = metadata.get("snapshot_id") or chunk.get("snapshot_id")
        score = next((chunk[key] for key in ("final_score", "rrf_score", "score")
                      if chunk.get(key) is not None), None)
        source_url = None
        if source_type != "external" and doc_id and snapshot_id:
            source_url = f"/documents/{doc_id}/source?{urlencode({'snapshot': snapshot_id})}"
        citations.append({
            "index": index,
            "doc_id": doc_id,
            "title": metadata.get("title") or "未命名资料",
            "snippet": (chunk.get("text") or "")[:100],
            "score": score,
            "snapshot_id": snapshot_id,
            "relative_path": metadata.get("relative_path", ""),
            "source_path": metadata.get("source_path", ""),
            "revision_id": metadata.get("revision_id", ""),
            "heading": metadata.get("heading", ""),
            "line_start": metadata.get("line_start"),
            "line_end": metadata.get("line_end"),
            "source_url": source_url,
            "source_type": source_type,
            "external_url": external_url,
            "retrieved_at": (metadata.get("retrieved_at") or "") if source_type == "external" else "",
        })
    return citations


def retrieve(query: str, top_k: int | None = None, *, max_results: int | None = None) -> list[dict]:
    settings = get_settings()
    if not query.strip():
        return []
    top_k = settings.recall_top_k if top_k is None else top_k
    if top_k <= 0:
        raise ValueError("top_k 必须为正数")
    limit = settings.rerank_top_n if max_results is None else max_results
    if type(limit) is not int or limit <= 0:
        raise ValueError("max_results必须为正整数")
    index = current_index()
    if index is None or index["manifest"]["chunk_count"] == 0:
        return []
    manifest = index["manifest"]
    if manifest["embedding_model"] != settings.embed_model:
        raise ValueError("嵌入模型与当前索引不一致，请重建索引")
    store, bm25 = _load_indexes(str(index["path"]), manifest["collection_name"])
    vector_hits = store.query(embed_query(query), top_k)
    bm25_hits = bm25.search(query, top_k)
    fused = rrf_fuse(vector_hits, bm25_hits)
    if not fused:
        return []
    vector_scores = {hit["chunk_id"]: hit.get("score", 0.0) for hit in vector_hits}
    bm25_ranks = {hit["chunk_id"]: hit.get("rank", 999) for hit in bm25_hits}
    for chunk in fused:
        chunk["vec_score"] = vector_scores.get(chunk["chunk_id"], 0.0)
        chunk["bm25_rank"] = bm25_ranks.get(chunk["chunk_id"], 999)
        chunk["metadata"]["snapshot_id"] = index["snapshot_id"]
    # Filter the full fused candidate set before the final output limit.
    reranked = reranker.rerank(query, fused, len(fused))
    if settings.enable_rerank:
        # 重排分使用独立门槛，与向量门槛分开校准。
        filtered = [chunk for chunk in reranked if chunk.get("final_score", 0.0) >= settings.rerank_min_score]
        if filtered and settings.rerank_score_margin is not None:
            floor = filtered[0]["final_score"] - settings.rerank_score_margin
            filtered = [chunk for chunk in filtered if chunk["final_score"] >= floor]
    else:
        # BM25 前三放行是召回启发式，名次可配置（0 关闭）。这些分数不是概率。
        filtered = [chunk for chunk in reranked
                    if chunk.get("vec_score", 0.0) >= settings.min_score
                    or (settings.bm25_exempt_rank > 0 and chunk.get("bm25_rank", 999) <= settings.bm25_exempt_rank)]
    if settings.retrieval_dedup_overlap > 0:
        filtered = deduplicate_sources(filtered, settings.retrieval_dedup_overlap)
    return filtered[:limit]
