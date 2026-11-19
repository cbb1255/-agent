"""Explicit-path Chroma storage for a single immutable index generation."""
from pathlib import Path
import os

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import get_settings


def _storage_path(path: Path) -> str:
    """Give native HNSW an ASCII Windows path to the same storage directory."""
    value = str(path.resolve())
    if os.name != "nt" or value.isascii():
        return value
    import ctypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    short_path = kernel.GetShortPathNameW
    short_path.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    short_path.restype = ctypes.c_uint32
    size = short_path(value, None, 0)
    if not size:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_unicode_buffer(size)
    length = short_path(value, buffer, size)
    if not length or length >= size:
        raise ctypes.WinError(ctypes.get_last_error())
    if not buffer.value.isascii():
        raise ValueError("本机原生向量索引需要ASCII路径，请将INDEX_DIR设为英文目录或启用NTFS短文件名")
    return buffer.value


class VectorStore:
    def __init__(self, path: str | Path, collection_name: str | None = None, *, create: bool = True):
        path = Path(path)
        if create:
            path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(
            path=_storage_path(path), settings=ChromaSettings(anonymized_telemetry=False),
        )
        name = collection_name or get_settings().collection_name
        if create:
            self.collection = self.client.get_or_create_collection(
                name, metadata={"hnsw:space": "cosine"}, embedding_function=None,
            )
        else:
            self.collection = self.client.get_collection(name, embedding_function=None)

    def upsert(self, ids, embeddings, documents, metadatas):
        if not ids:
            return
        if len({len(ids), len(embeddings), len(documents), len(metadatas)}) != 1:
            raise ValueError("索引 ID、向量、正文和元数据条数必须一致")
        batch_size = self.client.get_max_batch_size()
        for start in range(0, len(ids), batch_size):
            end = start + batch_size
            self.collection.upsert(
                ids=ids[start:end], embeddings=embeddings[start:end],
                documents=documents[start:end], metadatas=metadatas[start:end],
            )

    def query(self, embedding, top_k: int) -> list[dict]:
        count = self.collection.count()
        if count == 0 or top_k <= 0:
            return []
        result = self.collection.query(
            query_embeddings=[embedding], n_results=min(top_k, count),
            include=["documents", "metadatas", "distances"],
        )
        return [{
            "chunk_id": chunk_id,
            "text": text,
            "metadata": metadata,
            "score": 1.0 - distance,
        } for chunk_id, text, metadata, distance in zip(
            result["ids"][0], result["documents"][0],
            result["metadatas"][0], result["distances"][0],
        )]
