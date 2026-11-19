"""Manage Markdown files and read the original text of published snapshots."""
import hashlib
import os
import re
import tempfile
import unicodedata
from pathlib import Path
import yaml

from app.config import get_settings
from app.rag.indexing import INDEX_LOCK, current_index, read_index
from app.rag.ingest import build_index
from app.rag.loader import load_documents
from app.knowledge.operations import prepare_delete, complete_delete, preserve_uncertain_import, recover_deletions

DOCUMENT_ID = re.compile(r"doc_[0-9a-f]{24}\Z")
SNAPSHOT_ID = re.compile(r"[0-9a-f]{32}\Z")
REVISION_ID = re.compile(r"[0-9a-f]{64}\Z")
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL"}
WINDOWS_RESERVED.update(f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10))


class DocumentError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def index_summary(index: dict | None) -> dict | None:
    if index is None:
        return None
    manifest = index["manifest"]
    return {
        "snapshot_id": index["snapshot_id"],
        "document_count": manifest["document_count"],
        "chunk_count": manifest["chunk_count"],
        "embedding_model": manifest["embedding_model"],
    }


def current_summary() -> dict | None:
    with INDEX_LOCK:
        return index_summary(current_index())


def _managed_documents(root: str) -> list[dict]:
    """Keep raw file identities available when individual notes need repair."""
    try:
        return load_documents(root)
    except (UnicodeDecodeError, yaml.YAMLError):
        return load_documents(root, tolerate_parse_errors=True)


def _document_entries(index: dict | None) -> list[dict]:
    published = {}
    if index is not None:
        published = {doc["doc_id"]: doc for doc in index["manifest"]["documents"]}
    entries = []
    for doc in _managed_documents(get_settings().raw_dir):
        previous = published.get(doc["doc_id"])
        entries.append({
            "doc_id": doc["doc_id"],
            "title": doc["title"],
            "relative_path": doc["relative_path"],
            "size_bytes": doc["size_bytes"],
            "revision_id": doc["revision_id"],
            "indexed": previous is not None and previous["revision_id"] == doc["revision_id"],
            "parse_error": doc.get("parse_error", ""),
        })
    return sorted(entries, key=lambda entry: entry["relative_path"].casefold())


def list_documents() -> dict:
    with INDEX_LOCK:
        index = current_index()
        return {"documents": _document_entries(index), "index": index_summary(index)}


def _validate_filename(filename: str) -> str:
    filename = unicodedata.normalize("NFC", filename)
    if (
        not filename
        or len(filename) > 240
        or filename != filename.strip()
        or filename.startswith(".")
        or filename.endswith((" ", "."))
        or any(character in filename for character in '/\\:%<>"|?*')
        or any(ord(character) < 32 or ord(character) == 127 for character in filename)
    ):
        raise DocumentError("文件名须为单个 Markdown 文件名，不能包含路径或系统保留字符。")
    path = Path(filename)
    if path.suffix.lower() != ".md" or not path.stem.strip():
        raise DocumentError("当前支持扩展名为 .md 的 Markdown 文件。")
    if filename.split(".", 1)[0].rstrip(" .").upper() in WINDOWS_RESERVED:
        raise DocumentError("该文件名被系统保留，请换一个文件名。")
    return filename


def _atomic_write(path: Path, content: bytes) -> None:
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".import-", suffix=".tmp", delete=False) as file:
            temporary_path = Path(file.name)
            file.write(content)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def recover_document_operations() -> dict:
    """Recover interrupted document deletions and report preserved conflicts."""
    with INDEX_LOCK:
        return recover_deletions()


def _recover_before_mutation() -> None:
    report = recover_document_operations()
    if report["conflicts"]:
        raise DocumentError("存在尚未解决的文档恢复冲突，请先处理后重试。", 409)


def import_document(filename: str, content: str, overwrite: bool = False) -> dict:
    filename = _validate_filename(filename)
    try:
        encoded = content.encode("utf-8")
    except UnicodeEncodeError as error:
        raise DocumentError("文档包含无法编码的字符，请保存为 UTF-8 后重试。") from error
    if not content.strip():
        raise DocumentError("文档内容为空，请选择包含正文的 Markdown 文件。")
    settings = get_settings()
    if len(encoded) > settings.max_document_bytes:
        raise DocumentError(f"文档超过 {settings.max_document_bytes} 字节上限，请拆分后上传。", 413)

    with INDEX_LOCK:
        _recover_before_mutation()
        root = Path(settings.raw_dir)
        root.mkdir(parents=True, exist_ok=True)
        target = root / filename
        resolved_target = target.resolve()
        if target.is_symlink() or resolved_target.parent != root.resolve():
            raise DocumentError("目标文件位置无效，请选择知识目录内的普通 Markdown 文件。")
        target = resolved_target
        filename = target.name
        if target.exists() and not target.is_file():
            raise DocumentError("该名称已被目录占用，请换一个文件名。", 409)
        if target.exists() and not overwrite:
            raise DocumentError("同名文档已存在，请确认覆盖后重试。", 409)
        previous_index = current_index()
        previous_content = target.read_bytes() if target.exists() else None
        _atomic_write(target, encoded)
        try:
            build_index()
        except Exception:
            try:
                active = current_index()
            except Exception as error:
                preserve_uncertain_import(target, previous_content, encoded, previous_index)
                raise DocumentError("暂时无法确认导入是否已发布，已保留修改前后内容，请查看文档恢复提示。", 409) from error
            previous_snapshot = previous_index["snapshot_id"] if previous_index else None
            published = active is not None and active["snapshot_id"] != previous_snapshot and any(
                item["relative_path"] == filename
                and item["revision_id"] == hashlib.sha256(encoded).hexdigest()
                for item in active["manifest"]["documents"]
            )
            if not published:
                if previous_content is None:
                    target.unlink(missing_ok=True)
                else:
                    _atomic_write(target, previous_content)
                raise
        index = current_index()
        entries = _document_entries(index)
        document = next(entry for entry in entries if entry["relative_path"] == filename)
        return {"document": document, "index": index_summary(index)}


def delete_document(doc_id: str, expected_revision_id: str) -> dict:
    """Remove the selected revision, restoring its file if rebuilding fails."""
    if not isinstance(doc_id, str) or not DOCUMENT_ID.fullmatch(doc_id):
        raise DocumentError("文档 ID 无效，请刷新文档列表。")
    if not isinstance(expected_revision_id, str) or not REVISION_ID.fullmatch(expected_revision_id):
        raise DocumentError("文档修订号无效，请刷新文档列表。")
    with INDEX_LOCK:
        _recover_before_mutation()
        root = Path(get_settings().raw_dir).resolve()
        document = next((item for item in _managed_documents(str(root)) if item["doc_id"] == doc_id), None)
        if document is None:
            raise DocumentError("文档已不存在，请刷新文档列表。", 404)
        target = Path(document["path"])
        resolved_target = target.resolve(strict=True)
        if target.is_symlink() or resolved_target != target or not resolved_target.is_relative_to(root):
            raise DocumentError("文档位置无效，请选择知识目录内的普通 Markdown 文件。")
        previous_content = resolved_target.read_bytes()
        if hashlib.sha256(previous_content).hexdigest() != expected_revision_id:
            raise DocumentError("文档已更新，请刷新列表并核对内容后重新删除。", 409)
        index = current_index()
        indexed_ids = {item["doc_id"] for item in index["manifest"]["documents"]} if index else set()
        unindexed_broken_draft = bool(document.get("parse_error")) and doc_id not in indexed_ids
        operation = prepare_delete(
            resolved_target, doc_id, expected_revision_id, previous_content, index,
            rebuild=not unindexed_broken_draft,
        )
        resolved_target.unlink()
        try:
            if not unindexed_broken_draft:
                build_index()
        except Exception:
            recovery = recover_document_operations()
            committed = any(
                item["operation_id"] == operation.name and item["status"] == "committed"
                for item in recovery["recovered"]
            )
            if not committed:
                raise
        else:
            complete_delete(operation)
        return {
            "deleted": {"doc_id": doc_id, "relative_path": document["relative_path"]},
            "index": index_summary(current_index()),
        }


def reindex_documents() -> dict:
    with INDEX_LOCK:
        _recover_before_mutation()
        build_index()
        return {"index": index_summary(current_index())}


def read_source(doc_id: str, snapshot_id: str) -> dict:
    if not DOCUMENT_ID.fullmatch(doc_id) or not SNAPSHOT_ID.fullmatch(snapshot_id):
        raise DocumentError("文档或索引代次 ID 无效，请使用回答中的来源链接。")
    with INDEX_LOCK:
        index = read_index(snapshot_id)
        entry = next((doc for doc in index["manifest"]["documents"] if doc["doc_id"] == doc_id), None)
        if entry is None:
            raise DocumentError("该索引代次中没有此文档，请重新提问或重建索引。", 404)
        snapshot_path = Path(index["path"])
        expected_source = f"sources/{doc_id}.md"
        if entry.get("source_path") != expected_source or snapshot_path.is_symlink():
            raise DocumentError("原文来源位置无效，请重建索引后重试。", 409)
        source = snapshot_path / expected_source
        if source.parent.is_symlink() or source.is_symlink():
            raise DocumentError("原文来源位置无效，请重建索引后重试。", 409)
        resolved_source = source.resolve(strict=True)
        if not resolved_source.is_relative_to(snapshot_path.resolve()):
            raise DocumentError("原文来源位置无效，请重建索引后重试。", 409)
        raw = resolved_source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry["revision_id"]:
            raise DocumentError("原文修订校验失败，请重建索引后重试。", 409)
        return {
            "doc_id": doc_id,
            "title": entry["title"],
            "relative_path": entry["relative_path"],
            "revision_id": entry["revision_id"],
            "snapshot_id": snapshot_id,
            "content": raw.decode("utf-8"),
        }
