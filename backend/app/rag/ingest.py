"""Build a full generation, then publish its vector and BM25 indexes together."""
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
from uuid import uuid4

from app.config import get_settings
from app.rag.bm25 import BM25Index
from app.rag.embedder import embed_documents
from app.rag.indexing import INDEX_LOCK, current_index, publish_index
from app.rag.loader import load_documents
from app.rag.splitter import search_text, split_document
from app.rag.vectorstore import VectorStore


def _flatten_meta(metadata: dict) -> dict:
    result = {}
    for key, value in metadata.items():
        if isinstance(value, list):
            result[str(key)] = ",".join(str(item) for item in value)
        elif value is None:
            result[str(key)] = ""
        elif isinstance(value, float) and not math.isfinite(value):
            result[str(key)] = str(value)
        elif isinstance(value, (str, int, float, bool)):
            result[str(key)] = value
        else:
            result[str(key)] = str(value)
    return result


def build_index() -> dict:
    """Rebuild all active notes without exposing a partially built generation."""
    with INDEX_LOCK:
        settings = get_settings()
        documents = load_documents(settings.raw_dir)
        snapshot_id = uuid4().hex
        root = Path(settings.index_dir).resolve()
        path = root / snapshot_id
        path.mkdir(parents=True)
        try:
            sources = path / "sources"
            sources.mkdir()
            (path / "chroma").mkdir()
            chunks = []
            manifest_documents = []
            for document in documents:
                source_path = f"sources/{document['doc_id']}.md"
                (path / source_path).write_bytes(document["raw_text"].encode("utf-8"))
                document_chunks = split_document(document, settings.chunk_size, settings.chunk_overlap,
                                                 strategy=settings.chunk_strategy, exclude_boilerplate=settings.exclude_boilerplate)
                for chunk in document_chunks:
                    chunk["metadata"]["snapshot_id"] = snapshot_id
                    chunk["metadata"] = _flatten_meta(chunk["metadata"])
                chunks.extend(document_chunks)
                manifest_documents.append({
                    "doc_id": document["doc_id"],
                    "title": document["title"],
                    "relative_path": document["relative_path"],
                    "size_bytes": document["size_bytes"],
                    "revision_id": document["revision_id"],
                    "source_path": source_path,
                    "chunk_count": len(document_chunks),
                })

            if chunks:
                texts = [chunk["text"] for chunk in chunks]
                vectors = embed_documents([search_text(chunk) for chunk in chunks])
                store = VectorStore(path / "chroma", settings.collection_name)
                store.upsert(
                    ids=[chunk["chunk_id"] for chunk in chunks],
                    embeddings=vectors,
                    documents=texts,
                    metadatas=[chunk["metadata"] for chunk in chunks],
                )
            BM25Index(chunks).save(str(path / "bm25.pkl"))
            manifest = {
                "schema_version": 1,
                "snapshot_id": snapshot_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "embedding_model": settings.embed_model,
                "bm25_algorithm": "BM25L",
                "collection_name": settings.collection_name,
                "chunk_size": settings.chunk_size,
                "chunk_overlap": settings.chunk_overlap,
                "chunk_strategy": settings.chunk_strategy,
                "exclude_boilerplate": settings.exclude_boilerplate,
                "documents": manifest_documents,
                "document_count": len(documents),
                "chunk_count": len(chunks),
            }
            with (path / "manifest.json").open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(manifest, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            publish_index(snapshot_id)
        except Exception:
            # On Windows Chroma may keep handles open. Leftovers remain unpublished.
            can_remove = False
            try:
                active = current_index()
                can_remove = active is None or active["snapshot_id"] != snapshot_id
            except Exception:
                # Preserve the generation when publication cannot be established.
                pass
            if can_remove and path.resolve().is_relative_to(root) and path.resolve() != root:
                shutil.rmtree(path, ignore_errors=True)
            raise
        return {"documents": len(documents), "chunks": len(chunks), "snapshot_id": snapshot_id}


if __name__ == "__main__":
    print(build_index())
