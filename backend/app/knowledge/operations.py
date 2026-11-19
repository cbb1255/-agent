"""Persist document backups until an index publication or rollback is complete."""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4
import unicodedata

from app.config import get_settings
from app.rag.indexing import current_index


OPERATION_ID = re.compile(r"[0-9a-f]{32}\Z")


def _operations_root() -> Path:
    index_root = Path(get_settings().index_dir).resolve()
    root = index_root / ".document-operations"
    is_junction = getattr(root, "is_junction", None)
    if root.is_symlink() or (is_junction is not None and is_junction()):
        raise ValueError("文档恢复目录不能是链接目录")
    return root


def _write_file(path: Path, content: bytes) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".operation-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _save_record(path: Path, record: dict) -> None:
    _write_file(path, json.dumps(record, ensure_ascii=False).encode("utf-8"))


def prepare_delete(
    target: Path, doc_id: str, revision_id: str, content: bytes,
    previous_index: dict | None, *, rebuild: bool,
) -> Path:
    """Durably preserve the original and intent before the source is removed."""
    raw_root = Path(get_settings().raw_dir).resolve()
    relative_path = target.relative_to(raw_root).as_posix()
    root = _operations_root()
    root.mkdir(parents=True, exist_ok=True)
    operation = root / uuid4().hex
    operation.mkdir()
    _write_file(operation / "original.bin", content)
    record = {
        "schema_version": 1,
        "operation_id": operation.name,
        "raw_root": str(raw_root),
        "relative_path": relative_path,
        "doc_id": doc_id,
        "revision_id": revision_id,
        "previous_snapshot_id": previous_index["snapshot_id"] if previous_index else None,
        "rebuild": rebuild,
        "status": "prepared",
    }
    _save_record(operation / "intent.json", record)
    return operation


def preserve_uncertain_import(
    target: Path, original: bytes | None, incoming: bytes, previous_index: dict | None,
) -> Path:
    """Keep both versions when an import's publication state cannot be read."""
    raw_root = Path(get_settings().raw_dir).resolve()
    relative_path = target.relative_to(raw_root).as_posix()
    normalized_path = unicodedata.normalize("NFC", relative_path)
    root = _operations_root()
    root.mkdir(parents=True, exist_ok=True)
    operation = root / uuid4().hex
    operation.mkdir()
    if original is not None:
        _write_file(operation / "original.bin", original)
    _write_file(operation / "incoming.bin", incoming)
    record = {
        "schema_version": 1,
        "operation_type": "import",
        "operation_id": operation.name,
        "raw_root": str(raw_root),
        "relative_path": relative_path,
        "doc_id": "doc_" + sha256(normalized_path.encode("utf-8")).hexdigest()[:24],
        "revision_id": sha256(incoming).hexdigest(),
        "previous_revision_id": sha256(original).hexdigest() if original is not None else None,
        "had_original": original is not None,
        "previous_snapshot_id": previous_index["snapshot_id"] if previous_index else None,
        "rebuild": True,
        "status": "prepared",
    }
    _save_record(operation / "intent.json", record)
    return operation


def _load_record(operation: Path) -> tuple[dict, Path]:
    root = _operations_root()
    if not OPERATION_ID.fullmatch(operation.name) or operation.parent != root:
        raise ValueError("文档恢复任务编号无效")
    if operation.is_symlink() or operation.resolve() != operation:
        raise ValueError("文档恢复任务不能是链接目录")
    intent = operation / "intent.json"
    backup = operation / "original.bin"
    incoming = operation / "incoming.bin"
    if intent.is_symlink() or backup.is_symlink() or incoming.is_symlink():
        raise ValueError("文档恢复文件不能是链接文件")
    record = json.loads(intent.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or record.get("operation_id") != operation.name:
        raise ValueError("文档恢复任务身份不一致")
    if record.get("schema_version") != 1 or record.get("status") not in ("prepared", "committed", "rolled_back"):
        raise ValueError("文档恢复任务状态无效")
    raw_root = Path(get_settings().raw_dir).resolve()
    if record.get("raw_root") != str(raw_root):
        raise ValueError("文档恢复任务的知识目录与当前配置不一致")
    relative = record.get("relative_path")
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("文档恢复任务的相对路径无效")
    target = raw_root / relative
    if Path(relative).is_absolute() or target.resolve() != target or not target.is_relative_to(raw_root):
        raise ValueError("文档恢复目标越界或包含链接")
    if not re.fullmatch(r"doc_[0-9a-f]{24}", str(record.get("doc_id", ""))):
        raise ValueError("文档恢复任务的文档编号无效")
    normalized_path = unicodedata.normalize("NFC", relative)
    expected_doc_id = "doc_" + sha256(normalized_path.encode("utf-8")).hexdigest()[:24]
    if record["doc_id"] != expected_doc_id:
        raise ValueError("文档恢复任务的路径与文档编号不一致")
    if not re.fullmatch(r"[0-9a-f]{64}", str(record.get("revision_id", ""))):
        raise ValueError("文档恢复任务的修订号无效")
    if type(record.get("rebuild")) is not bool:
        raise ValueError("文档恢复任务的索引策略无效")
    previous = record.get("previous_snapshot_id")
    if previous is not None and not OPERATION_ID.fullmatch(str(previous)):
        raise ValueError("文档恢复任务的原索引代次无效")
    operation_type = record.get("operation_type", "delete")
    if operation_type not in ("delete", "import"):
        raise ValueError("文档恢复任务的操作类型无效")
    if operation_type == "import":
        if type(record.get("had_original")) is not bool:
            raise ValueError("导入恢复任务的原文状态无效")
        previous_revision = record.get("previous_revision_id")
        if record["had_original"]:
            if not isinstance(previous_revision, str) or not re.fullmatch(r"[0-9a-f]{64}", previous_revision):
                raise ValueError("导入恢复任务的原修订号无效")
        elif previous_revision is not None:
            raise ValueError("新增导入的原修订号须为空")
    return record, target


def _finish(operation: Path, record: dict, status: str) -> None:
    record = {**record, "status": status}
    _save_record(operation / "intent.json", record)
    (operation / "original.bin").unlink(missing_ok=True)
    (operation / "incoming.bin").unlink(missing_ok=True)
    # An interrupted atomic record write can leave a private temporary file.
    for temporary in operation.glob(".operation-*"):
        if temporary.is_file() and not temporary.is_symlink():
            temporary.unlink()
    (operation / "intent.json").unlink()
    operation.rmdir()


def complete_delete(operation: Path) -> None:
    """The caller has published a complete index or removed an unindexed draft."""
    record, target = _load_record(operation)
    if target.exists():
        raise ValueError("删除完成前目标文件重新出现，请核对文档恢复任务")
    _finish(operation, record, "committed")


def _recover_import(operation: Path, record: dict, target: Path) -> str:
    incoming = (operation / "incoming.bin").read_bytes()
    if sha256(incoming).hexdigest() != record["revision_id"]:
        raise ValueError("导入恢复的新正文备份校验失败")
    original = None
    if record["had_original"]:
        original = (operation / "original.bin").read_bytes()
        if sha256(original).hexdigest() != record["previous_revision_id"]:
            raise ValueError("导入恢复的原文备份校验失败")
    index = current_index()
    snapshot = index["snapshot_id"] if index else None
    indexed = next((
        item for item in index["manifest"]["documents"]
        if item["doc_id"] == record["doc_id"]
    ), None) if index else None
    generation_changed = snapshot != record["previous_snapshot_id"]
    committed = generation_changed and indexed is not None and indexed.get("revision_id") == record["revision_id"]
    existing = target.read_bytes() if target.is_file() else None
    if target.exists() and not target.is_file():
        raise ValueError("导入恢复目标已被其他对象占用，请先处理冲突")
    if committed:
        if existing is not None and existing != incoming:
            raise ValueError("已发布导入的同路径文件出现不同内容，已保留修改前后备份，请先处理冲突")
        if existing is None:
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_file(target, incoming)
        return "committed"
    if generation_changed:
        raise ValueError("当前索引代次已改变且未确认本次导入，已保留修改前后备份，请先处理冲突")
    if existing is not None and existing != incoming and existing != original:
        raise ValueError("未决导入的同路径文件出现不同内容，已保留修改前后备份，请先处理冲突")
    if original is None:
        if existing is not None:
            target.unlink()
    elif existing != original:
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_file(target, original)
    return "rolled_back"


def recover_deletions() -> dict:
    """Recover pending document operations while preserving new source changes.

    Call under INDEX_LOCK. A published generation that excludes the document
    is the commit point. Before publication, restore the backed-up bytes.
    Conflicts retain their records and backups for an explicit user decision.
    """
    report = {"recovered": [], "conflicts": []}
    try:
        root = _operations_root()
    except (ValueError, OSError) as error:
        report["conflicts"].append({
            "operation_id": "", "status": "conflict", "relative_path": "",
            "message": str(error),
        })
        return report
    if not root.exists():
        return report
    try:
        operations = sorted(root.iterdir())
    except OSError as error:
        report["conflicts"].append({
            "operation_id": "", "status": "conflict", "relative_path": "",
            "message": str(error),
        })
        return report
    for operation in operations:
        result = {
            "operation_id": operation.name,
            "relative_path": "",
            "status": "conflict",
            "message": "",
        }
        try:
            if not operation.is_dir() or operation.is_symlink() or not OPERATION_ID.fullmatch(operation.name):
                raise ValueError("文档恢复目录中存在无法识别的任务")
            # The source cannot be removed until intent.json has been published.
            if not (operation / "intent.json").exists():
                if any(operation.iterdir()):
                    raise ValueError("恢复备份缺少完整操作记录，已保留备份，请按任务编号人工核对")
                operation.rmdir()
                continue
            record, target = _load_record(operation)
            result["relative_path"] = record["relative_path"]
            if record["status"] != "prepared":
                status = record["status"]
                _finish(operation, record, status)
            elif record.get("operation_type") == "import":
                status = _recover_import(operation, record, target)
                _finish(operation, record, status)
            else:
                backup = (operation / "original.bin").read_bytes()
                if sha256(backup).hexdigest() != record["revision_id"]:
                    raise ValueError("文档恢复原文备份校验失败")
                index = current_index()
                indexed = next((
                    item for item in index["manifest"]["documents"]
                    if item["doc_id"] == record["doc_id"]
                ), None) if index else None
                changed_generation = index is not None and index["snapshot_id"] != record["previous_snapshot_id"]
                committed = indexed is None and (
                    changed_generation or not record["rebuild"]
                )
                if target.exists():
                    if not target.is_file() or target.read_bytes() != backup:
                        raise ValueError("恢复目标已有不同内容，已保留新文件与原文备份，请先处理冲突")
                    if committed and changed_generation:
                        raise ValueError("已发布的删除任务出现同路径文件，已保留文件与备份，请先处理冲突")
                    status = "rolled_back"
                elif committed:
                    status = "committed"
                else:
                    if changed_generation and indexed is not None and indexed.get("revision_id") != record["revision_id"]:
                        raise ValueError("当前索引记录了另一修订，已保留原文备份，请先处理冲突")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _write_file(target, backup)
                    status = "rolled_back"
                _finish(operation, record, status)
            result["status"] = status
            if record.get("operation_type") == "import":
                result["message"] = "已完成未决导入的状态登记。" if status == "committed" else "已恢复未决导入前的原文状态。"
            else:
                result["message"] = "已完成中断删除的状态登记。" if status == "committed" else "已恢复中断删除的原文件。"
            report["recovered"].append(result)
        except (ValueError, OSError, KeyError, TypeError) as error:
            result["message"] = str(error)
            report["conflicts"].append(result)
    return report
