"""JSON codecs and atomic writes for persistent extension services."""
from dataclasses import fields, is_dataclass
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path, PureWindowsPath, PurePosixPath
import tempfile
from typing import Mapping
from math import isfinite

from app.contracts import GeneratedArtifact, GovernedDocument, MemoryEntry, SourceLocator, SourceReference, SourceSpan


def _datetime(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("持久化时间必须带时区")
    return value.astimezone(timezone.utc).isoformat()


def _json_value(value):
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("JSON对象的键须为字符串")
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, datetime):
        return _datetime(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def model_json(model):
    try:
        return json.dumps(_json_value(model), ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, OverflowError, RecursionError) as error:
        raise ValueError("数据不能序列化为JSON") from error


def model_from_json(model_type, content):
    try:
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("记录须为JSON对象")
        if model_type in (MemoryEntry, GeneratedArtifact):
            refs = []
            for item in data.get('source_refs', []):
                item['locator'] = SourceLocator(**item['locator'])
                refs.append(SourceReference(**item))
            data['source_refs'] = tuple(refs)
        if model_type is GovernedDocument:
            data['source_spans'] = tuple(SourceSpan(item['text_start'], item['text_end'], SourceLocator(**item['locator'])) for item in data.get('source_spans', []))
            data['tags'] = tuple(data.get('tags', []))
            data['quality_reasons'] = tuple(data.get('quality_reasons', []))
        for name in ('created_at', 'updated_at', 'expires_at'):
            if name in data and data[name] is not None:
                data[name] = datetime.fromisoformat(data[name])
        return model_type(**data)
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("持久化记录结构损坏") from error


def atomic_write(destination: Path, content: bytes, *, overwrite=True):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.write-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, destination)
        else:
            try:
                os.link(temporary, destination)
            except FileExistsError:
                if destination.read_bytes() != content:
                    raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def validate_locator(locator):
    if not isinstance(locator, SourceLocator):
        raise ValueError("locator须为SourceLocator")
    path = locator.relative_path
    if (not isinstance(path, str) or not path.strip() or '\x00' in path
            or PureWindowsPath(path).drive or PurePosixPath(path).is_absolute()
            or '..' in PurePosixPath(path.replace('\\', '/')).parts):
        raise ValueError("来源定位须为有效的相对路径")
    for field in ('page', 'slide', 'paragraph'):
        number = getattr(locator, field)
        if number is not None and (type(number) is not int or number <= 0):
            raise ValueError("来源页码、幻灯片和段落编号须为正整数")
    if (locator.start_seconds is None) != (locator.end_seconds is None):
        raise ValueError("媒体定位须同时提供开始和结束时间")
    if locator.start_seconds is not None:
        for value in (locator.start_seconds, locator.end_seconds):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
                raise ValueError("媒体时间须为有限数字")
        if not 0 <= locator.start_seconds < locator.end_seconds:
            raise ValueError("媒体时间范围无效")
    confidence = locator.confidence
    if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1):
        raise ValueError("来源置信度须为0到1之间的数字")


def validate_reference(reference):
    if not isinstance(reference, SourceReference):
        raise ValueError("来源须为SourceReference")
    for name in ('doc_id', 'revision_id', 'snapshot_id'):
        value = getattr(reference, name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("来源须提供文档、修订和快照编号")
    if reference.chunk_id is not None and (not isinstance(reference.chunk_id, str) or not reference.chunk_id.strip()):
        raise ValueError("chunk_id须为非空字符串或None")
    validate_locator(reference.locator)


def validate_spans(text, spans):
    if not isinstance(text, str) or not isinstance(spans, tuple):
        raise ValueError("正文和来源映射结构无效")
    for span in spans:
        if (not isinstance(span, SourceSpan) or type(span.text_start) is not int or type(span.text_end) is not int
                or not 0 <= span.text_start < span.text_end <= len(text)):
            raise ValueError("来源映射须位于正文范围内")
        validate_locator(span.locator)
