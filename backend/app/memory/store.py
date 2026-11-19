"""User/project-scoped SQLite memories with atomic optimistic updates."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from threading import RLock

from app.contracts import MemoryEntry, SourceReference
from app.graph.grounding import nonblank, positive_integer
from app.learning.review import utc_datetime
from app.persistence import model_from_json, model_json, validate_reference

STATUS = 'implemented'


class MemoryConflict(ValueError):
    """A stale version, duplicate identity, or unresolved memory decision."""


def validate_memory(entry):
    if not isinstance(entry, MemoryEntry):
        raise ValueError("entry 必须是MemoryEntry")
    for field in ('entry_id', 'user_id', 'content'):
        nonblank(getattr(entry, field), field)
    if entry.kind not in ('preference', 'project_context', 'confirmed_conclusion'):
        raise ValueError("记忆类型无效")
    if entry.status not in ('candidate', 'confirmed', 'rejected'):
        raise ValueError("记忆状态无效")
    if entry.provenance not in ('user_explicit', 'user_confirmed', 'model_inferred'):
        raise ValueError("记忆来源无效")
    if entry.project_id is not None:
        nonblank(entry.project_id, 'project_id')
    if entry.kind == 'project_context' and entry.project_id is None:
        raise ValueError("项目背景记忆需要project_id")
    if entry.source_session_id is not None:
        nonblank(entry.source_session_id, 'source_session_id')
    created, updated = utc_datetime(entry.created_at), utc_datetime(entry.updated_at)
    if updated < created:
        raise ValueError("更新时间早于创建时间")
    if entry.expires_at is not None and utc_datetime(entry.expires_at) <= created:
        raise ValueError("有效期必须晚于创建时间")
    confidence = entry.confidence
    if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1):
        raise ValueError("置信度须为0到1有限数字或None")
    positive_integer(entry.version, 'version')
    if not isinstance(entry.source_refs, tuple) or any(not isinstance(ref, SourceReference) for ref in entry.source_refs):
        raise ValueError("source_refs 须为SourceReference元组")
    for reference in entry.source_refs:
        validate_reference(reference)
    model_json(entry)


class MemoryStore:
    def __init__(self, path: str | Path = ':memory:', *, clock=None):
        if str(path) != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.connection = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.execute('CREATE TABLE IF NOT EXISTS memories (entry_id TEXT NOT NULL, user_id TEXT NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY(user_id,entry_id))')
        self.connection.execute('CREATE INDEX IF NOT EXISTS memory_user ON memories(user_id)')
        self.connection.commit()

    @contextmanager
    def transaction(self):
        with self.lock:
            nested = self.connection.in_transaction
            if not nested:
                self.connection.execute('BEGIN IMMEDIATE')
            try:
                yield
                if not nested:
                    self.connection.commit()
            except BaseException:
                if not nested:
                    self.connection.rollback()
                raise

    def close(self):
        with self.lock:
            self.connection.close()

    def get(self, user_id: str, entry_id: str) -> MemoryEntry | None:
        nonblank(user_id, 'user_id')
        nonblank(entry_id, 'entry_id')
        with self.lock:
            row = self.connection.execute('SELECT data FROM memories WHERE user_id=? AND entry_id=?', (user_id, entry_id)).fetchone()
        if row is None:
            return None
        entry = model_from_json(MemoryEntry, row[0])
        validate_memory(entry)
        return entry

    def list_entries(self, user_id: str, *, project_id: str | None = None, include_inactive: bool = False) -> list[MemoryEntry]:
        nonblank(user_id, 'user_id')
        if project_id is not None:
            nonblank(project_id, 'project_id')
        if type(include_inactive) is not bool:
            raise ValueError("include_inactive 必须是bool")
        now = utc_datetime(self.clock())
        with self.lock:
            rows = self.connection.execute('SELECT data FROM memories WHERE user_id=? ORDER BY entry_id', (user_id,)).fetchall()
        result = []
        for row in rows:
            entry = model_from_json(MemoryEntry, row[0])
            validate_memory(entry)
            if entry.project_id is not None and entry.project_id != project_id:
                continue
            if not include_inactive and (entry.status == 'rejected' or (entry.expires_at is not None and utc_datetime(entry.expires_at) <= now)):
                continue
            result.append(entry)
        return result

    def save(self, entry: MemoryEntry, *, expected_version: int | None = None) -> MemoryEntry:
        validate_memory(entry)
        if expected_version is not None:
            positive_integer(expected_version, 'expected_version')
            if entry.version != expected_version + 1:
                raise MemoryConflict("更新后的记忆版本须递增一次")
        elif entry.version != 1:
            raise MemoryConflict("新记忆版本须为1")
        with self.transaction():
            if expected_version is None:
                try:
                    self.connection.execute('INSERT INTO memories VALUES (?,?,?,?)', (entry.entry_id, entry.user_id, entry.version, model_json(entry)))
                except sqlite3.IntegrityError as error:
                    raise MemoryConflict("记忆编号已存在") from error
            else:
                current = self.get(entry.user_id, entry.entry_id)
                if current is not None and (current.created_at != entry.created_at or current.updated_at > entry.updated_at):
                    raise MemoryConflict("记忆创建时间变化或更新时间回退")
                cursor = self.connection.execute('UPDATE memories SET version=?, data=? WHERE entry_id=? AND user_id=? AND version=?',
                    (entry.version, model_json(entry), entry.entry_id, entry.user_id, expected_version))
                if cursor.rowcount != 1:
                    raise MemoryConflict("记忆版本已变化或记录不存在")
        return model_from_json(MemoryEntry, model_json(entry))

    def delete(self, user_id: str, entry_id: str, *, expected_version: int) -> None:
        nonblank(user_id, 'user_id')
        nonblank(entry_id, 'entry_id')
        positive_integer(expected_version, 'expected_version')
        with self.transaction():
            cursor = self.connection.execute('DELETE FROM memories WHERE user_id=? AND entry_id=? AND version=?', (user_id, entry_id, expected_version))
            if cursor.rowcount != 1:
                raise MemoryConflict("记忆版本已变化或记录不存在")
