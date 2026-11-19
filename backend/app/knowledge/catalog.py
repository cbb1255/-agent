"""SQLite catalog for fixed revisions, durable tasks, and knowledge snapshots.

The future task runner owns leases, stage checkpoints, bounded retries, and
idempotency. The catalog records publication only after a complete index exists.
Index pointer publication remains coordinated with app.rag.indexing. Importing
this module creates no database, task queue, or background process.
"""

from hashlib import sha256
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Mapping

from app.contracts import (
    GovernedDocument,
    KnowledgeSnapshot,
    ProcessingTask,
)
from app.graph.grounding import nonblank, positive_integer
from app.learning.review import utc_datetime
from app.persistence import model_from_json, model_json, validate_spans


STATUS = 'implemented'


class CatalogConflict(ValueError):
    """An immutable record or optimistic checkpoint conflicts with persisted data."""


def _complete_index(snapshot_id):
    from app.rag.indexing import read_index
    index = read_index(snapshot_id)
    root, manifest = index['path'], index['manifest']
    if not (root / 'bm25.pkl').is_file() or not (root / 'chroma').is_dir():
        raise ValueError("索引代次缺少关键词或向量索引")
    chunks = manifest.get('chunk_count')
    if type(chunks) is not int or chunks < 0:
        raise ValueError("索引分块数量无效")
    if chunks and not (root / 'chroma/chroma.sqlite3').is_file():
        raise ValueError("向量索引尚未完成写入")
    documents = manifest.get('documents')
    if not isinstance(documents, list) or manifest.get('document_count') != len(documents):
        raise ValueError("索引文档清单无效")
    for document in documents:
        if not isinstance(document, dict):
            raise ValueError("索引文档记录无效")
        source_path = document.get('source_path')
        nonblank(source_path, 'source_path')
        source = root / source_path
        if source.is_symlink() or not source.resolve().is_relative_to(root.resolve()) or not source.is_file():
            raise ValueError("索引原件副本缺失或路径无效")
    return index


def _revision(document):
    if not isinstance(document, GovernedDocument):
        raise ValueError("document 必须是GovernedDocument")
    for name in ('doc_id', 'revision_id', 'raw_hash', 'normalized_hash', 'governance_version', 'parser_name', 'parser_version'):
        nonblank(getattr(document, name), name)
    if not isinstance(document.text, str) or document.normalized_hash != sha256(document.text.encode('utf-8')).hexdigest():
        raise ValueError("规范正文哈希不一致")
    if document.review_status not in ('needs_review', 'approved', 'rejected'):
        raise ValueError("审核状态无效")
    if not isinstance(document.metadata, Mapping):
        raise ValueError("metadata 必须是映射")
    validate_spans(document.text, document.source_spans)
    if not isinstance(document.tags, tuple) or any(not isinstance(tag, str) or not tag.strip() for tag in document.tags):
        raise ValueError("tags 必须是非空字符串元组")
    if document.quality_score is not None and (isinstance(document.quality_score, bool) or not isinstance(document.quality_score, (int, float)) or not 0 <= document.quality_score <= 100):
        raise ValueError("质量分无效")
    if not isinstance(document.quality_reasons, tuple) or any(not isinstance(reason, str) or not reason.strip() for reason in document.quality_reasons):
        raise ValueError("质量原因须为非空字符串元组")
    if document.duplicate_of_revision_id is not None:
        nonblank(document.duplicate_of_revision_id, 'duplicate_of_revision_id')
    model_json(document)


def _task(task):
    if not isinstance(task, ProcessingTask):
        raise ValueError("task 必须是ProcessingTask")
    for name in ('task_id', 'source_relative_path', 'raw_hash', 'pipeline_version', 'idempotency_key', 'stage'):
        nonblank(getattr(task, name), name)
    if task.status not in ('queued', 'running', 'needs_review', 'succeeded', 'failed'):
        raise ValueError("任务状态无效")
    if type(task.attempts) is not int or task.attempts < 0:
        raise ValueError("attempts 必须是非负整数")
    positive_integer(task.version, 'version')
    if task.last_error is not None and not isinstance(task.last_error, str):
        raise ValueError("last_error 必须是字符串或None")


class KnowledgeCatalog:
    """SQLite revision, task and index-snapshot catalog."""

    def __init__(self, path: str | Path = ':memory:', *, index_reader=None):
        if str(path) != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.executescript('''
            CREATE TABLE IF NOT EXISTS revisions (doc_id TEXT, revision_id TEXT, data TEXT NOT NULL, PRIMARY KEY(doc_id,revision_id));
            CREATE TABLE IF NOT EXISTS revision_requests (request_key TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, revision_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS processing_tasks (task_id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS knowledge_snapshots (snapshot_id TEXT PRIMARY KEY, data TEXT NOT NULL);
        ''')
        self.connection.commit()
        self.lock = RLock()
        if index_reader is None:
            index_reader = _complete_index
        self.index_reader = index_reader

    def close(self):
        with self.lock:
            self.connection.close()

    def save_revision(
        self, document: GovernedDocument, *, idempotency_key: str
    ) -> str:
        """Return the existing or newly saved revision ID for the same key."""
        _revision(document)
        nonblank(idempotency_key, 'idempotency_key')
        payload = model_json(document)
        digest = sha256(payload.encode('utf-8')).hexdigest()
        with self.lock, self.connection:
            self.connection.execute('BEGIN IMMEDIATE')
            request = self.connection.execute('SELECT payload_hash, revision_id FROM revision_requests WHERE request_key=?', (idempotency_key,)).fetchone()
            if request is not None:
                if request[0] != digest:
                    raise CatalogConflict("同一幂等键对应不同修订内容")
                return request[1]
            previous = self.connection.execute('SELECT data FROM revisions WHERE doc_id=? AND revision_id=?', (document.doc_id, document.revision_id)).fetchone()
            if previous is not None and previous[0] != payload:
                raise CatalogConflict("固定修订的内容发生冲突")
            self.connection.execute('INSERT OR IGNORE INTO revisions VALUES (?,?,?)', (document.doc_id, document.revision_id, payload))
            self.connection.execute('INSERT INTO revision_requests VALUES (?,?,?)', (idempotency_key, digest, document.revision_id))
        return document.revision_id

    def get_revision(
        self, doc_id: str, revision_id: str
    ) -> GovernedDocument | None:
        """Load a fixed revision, validating its document identity and body hash."""
        nonblank(doc_id, 'doc_id')
        nonblank(revision_id, 'revision_id')
        with self.lock:
            row = self.connection.execute('SELECT data FROM revisions WHERE doc_id=? AND revision_id=?', (doc_id, revision_id)).fetchone()
        if row is None:
            return None
        document = model_from_json(GovernedDocument, row[0])
        _revision(document)
        if (document.doc_id, document.revision_id) != (doc_id, revision_id):
            raise ValueError("目录身份校验失败")
        return document

    def submit_task(self, task: ProcessingTask) -> ProcessingTask:
        """Create a task once, or return the task with its existing unique key."""
        _task(task)
        if task.version != 1:
            raise ValueError("新任务版本必须为1")
        with self.lock, self.connection:
            self.connection.execute('BEGIN IMMEDIATE')
            row = self.connection.execute('SELECT data FROM processing_tasks WHERE request_key=?', (task.idempotency_key,)).fetchone()
            if row is not None:
                existing = model_from_json(ProcessingTask, row[0])
                if (existing.source_relative_path, existing.raw_hash, existing.pipeline_version) != (task.source_relative_path, task.raw_hash, task.pipeline_version):
                    raise CatalogConflict("任务幂等键的原件或流水线版本冲突")
                return existing
            try:
                self.connection.execute('INSERT INTO processing_tasks VALUES (?,?,?,?)', (task.task_id, task.idempotency_key, task.version, model_json(task)))
            except sqlite3.IntegrityError as error:
                raise CatalogConflict("任务编号已存在") from error
        return model_from_json(ProcessingTask, model_json(task))

    def get_task(self, task_id: str) -> ProcessingTask | None:
        """Return the persisted checkpoint when the task exists."""
        nonblank(task_id, 'task_id')
        with self.lock:
            row = self.connection.execute('SELECT data FROM processing_tasks WHERE task_id=?', (task_id,)).fetchone()
        if row is None:
            return None
        result = model_from_json(ProcessingTask, row[0])
        _task(result)
        if result.task_id != task_id:
            raise ValueError("任务身份不一致")
        return result

    def save_task(
        self, task: ProcessingTask, *, expected_version: int
    ) -> ProcessingTask:
        """Advance a checkpoint atomically, raising on a stale expected version."""
        _task(task)
        positive_integer(expected_version, 'expected_version')
        if task.version != expected_version + 1:
            raise CatalogConflict("任务版本须递增一次")
        with self.lock, self.connection:
            self.connection.execute('BEGIN IMMEDIATE')
            existing = self.get_task(task.task_id)
            if existing is None or existing.version != expected_version:
                raise CatalogConflict("任务检查点已变化")
            identity = ('idempotency_key', 'source_relative_path', 'raw_hash', 'pipeline_version')
            if any(getattr(existing, name) != getattr(task, name) for name in identity) or task.attempts < existing.attempts:
                raise CatalogConflict("任务原件身份变化或重试次数回退")
            self.connection.execute('UPDATE processing_tasks SET data=?, version=? WHERE task_id=? AND version=?', (model_json(task), task.version, task.task_id, expected_version))
        return model_from_json(ProcessingTask, model_json(task))

    def record_snapshot(self, snapshot: KnowledgeSnapshot) -> None:
        """Record a complete immutable revision/index mapping idempotently.

        A repeated snapshot ID with different content must raise a conflict.
        This operation alone does not publish the current index pointer.
        """
        if not isinstance(snapshot, KnowledgeSnapshot):
            raise ValueError("snapshot 必须是KnowledgeSnapshot")
        nonblank(snapshot.snapshot_id, 'snapshot_id')
        nonblank(snapshot.index_snapshot_id, 'index_snapshot_id')
        utc_datetime(snapshot.created_at)
        for mapping in (snapshot.document_revisions, snapshot.pipeline_versions):
            if not isinstance(mapping, Mapping):
                raise ValueError("快照版本映射无效")
            for key, value in mapping.items():
                nonblank(key, '版本键')
                nonblank(value, '版本值')
        index = self.index_reader(snapshot.index_snapshot_id)
        if not isinstance(index, dict) or index.get('snapshot_id') != snapshot.index_snapshot_id:
            raise ValueError("索引快照不存在或身份不一致")
        manifest = index.get('manifest')
        if not isinstance(manifest, dict) or not isinstance(manifest.get('documents'), list):
            raise ValueError("索引文档清单无效")
        indexed = {}
        for item in manifest['documents']:
            if not isinstance(item, dict):
                raise ValueError("索引文档记录无效")
            doc_id = nonblank(item.get('doc_id'), 'doc_id')
            revision_id = nonblank(item.get('revision_id'), 'revision_id')
            if doc_id in indexed:
                raise ValueError("索引文档身份重复")
            indexed[doc_id] = revision_id
        if indexed != dict(snapshot.document_revisions):
            raise ValueError("快照修订映射与完整索引不一致")
        payload = model_json(snapshot)
        with self.lock, self.connection:
            self.connection.execute('BEGIN IMMEDIATE')
            if any(self.get_revision(doc_id, revision_id) is None for doc_id, revision_id in snapshot.document_revisions.items()):
                raise ValueError("快照引用未保存修订")
            previous = self.connection.execute('SELECT data FROM knowledge_snapshots WHERE snapshot_id=?', (snapshot.snapshot_id,)).fetchone()
            if previous is not None and previous[0] != payload:
                raise CatalogConflict("同一快照编号对应不同内容")
            self.connection.execute('INSERT OR IGNORE INTO knowledge_snapshots VALUES (?,?)', (snapshot.snapshot_id, payload))

    def get_snapshot(self, snapshot_id: str) -> KnowledgeSnapshot | None:
        """Read a fixed document-to-revision mapping for evidence or generation."""
        nonblank(snapshot_id, 'snapshot_id')
        with self.lock:
            row = self.connection.execute('SELECT data FROM knowledge_snapshots WHERE snapshot_id=?', (snapshot_id,)).fetchone()
        if row is None:
            return None
        result = model_from_json(KnowledgeSnapshot, row[0])
        if result.snapshot_id != snapshot_id:
            raise ValueError("快照身份不一致")
        utc_datetime(result.created_at)
        return result
