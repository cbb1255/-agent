"""3嵌入模型封装（框架已实现）。

bge 系列模型对「查询侧」要加指令前缀，文档侧不加，加错会明显掉效果。
"""
from functools import lru_cache
from threading import RLock
from app.config import get_settings

QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


_EMBED_LOAD_LOCK = RLock()


@lru_cache
def _cached_embedder():
    from app.rag.model_loader import load_sentence_transformer
    return load_sentence_transformer(get_settings().embed_model)


def get_embedder():
    with _EMBED_LOAD_LOCK:
        return _cached_embedder()


get_embedder.cache_clear = _cached_embedder.cache_clear


def embed_documents(texts: list[str]) -> list[list[float]]:
    """批量编码文档，不做指令前缀。"""
    model = get_embedder()
    vecs = model.encode(texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vecs]


def embed_query(text: str) -> list[float]:
    """编码查询，带指令前缀。"""
    model = get_embedder()
    return model.encode(QUERY_INSTRUCTION + text, normalize_embeddings=True).tolist()


def warmup() -> dict:
    """Preload embedder and optional reranker, timing each stage in ms."""
    import time

    started = time.monotonic()
    model = get_embedder()
    model.encode(QUERY_INSTRUCTION + "预热", normalize_embeddings=True)
    embed_ms = max(0, int((time.monotonic() - started) * 1000))
    rerank_ms = None
    if get_settings().enable_rerank:
        started = time.monotonic()
        from app.rag import reranker
        reranker.warmup()
        rerank_ms = max(0, int((time.monotonic() - started) * 1000))
    return {"embed_ms": embed_ms, "rerank_ms": rerank_ms}
