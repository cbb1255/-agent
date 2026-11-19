"""Read UTF-8 Markdown with stable path identities and original line locations."""
from hashlib import sha256
from pathlib import Path
import unicodedata

import yaml

from app.config import get_settings
from app.rag.splitter import iter_markdown_headings


def _parse_front_matter(raw: str) -> tuple[dict, str, int]:
    # 将字符串按照换行符拆分成列表
    lines = raw.splitlines(keepends=True)
    # lines[0].lstrip("\ufeff").strip() != "---"：判断第一行去除 BOM 和两端空白后，是不是 ---
    if not lines or lines[0].lstrip("\ufeff").strip() != "---":
        return {}, raw, 1
    for index, line in enumerate(lines[1:], 1):
        # 判断是不是末尾的---
        if line.strip() == "---":
            metadata = yaml.safe_load("".join(lines[1:index])) or {}
            if not isinstance(metadata, dict):
                metadata = {}
# metadata = yaml.safe_load("""
# title: Python
# author: 张三
# """)
# 结果：{
#     "title": "Python",
#     "author": "张三"
# }
            return metadata, "".join(lines[index + 1:]), index + 2
    return {}, raw, 1


def parse_front_matter(raw: str) -> tuple[dict, str]:
    """Return metadata and body while retaining the body's original whitespace."""
    metadata, body, _ = _parse_front_matter(raw)
    return metadata, body


def load_documents(raw_dir: str | None = None, *, tolerate_parse_errors: bool = False) -> list[dict]:
    """Load Markdown recursively. Business IDs remain external metadata."""
    root = Path(raw_dir or get_settings().raw_dir).resolve()
    # 路径的绝对路径。
    if not root.exists():
        return []
    if not root.is_dir():
        raise ValueError("知识目录必须是文件夹")
    documents = []
    seen_ids = set()
    paths = sorted(
        (path for path in root.rglob("*") if path.is_file() and path.suffix.lower() == ".md"),key=lambda path: path.relative_to(root).as_posix(),
    )
# relative_to(root)：获取相对路径。
# as_posix()：将路径转换为 / 风格的字符串。
# lambda：定义排序时使用的键。
    for path in paths:
# resolve()：解析真实路径。
# is_relative_to(root)：判断真实路径是否位于知识库目录之内。
        if not path.resolve().is_relative_to(root):
            raise ValueError("文档路径不能指向知识目录之外")
        relative_path = unicodedata.normalize("NFC", path.relative_to(root).as_posix())
        raw_bytes = path.read_bytes()
        parse_error = None
        try:
            raw_text = raw_bytes.decode("utf-8")
            metadata, text, body_line_start = _parse_front_matter(raw_text)
        except UnicodeDecodeError:
            if not tolerate_parse_errors:
                raise
            raw_text = raw_bytes.decode("utf-8", errors="replace")
            metadata, text, body_line_start = {}, raw_text, 1
            parse_error = "UTF-8 解码失败，请修正文件编码或清理该文档。"
        except yaml.YAMLError:
            if not tolerate_parse_errors:
                raise
            metadata, text, body_line_start = {}, raw_text, 1
            parse_error = "YAML 前置元数据解析失败，请修正元数据或清理该文档。"
        heading = next(iter_markdown_headings(text), None) if parse_error is None else None
        heading_title = heading[2].strip() if heading else ""
        title = str(metadata.get("title") or heading_title or path.stem)
        doc_id = "doc_" + sha256(relative_path.encode("utf-8")).hexdigest()[:24]
        if doc_id in seen_ids:
            raise ValueError(f"文档规范路径或 ID 冲突: {relative_path}")
        seen_ids.add(doc_id)
        revision_id = sha256(raw_bytes).hexdigest()
        external_id = metadata.get("external_id") or metadata.get("doc_id") or metadata.get("sku")
        metadata = dict(metadata)
        metadata.update({
            "doc_id": doc_id,
            "title": title,
            "source_type": "markdown",
            "source_path": relative_path,
            "relative_path": relative_path,
            "revision_id": revision_id,
        })
        if external_id is not None:
            metadata["external_id"] = str(external_id)
        document = {
            "doc_id": doc_id,
            "title": title,
            "source_type": "markdown",
            "source_path": relative_path,
            "relative_path": relative_path,
            "path": str(path),
            "size_bytes": len(raw_bytes),
            "revision_id": revision_id,
            "metadata": metadata,
            "text": text,
            "raw_text": raw_text,
            "body_line_start": body_line_start,
        }
        if parse_error is not None:
            document["parse_error"] = parse_error
        documents.append(document)
    return documents
