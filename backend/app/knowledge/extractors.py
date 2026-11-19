"""Local format adapters and source-preserving text assembly."""

from pathlib import Path
from typing import Mapping
from dataclasses import replace
from datetime import date
from hashlib import sha256
from io import BytesIO
import json
import posixpath
from tempfile import TemporaryDirectory
import unicodedata
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile
from math import isfinite
from app.contracts import ParsedDocument, SourceLocator, SourceSpan
from app.graph.grounding import nonblank, positive_integer
from app.media_runtime import run_media


STATUS = "implemented"


def plan_pdf_extraction(
    pages: list[dict],
    min_text_chars: int = 30,
    max_replacement_ratio: float = 0.05,
) -> list[dict]:
    """家庭作业。依据每页文字量和替换字符比例规划文字层或 OCR。

    pages 须为列表。每项须为字典，含 page_number 和 text。page_number
    须为排除 bool 的正整数且不重复；text 须为字符串，允许为空。
    min_text_chars 须为排除 bool 的正整数。max_replacement_ratio 须为
    排除 bool 的 int 或 float，且为 0 到 1 之间的有限数。非法输入
    抛出 ValueError，保持输入数据。空 pages 也完成参数校验后返回 []。

    每页统计所有非空白字符的数量，使用字符的 isspace() 判断空白。
    U+FFFD 替换字符也计入此总数。替换字符数量除以非空白字符总数为
    replacement_ratio，总数为零时比例为 0.0。此指标针对 U+FFFD。

    当字符数 >= min_text_chars 且比例 <= max_replacement_ratio 时，
    method 为 text，否则为 ocr。先用未舍入比例判断，再将返回的比例
    round(..., 6)。临界值相等时通过对应条件。

    按输入顺序返回列表，每项含 page_number、method、text_char_count、
    replacement_ratio、reason。reason 须非空白并说明所选方法的依据。
    只使用提供的逐页文字结果，调用方负责 PDF 读取与实际 OCR 执行。
    """
    results=[]
    result={}
    if not isinstance(pages,list):
        raise ValueError
    if type(min_text_chars) is not int or min_text_chars <= 0:
                raise ValueError
    if (
    isinstance(max_replacement_ratio, bool)
    or not isinstance(max_replacement_ratio, (int, float))
    or not 0 <= max_replacement_ratio <= 1
):
        raise ValueError("比例阈值必须是 0 到 1 之间的有限数")
    if len(pages)==0 :
            return []
    seen = set()
    for page in pages:
        if not isinstance(page,dict):
            raise ValueError
        if type(page.get("page_number")) is not int or page["page_number"] <= 0:
                raise ValueError
        if page["page_number"] in seen:
            raise ValueError(f"页码重复：{page['page_number']}")
        seen.add(page["page_number"])
        if not isinstance(page.get("text"),str):
                raise ValueError
        text_char_count = 0
        replacement_count = 0
        for char in page["text"]:
            if char.isspace():  # 跳过空格、换行、制表符等
                continue
            text_char_count += 1
            if char == "\ufffd":  # 就是 �
                replacement_count += 1
        if text_char_count == 0:
            replacement_ratio = 0.0
        else:
            replacement_ratio = replacement_count / text_char_count
        result={
        "page_number":page.get("page_number"),
        "method": "text",
        "text_char_count":text_char_count,
        "replacement_ratio": round(replacement_ratio, 6),
        "reason": "文字量充足，替换字符比例符合要求。",
    }
        if text_char_count < min_text_chars:
            result["method"] = "ocr"
            result["reason"] = "文字层内容不足，需要 OCR。"
        elif replacement_ratio > max_replacement_ratio:
            result["method"] = "ocr"
            result["reason"] = "替换字符比例过高，需要 OCR。"
        else:
            result["method"] = "text"
            result["reason"] = "文字量充足，替换字符比例符合要求。"
        results.append(result)
    return results
def assemble_pdf_document(
    page_results: list[dict],
    *,
    doc_id: str,
    source_relative_path: str,
    raw_hash: str,
) -> ParsedDocument:
    for name, value in (
    ("doc_id", doc_id),
    ("source_relative_path", source_relative_path),
    ("raw_hash", raw_hash),
):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} 必须是非空白字符串")
    if not isinstance(page_results,list):
        raise ValueError
    seen = set()
    for page_result in page_results:
        if not isinstance(page_result, dict):
            raise ValueError("每页结果必须是字典")
        if (isinstance(page_result.get("page_number"), bool) or not isinstance(page_result.get("page_number"),int)) or page_result["page_number"] <= 0:
             raise ValueError
        if page_result["page_number"] in seen:
            raise ValueError(f"页码重复：{page_result['page_number']}")
        seen.add(page_result["page_number"])
        if not isinstance(page_result.get("text"),str):
            raise ValueError
        if page_result.get("method") not in ["text" , "ocr"] :
            raise ValueError
        confidence = page_result.get("confidence", None)
        if confidence is not None:
            if (
                isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
            ):
                raise ValueError("confidence 必须是数字或 None")
            if not 0 <= confidence <= 1:
                raise ValueError("confidence 必须是 0 到 1 之间的有限数字")
        if page_result["method"] == "text" and confidence is not None:
            confidence=None
            raise ValueError("文字层页面的 confidence 必须为 None") 
    text_parts = []
    source_spans = []
    ocr_pages = []
    empty_pages = []
    warnings = []
    offset = 0
    # sorted 返回新的列表，按原页码升序处理
    for page_result in sorted(
        page_results,
        key=lambda item: item["page_number"]):
        page_number = page_result["page_number"]
        text = page_result["text"].strip()
        confidence = page_result.get("confidence", None)
        if page_result["method"] == "ocr":
            ocr_pages.append(page_number)
        if not text:
            empty_pages.append(page_number)
            warnings.append(f"第 {page_number} 页未提取到有效文字。")
            continue
        # 前面已有非空页时，两个换行占用两个字符位置
        if text_parts:
            offset += 2
        text_start = offset
        text_end = text_start + len(text)
        locator = SourceLocator(
            relative_path=source_relative_path,
            page=page_number,
            confidence=confidence,
        )
        source_spans.append(
            SourceSpan(
                text_start=text_start,
                text_end=text_end,
                locator=locator,
            )
        )
        text_parts.append(text)
        offset = text_end
    merged_text = "\n\n".join(text_parts)
    if not page_results:
        warnings.append("PDF 未提供页面结果。")
    return ParsedDocument(
    doc_id=doc_id,
    source_relative_path=source_relative_path,
    media_type="application/pdf",
    raw_hash=raw_hash,
    text=merged_text,
    parser_name="pdf_assembler",
    parser_version="1",
    source_spans=tuple(source_spans),
    metadata={
        "page_count": len(page_results),
        "ocr_pages": ocr_pages,
        "empty_pages": empty_pages,
    },
    warnings=tuple(warnings),)
def assemble_office_document(
    blocks: list[dict],
    *,
    document_format: str,
    doc_id: str,
    source_relative_path: str,
    raw_hash: str,
) -> ParsedDocument:
    if document_format not in ["docx" , "pptx"] or not isinstance(document_format,str) or not document_format.strip():
        raise ValueError
    if  not isinstance(doc_id,str) or not doc_id.strip():
         raise ValueError
    if  not isinstance(raw_hash,str) or not raw_hash.strip():
        raise ValueError
    if  not isinstance(source_relative_path,str) or not source_relative_path.strip():
            raise ValueError
    if not isinstance(blocks,list):
        raise ValueError
    seen_orders = set()
    if document_format == "docx":
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        media_type = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    for block in blocks:
        if not isinstance(block,dict):
                raise ValueError
        if (isinstance(block.get("order"), bool) or not isinstance(block.get("order"),int)) or block["order"] <= 0:
                raise ValueError
        if block["order"] in seen_orders:
            raise ValueError("order 不能重复")
        seen_orders.add(block["order"])
        if not isinstance(block.get("text"), str):
             raise ValueError
        if document_format=="docx":
            if type(block.get("paragraph")) is not int or block.get("paragraph") <= 0:
                raise ValueError("paragraph 必须是正整数，不能是 bool")
        if document_format=="pptx":
            if type(block.get("slide")) is not int or block.get("slide") <= 0:
                    raise ValueError("slide 必须是正整数，不能是 bool")
    source_spans = []
    text_parts = []
    warnings = []
    offset = 0
    empty_blocks=[]
    if not blocks:
        warnings.append("Office 未提供文本块。")
    for block in sorted(
            blocks,
            key=lambda item: item["order"]):
        text=block.get("text").strip()
        if not text:
            empty_blocks.append(block["order"])
            warnings.append(
                f"第 {block['order']} 个文本块未提取到有效文字。"
            )
            continue
        if document_format == "docx":
            locator = SourceLocator(
                relative_path=source_relative_path,
                paragraph=block["paragraph"],
            )
        else:
            locator = SourceLocator(
                relative_path=source_relative_path,
                slide=block["slide"],
            )
        if text_parts:
            offset += 2
        text_start = offset
        text_end = text_start + len(text)
        text_parts.append(text)
        offset = text_end
        source_spans.append(
            SourceSpan(
            text_start=text_start,
            text_end=text_end,
            locator=locator,
        ))
    merged_text = "\n\n".join(text_parts)
    return ParsedDocument(
    doc_id=doc_id,
    source_relative_path=source_relative_path,
    raw_hash=raw_hash,
    media_type=media_type,
    text=merged_text,
    parser_name="office_assembler",
    parser_version="1",
    source_spans=tuple(source_spans),
    metadata = {
    "document_format": document_format,
    "block_count": len(blocks),
    "empty_blocks": empty_blocks,
},
    warnings=tuple(warnings),)
def assemble_ocr_document(
    blocks: list[dict],
    *,
    doc_id: str,
    source_relative_path: str,
    raw_hash: str,
    media_type: str,
    min_confidence: float = 0.7,
) -> ParsedDocument:
    for name, value in (
        ("doc_id", doc_id),
        ("source_relative_path", source_relative_path),
        ("raw_hash", raw_hash),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} 必须是非空白字符串")

    if not isinstance(blocks, list):
        raise ValueError("blocks 必须是列表")

    if media_type not in ["image/png", "image/jpeg", "image/webp"]:
        raise ValueError("不支持的图片 media_type")

    if (
        isinstance(min_confidence, bool)
        or not isinstance(min_confidence, (int, float))
        or not 0 <= min_confidence <= 1
    ):
        raise ValueError("min_confidence 必须是 0 到 1 之间的有限数字")

    seen_orders = set()
    for block in blocks:
        if not isinstance(block, dict):
            raise ValueError("每个文本块必须是字典")

        if (
            isinstance(block.get("order"), bool)
            or not isinstance(block.get("order"), int)
            or block["order"] <= 0
        ):
            raise ValueError("order 必须是正整数，不能是 bool")

        if block["order"] in seen_orders:
            raise ValueError("order 不能重复")
        seen_orders.add(block["order"])

        if not isinstance(block.get("text"), str):
            raise ValueError("text 必须是字符串")

        confidence = block.get("confidence", None)
        if confidence is not None:
            if (
                isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
                or not 0 <= confidence <= 1
            ):
                raise ValueError(
                    "confidence 必须是 None 或 0 到 1 之间的有限数字"
                )
    text_parts = []
    empty_blocks = []
    warnings = []
    source_spans = []
    offset = 0
    low_confidence_blocks = []
    unknown_confidence_blocks = []

    if not blocks:
        warnings.append("OCR 未提供文本块。")

    for block in sorted(blocks, key=lambda item: item["order"]):
        text = block["text"].strip()
        confidence = block.get("confidence", None)

        if not text:
            empty_blocks.append(block["order"])
            warnings.append(
                f"第 {block['order']} 个文本块未提取到有效文字。"
            )
            continue

        # confidence 为 None 时，记录未知置信度的编号和警告。
        if confidence is None:
            unknown_confidence_blocks.append(block["order"])
            warnings.append(
                f"第 {block['order']} 个文本块缺少识别置信度。"
            )
        # confidence 小于 min_confidence 时，记录低置信度的编号和警告。
        elif confidence < min_confidence:
            low_confidence_blocks.append(block["order"])
            warnings.append(
                f"第 {block['order']} 个文本块识别置信度偏低。"
            )
        if text_parts:
            offset += 2
        text_start = offset
        text_end = text_start + len(text)
        locator = SourceLocator(
            relative_path=source_relative_path,
            confidence=confidence,
        )
        source_spans.append(
            SourceSpan(
                text_start=text_start,
                text_end=text_end,
                locator=locator,
            )
        )
        text_parts.append(text)
        offset = text_end
    merged_text = "\n\n".join(text_parts)
    return ParsedDocument(
        doc_id=doc_id,
        source_relative_path=source_relative_path,
        media_type=media_type,
        raw_hash=raw_hash,
        text=merged_text,
        parser_name="ocr_assembler",
        parser_version="1",
        source_spans=tuple(source_spans),
        metadata={
            "block_count": len(blocks),
            "empty_blocks": empty_blocks,
            "low_confidence_blocks": low_confidence_blocks,
            "unknown_confidence_blocks": unknown_confidence_blocks,
            "min_confidence": min_confidence,
        },
        warnings=tuple(warnings),
    )
def assemble_transcript_document(
    segments: list[dict],
    *,
    doc_id: str,
    source_relative_path: str,
    raw_hash: str,
    media_type: str,
) -> ParsedDocument:
    for name, value in (
        ("doc_id", doc_id),
        ("source_relative_path", source_relative_path),
        ("raw_hash", raw_hash),
        ("media_type", media_type),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} 必须是非空白字符串")
    if not isinstance(segments, list):
        raise ValueError("segments 必须是列表")
    seen_numbers = set()
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("每个转写片段必须是字典")
        segment_number = segment.get("segment_number")
        if (
            isinstance(segment_number, bool)
            or not isinstance(segment_number, int)
            or segment_number <= 0
        ):
            raise ValueError("segment_number 必须是正整数，不能是 bool")
        if segment_number in seen_numbers:
            raise ValueError(f"片段编号重复：{segment_number}")
        seen_numbers.add(segment_number)
        for name in ("start_seconds", "end_seconds"):
            value = segment.get(name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or (isinstance(value, float) and not isfinite(value))
            ):
                raise ValueError(f"{name} 必须是有限数字，不能是 bool")
        if not 0 <= segment["start_seconds"] < segment["end_seconds"]:
            raise ValueError(
                "时间必须满足 0 <= start_seconds < end_seconds"
            )
        if not isinstance(segment.get("text"), str):
            raise ValueError("text 必须是字符串")
    text_parts = []
    source_spans = []
    empty_segments = []
    overlap_segments = []
    warnings = []
    offset = 0
    max_end_seconds = 0

    if not segments:
        warnings.append("转写未提供片段。")

    for segment in sorted(
        segments,
        key=lambda item: (
            item["start_seconds"],
            item["segment_number"],
        ),
    ):
        segment_number = segment["segment_number"]
        text = segment["text"].strip()
        start_seconds = segment["start_seconds"]
        end_seconds = segment["end_seconds"]

        # 空白片段只记录警告，跳过重叠判断和最大结束时间更新。
        if not text:
            empty_segments.append(segment_number)
            warnings.append(f"第 {segment_number} 个转写片段未提取到有效文字。")
            continue

        # 与此前所有非空片段的最大结束时间比较，兼顾嵌套重叠。
        if start_seconds < max_end_seconds:
            overlap_segments.append(segment_number)
            warnings.append(
                f"第 {segment_number} 个转写片段与前面的有效片段时间重叠。"
            )
        max_end_seconds = max(max_end_seconds, end_seconds)

        if text_parts:
            offset += 2
        text_start = offset
        text_end = text_start + len(text)
        locator = SourceLocator(
            relative_path=source_relative_path,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
        )
        source_spans.append(
            SourceSpan(
                text_start=text_start,
                text_end=text_end,
                locator=locator,
            )
        )
        text_parts.append(text)
        offset = text_end

    merged_text = "\n\n".join(text_parts)
    return ParsedDocument(
        doc_id=doc_id,
        source_relative_path=source_relative_path,
        media_type=media_type,
        raw_hash=raw_hash,
        text=merged_text,
        parser_name="transcript_assembler",
        parser_version="1",
        source_spans=tuple(source_spans),
        metadata={
            "segment_count": len(segments),
            "empty_segments": empty_segments,
            "overlap_segments": overlap_segments,
        },
        warnings=tuple(warnings),
    )
class Extractor:
    """Read one original without modifying it and retain format locations."""

    def __init__(self, *, media_runner=None):
        self.media_runner = media_runner or run_media

    def extract(
        self, path: Path, options: Mapping[str, object]
    ) -> ParsedDocument:
        """Extract Markdown, PDF, Office, image or audio/video from fixed bytes.

        storage_root defaults to the original's parent. doc_id is optional.
        Media options include language, max_pages, timeout and media_config_path.
        Empty results carry warnings. Invalid files and unavailable engines fail
        explicitly. No network request is made during extraction.
        """
        if not isinstance(path, (str, Path)) or not isinstance(options, Mapping):
            raise ValueError("path须为路径，options须为映射")
        path = Path(path).resolve(strict=True)
        root = Path(options.get('storage_root', path.parent)).resolve(strict=True)
        if not root.is_dir() or not path.is_file() or not path.is_relative_to(root):
            raise ValueError("原件须位于storage_root目录中")
        relative_path = unicodedata.normalize('NFC', path.relative_to(root).as_posix())
        max_bytes = options.get('max_bytes', 500 * 1024 * 1024)
        positive_integer(max_bytes, 'max_bytes')
        if path.stat().st_size > max_bytes:
            raise ValueError("原件大小超过解析上限")
        raw = path.read_bytes()
        if len(raw) > max_bytes:
            raise ValueError("原件大小超过解析上限")
        identity = {
            'doc_id': nonblank(options.get('doc_id', 'doc_' + sha256(relative_path.encode('utf-8')).hexdigest()[:24]), 'doc_id'),
            'source_relative_path': relative_path, 'raw_hash': sha256(raw).hexdigest(),
        }
        suffix = path.suffix.lower()
        metadata = options.get('metadata', {})
        if not isinstance(metadata, Mapping):
            raise ValueError("metadata须为映射")
        if suffix == '.md':
            from app.rag.loader import parse_front_matter
            import yaml
            try:
                parsed_metadata, text = parse_front_matter(raw.decode('utf-8-sig'))
            except (UnicodeDecodeError, yaml.YAMLError) as error:
                raise ValueError("Markdown编码或前置元数据损坏") from error
            spans = (SourceSpan(0, len(text), SourceLocator(relative_path, paragraph=1)),) if text else ()
            result = ParsedDocument(**identity, media_type='text/markdown', text=text,
                                    parser_name='markdown', parser_version='1', source_spans=spans,
                                    metadata=parsed_metadata)
        elif suffix in ('.docx', '.pptx'):
            blocks = _office_blocks(raw, suffix[1:])
            result = assemble_office_document(blocks, document_format=suffix[1:], **identity)
        else:
            media_types = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp',
                           '.wav': 'audio/wav', '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.flac': 'audio/flac',
                           '.ogg': 'audio/ogg', '.mp4': 'video/mp4', '.mov': 'video/quicktime',
                           '.mkv': 'video/x-matroska', '.webm': 'video/webm'}
            if suffix != '.pdf' and suffix not in media_types:
                raise ValueError(f"不支持的文件格式{suffix}")
            # Parse the captured bytes so a concurrent original-file edit cannot
            # change the content associated with raw_hash.
            with TemporaryDirectory(prefix='pkb-extract-') as temporary:
                frozen = Path(temporary) / ('original' + suffix)
                frozen.write_bytes(raw)
                payload = {'path': str(frozen)}
                runner_options = {'config_path': options.get('media_config_path'), 'timeout': options.get('timeout', 600)}
                if suffix == '.pdf':
                    minimum = options.get('min_text_chars', 30)
                    replacement_ratio = options.get('max_replacement_ratio', .05)
                    plan_pdf_extraction([], minimum, replacement_ratio)
                    max_pages = options.get('max_pages', 500)
                    positive_integer(max_pages, 'max_pages')
                    response = self.media_runner('pdf', payload | {'min_text_chars': minimum,
                        'max_replacement_ratio': replacement_ratio, 'max_pages': max_pages}, **runner_options)
                    result = assemble_pdf_document(response['pages'], **identity)
                elif media_types[suffix].startswith('image/'):
                    response = self.media_runner('ocr', payload, **runner_options)
                    result = assemble_ocr_document(response['blocks'], media_type=media_types[suffix],
                                                   min_confidence=options.get('min_confidence', .7), **identity)
                else:
                    language = options.get('language')
                    if language is not None:
                        nonblank(language, 'language')
                    response = self.media_runner('transcribe', payload | {'language': language}, **runner_options)
                    result = assemble_transcript_document(response['segments'], media_type=media_types[suffix], **identity)
                    result = replace(result, metadata=dict(result.metadata) | {'language': response.get('language'),
                                                                               'duration': response.get('duration')})
                result = replace(result, metadata=dict(result.metadata) | {'engine_versions': response.get('engine_versions', {})})
        combined = dict(result.metadata) | dict(metadata) | {'source_relative_path': relative_path}
        try:
            combined = json.loads(json.dumps(combined, ensure_ascii=False, allow_nan=False,
                default=lambda value: value.isoformat() if isinstance(value, date) else _invalid_metadata()))
        except (TypeError, OverflowError) as error:
            raise ValueError("元数据须可序列化为JSON") from error
        warnings = result.warnings
        if not result.text.strip() and not warnings:
            warnings = ('原件未提取到有效文字。',)
        return replace(result, metadata=combined, warnings=warnings)


def _invalid_metadata():
    raise ValueError("元数据须可序列化为JSON")


def _office_blocks(raw, document_format):
    namespaces = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
                  'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
                  'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
                  'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
    blocks = []
    try:
        with ZipFile(BytesIO(raw)) as archive:
            if len(archive.infolist()) > 10000 or sum(item.file_size for item in archive.infolist()) > 256 * 1024 * 1024:
                raise ValueError("Office解压内容超过处理上限")
            if document_format == 'docx':
                tree = ElementTree.fromstring(archive.read('word/document.xml'))
                for number, paragraph in enumerate(tree.iter('{' + namespaces['w'] + '}p'), 1):
                    parts = []
                    for node in paragraph.iter():
                        if node.tag == '{' + namespaces['w'] + '}t':
                            parts.append(node.text or '')
                        elif node.tag in ('{' + namespaces['w'] + '}br', '{' + namespaces['w'] + '}cr'):
                            parts.append('\n')
                        elif node.tag == '{' + namespaces['w'] + '}tab':
                            parts.append('\t')
                    blocks.append({'order': number, 'paragraph': number, 'text': ''.join(parts)})
            else:
                presentation = ElementTree.fromstring(archive.read('ppt/presentation.xml'))
                relationships = ElementTree.fromstring(archive.read('ppt/_rels/presentation.xml.rels'))
                targets = {node.get('Id'): node.get('Target') for node in relationships
                           if node.get('TargetMode') != 'External'}
                slide_list = presentation.find('p:sldIdLst', namespaces)
                if slide_list is None:
                    return []
                for number, slide in enumerate(slide_list, 1):
                    target = targets[slide.get('{' + namespaces['r'] + '}id')]
                    name = posixpath.normpath('ppt/' + target) if not target.startswith('/') else target.lstrip('/')
                    if not name.startswith('ppt/slides/'):
                        raise ValueError("幻灯片引用路径无效")
                    tree = ElementTree.fromstring(archive.read(name))
                    for paragraph in tree.iter('{' + namespaces['a'] + '}p'):
                        text = ''.join(node.text or '' for node in paragraph.iter('{' + namespaces['a'] + '}t'))
                        blocks.append({'order': len(blocks) + 1, 'slide': number, 'text': text})
    except (BadZipFile, KeyError, TypeError, ElementTree.ParseError) as error:
        raise ValueError("Office文件结构损坏或引用无效") from error
    return blocks
