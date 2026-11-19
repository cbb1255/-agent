"""Atomic Markdown and Chinese PDF exports of source-bound artifacts."""
import base64
import json
from pathlib import Path
from typing import Literal, Mapping, get_args

from app.contracts import ArtifactKind, GeneratedArtifact
from app.graph.citations import extract_source_numbers
from app.graph.grounding import nonblank
from app.learning.review import utc_datetime
from app.media_runtime import run_media
from app.persistence import atomic_write, model_json, validate_reference


STATUS = "implemented"
ExportFormat = Literal["markdown", "pdf"]


def validate_artifact(artifact):
    """Validate a fixed artifact and its complete document/reference binding."""
    if not isinstance(artifact, GeneratedArtifact):
        raise ValueError("artifact须为GeneratedArtifact")
    for name in ('artifact_id', 'title', 'content', 'template_version', 'model_id'):
        nonblank(getattr(artifact, name), name)
    if artifact.kind not in get_args(ArtifactKind):
        raise ValueError("不支持的产物类型")
    utc_datetime(artifact.created_at)
    if not isinstance(artifact.input_revisions, Mapping) or not artifact.input_revisions:
        raise ValueError("产物须绑定输入修订")
    for doc_id, revision_id in artifact.input_revisions.items():
        nonblank(doc_id, 'doc_id')
        nonblank(revision_id, 'revision_id')
    if not isinstance(artifact.source_refs, tuple) or not artifact.source_refs:
        raise ValueError("产物须保留来源引用")
    referenced = set()
    for reference in artifact.source_refs:
        validate_reference(reference)
        if artifact.input_revisions.get(reference.doc_id) != reference.revision_id:
            raise ValueError("产物来源与输入修订不一致")
        referenced.add(reference.doc_id)
    if referenced != set(artifact.input_revisions):
        raise ValueError("存在缺少来源引用的输入修订")
    if not isinstance(artifact.metadata, Mapping):
        raise ValueError("产物metadata须为映射")
    manifest = artifact.metadata.get('source_manifest')
    if manifest is not None:
        if not isinstance(manifest, list) or len(manifest) != len(referenced):
            raise ValueError("产物来源清单无效")
        seen = set()
        for index, item in enumerate(manifest, 1):
            if not isinstance(item, dict) or type(item.get('source_index')) is not int or item['source_index'] != index:
                raise ValueError("来源编号须按清单连续排列")
            doc_id = item.get('doc_id')
            if not isinstance(doc_id, str) or doc_id in seen or artifact.input_revisions.get(doc_id) != item.get('revision_id'):
                raise ValueError("来源清单修订无效")
            seen.add(doc_id)
            refs = [ref for ref in artifact.source_refs if ref.doc_id == doc_id]
            if any(ref.snapshot_id != item.get('snapshot_id') or ref.locator.relative_path != item.get('source_relative_path') for ref in refs):
                raise ValueError("来源清单快照或路径不一致")
    citations = extract_source_numbers(artifact.content)
    if not citations or not citations.issubset(set(range(1, len(referenced) + 1))):
        raise ValueError("产物正文引用缺失或越界")
    model_json(artifact)


def _export_content(artifact):
    references = []
    manifest = artifact.metadata.get('source_manifest')
    doc_ids = [item['doc_id'] for item in manifest] if manifest is not None else list(dict.fromkeys(ref.doc_id for ref in artifact.source_refs))
    for index, doc_id in enumerate(doc_ids, 1):
        refs = [ref for ref in artifact.source_refs if ref.doc_id == doc_id]
        for reference in refs:
            locator = reference.locator
            locations = [f"{field} {getattr(locator, field)}" for field in ('page', 'slide', 'paragraph')
                         if getattr(locator, field) is not None]
            if locator.start_seconds is not None:
                locations.append(f"{locator.start_seconds:g}–{locator.end_seconds:g}秒")
            references.append(f"- [{index}] {locator.relative_path} {' '.join(locations)}\n"
                              f"  doc_id={reference.doc_id} revision_id={reference.revision_id} snapshot_id={reference.snapshot_id}")
    content = artifact.content.rstrip() + '\n\n## 资料来源\n\n' + '\n\n'.join(references) + '\n'
    provenance = json.loads(model_json(artifact))
    del provenance['content']
    return content, json.dumps(provenance, ensure_ascii=False, sort_keys=True)


class ArtifactExporter:
    """Export validated output, preserving existing files unless overwrite=True."""

    def __init__(self, *, overwrite=False, media_runner=None, media_config_path=None, timeout=120, font_path=None):
        if type(overwrite) is not bool:
            raise ValueError("overwrite须为bool")
        self.overwrite = overwrite
        self.media_runner = media_runner or run_media
        self.media_config_path = media_config_path
        self.timeout = timeout
        self.font_path = str(font_path) if font_path is not None else None

    def export(self, artifact: GeneratedArtifact, format: ExportFormat, *, destination: Path) -> Path:
        """Return the completed path after validation and an atomic file commit."""
        validate_artifact(artifact)
        if format not in ('markdown', 'pdf'):
            raise ValueError("不支持的导出格式")
        destination = Path(destination).absolute()
        expected = ('.md', '.markdown') if format == 'markdown' else ('.pdf',)
        if destination.suffix.lower() not in expected:
            raise ValueError("导出文件扩展名与格式不一致")
        content, provenance = _export_content(artifact)
        if format == 'markdown':
            data = (content + '\n<!-- artifact_metadata\n' + provenance.replace('-->', '--\\u003e') + '\n-->\n').encode('utf-8')
        else:
            response = self.media_runner('export_pdf', {'title': artifact.title, 'content': content,
                'provenance': provenance, 'font_path': self.font_path}, config_path=self.media_config_path, timeout=self.timeout)
            try:
                data = base64.b64decode(response['pdf_base64'], validate=True)
            except (KeyError, ValueError, TypeError) as error:
                raise ValueError("PDF渲染结果无效") from error
            if not data.startswith(b'%PDF-') or b'%%EOF' not in data[-1024:]:
                raise ValueError("PDF输出不完整")
        atomic_write(destination, data, overwrite=self.overwrite)
        return destination
