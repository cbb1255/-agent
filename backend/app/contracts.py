"""Shared data contracts for ingestion, memory, and generated outputs.

These standard-library data types describe the service boundaries. The
Markdown RAG path keeps its existing dictionaries. Creating a contract value
does not run a parser, persist a memory, or generate an artifact.

Paths are relative to the owning storage root. Timestamps must be timezone-aware
UTC values. Metadata must remain JSON-serializable when persisted. Page, slide,
and paragraph numbers are one-based. Text offsets use Python character offsets
with an exclusive end, and media offsets use seconds.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Mapping


STATUS: Literal["implemented"] = "implemented"
CONTRACT_VERSION = 1

ArtifactKind = Literal[
    "summary",
    "knowledge_card",
    "quiz",
    "learning_outline",
    "comparison_table",
    "glossary",
    "weekly_report",
    "project_brief",
]
MemoryKind = Literal["preference", "project_context", "confirmed_conclusion"]
MemoryStatus = Literal["candidate", "confirmed", "rejected"]
MemoryProvenance = Literal["user_explicit", "user_confirmed", "model_inferred"]
TaskStatus = Literal["queued", "running", "needs_review", "succeeded", "failed"]


@dataclass(frozen=True)
class SourceLocator:
    """Location in a preserved original, with optional extraction confidence."""

    relative_path: str
    page: int | None = None
    slide: int | None = None
    paragraph: int | None = None
    start_seconds: float | None = None
    end_seconds: float | None = None
    confidence: float | None = None


@dataclass(frozen=True)
class SourceSpan:
    """Map a range of parsed or normalized text back to an original location."""

    text_start: int
    text_end: int
    locator: SourceLocator


@dataclass(frozen=True)
class SourceReference:
    """Pin evidence to a document revision and an immutable index snapshot."""

    doc_id: str
    revision_id: str
    snapshot_id: str
    locator: SourceLocator
    chunk_id: str | None = None


@dataclass(frozen=True)
class ParsedDocument:
    """Parser output before cleaning, deduplication, and review."""

    doc_id: str
    source_relative_path: str
    media_type: str
    raw_hash: str
    text: str
    parser_name: str
    parser_version: str
    source_spans: tuple[SourceSpan, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class GovernedDocument:
    """Versioned text with provenance and a review decision for future indexing.

    A quality score is optional until a scorer exists. A duplicate candidate is
    recorded for review through duplicate_of_revision_id. Adapters must preserve
    source spans when cleaning changes text offsets.
    """

    doc_id: str
    revision_id: str
    raw_hash: str
    normalized_hash: str
    text: str
    governance_version: str
    parser_name: str
    parser_version: str
    source_spans: tuple[SourceSpan, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    quality_score: float | None = None
    quality_reasons: tuple[str, ...] = ()
    review_status: Literal["needs_review", "approved", "rejected"] = "needs_review"
    duplicate_of_revision_id: str | None = None


@dataclass(frozen=True)
class ProcessingTask:
    """Durable ingestion task identity and checkpoint, with optimistic versioning."""

    task_id: str
    source_relative_path: str
    raw_hash: str
    pipeline_version: str
    idempotency_key: str
    stage: str
    status: TaskStatus
    attempts: int = 0
    last_error: str | None = None
    version: int = 1


@dataclass(frozen=True)
class KnowledgeSnapshot:
    """Future catalog record linking document revisions to an index generation."""

    snapshot_id: str
    index_snapshot_id: str
    document_revisions: Mapping[str, str]
    pipeline_versions: Mapping[str, str]
    created_at: datetime


@dataclass(frozen=True)
class MemoryEntry:
    """User-scoped memory. Model-inferred content starts as a candidate.

    Confirmation is represented separately from confidence. Project context
    carries project_id, and source_refs retain the supporting document revisions
    or artifact evidence. A store must validate the scope on every operation.
    """

    entry_id: str
    user_id: str
    kind: MemoryKind
    content: str
    status: MemoryStatus
    provenance: MemoryProvenance
    created_at: datetime
    updated_at: datetime
    project_id: str | None = None
    source_session_id: str | None = None
    source_refs: tuple[SourceReference, ...] = ()
    confidence: float | None = None
    expires_at: datetime | None = None
    version: int = 1


@dataclass(frozen=True)
class MemoryContext:
    """Selected confirmed memories within the caller's memory token budget."""

    entries: tuple[MemoryEntry, ...]
    token_budget: int
    used_tokens: int
    conflict_entry_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class GeneratedArtifact:
    """Generated Markdown and the frozen revisions needed for reproduction."""

    artifact_id: str
    kind: ArtifactKind
    title: str
    content: str
    input_revisions: Mapping[str, str]
    source_refs: tuple[SourceReference, ...]
    template_version: str
    model_id: str
    created_at: datetime
    metadata: Mapping[str, object] = field(default_factory=dict)
