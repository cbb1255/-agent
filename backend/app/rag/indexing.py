"""Read immutable index generations and atomically publish their current pointer."""
from contextlib import suppress
import json
import os
from pathlib import Path
import re
from threading import RLock
from uuid import uuid4

from app.config import get_settings


INDEX_LOCK = RLock()


def read_index(snapshot_id: str) -> dict:
    """Resolve a server-generated generation ID inside the configured index root."""
    if not isinstance(snapshot_id, str) or not re.fullmatch(r"[0-9a-f]{32}", snapshot_id):
        raise ValueError("无效的索引代次 ID")
    root = Path(get_settings().index_dir).resolve()
    lexical_path = root / snapshot_id
    is_junction = getattr(lexical_path, "is_junction", None)
    if lexical_path.is_symlink() or (is_junction is not None and is_junction()):
        raise ValueError("索引代次不能是链接目录")
    path = lexical_path.resolve()
    if not path.is_relative_to(root):
        raise ValueError("索引代次路径越界")
    manifest_path = path / "manifest.json"
    if manifest_path.is_symlink():
        raise ValueError("索引清单不能是链接文件")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("snapshot_id") != snapshot_id:
        raise ValueError("索引清单与代次 ID 不一致")
    return {"snapshot_id": snapshot_id, "path": path, "manifest": manifest}


def current_index() -> dict | None:
    """A missing current pointer means no index has been published yet."""
    pointer = Path(get_settings().index_dir) / "current.json"
    try:
        payload = json.loads(pointer.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(payload, dict):
        raise ValueError("当前索引指针格式无效")
    return read_index(payload.get("snapshot_id"))


def publish_index(snapshot_id: str) -> None:
    """Publish after all generation files have been written, under INDEX_LOCK."""
    index = read_index(snapshot_id)
    root = index["path"].parent
    temporary_pointer = root / f".current-{uuid4().hex}.tmp"
    try:
        with temporary_pointer.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump({"snapshot_id": snapshot_id}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_pointer, root / "current.json")
    finally:
        with suppress(OSError):
            temporary_pointer.unlink(missing_ok=True)
