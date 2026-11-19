"""Split every Markdown document by headings and character windows."""
from bisect import bisect_right
import re
from typing import Iterator


def iter_markdown_headings(text: str) -> Iterator[tuple[int, int, str]]:
    """Yield ATX headings outside fenced code, with their character offsets."""
    fence = None
    offset = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        marker_match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", content)
        if fence is not None:
            if marker_match:
                marker, tail = marker_match.groups()
                if (marker[0] == fence[0] and len(marker) >= len(fence)
                        and not tail.strip(" \t")):
                    fence = None
        elif marker_match:
            marker, info = marker_match.groups()
            if marker[0] != "`" or "`" not in info:
                fence = marker
        else:
            heading = re.match(r"^ {0,3}(#{1,6})[ \t]+(.+?)\s*$", content)
            if heading:
                label = re.sub(r"[ \t]+#+[ \t]*$", "", heading.group(2))
                yield offset, len(heading.group(1)), label
        offset += len(line)
def search_text(chunk: dict) -> str:
    """Add document identity for ranking while retaining original text for citations."""
    metadata = chunk.get("metadata") or {}
    parts = (metadata.get("title"), metadata.get("heading"), chunk["text"])
    if metadata.get("index_text_cleaned"):
        text = "\n".join(dict.fromkeys(str(part) for part in parts if part))
        text = re.sub(r"\{\*.*?\*\}", "", text, flags=re.DOTALL)
        return re.sub(r"\{\s*#[^}\n]+\}", "", text).strip()
    return "\n".join(str(part) for part in parts if part)


def _boilerplate_heading(heading: str) -> bool:
    for part in heading.split(" / "):
        label = re.sub(r"\s*\{\s*#[^}]+\}", "", part).strip()
        if re.fullmatch(r"Sponsors?|Sponsorship|赞助商|赞助者|FastAPI\s*(?:Mini Documentary|迷你纪录片)", label, re.IGNORECASE):
            return True
    return False


def _has_body(text: str) -> bool:
    body = re.sub(r"(?m)^\s*#{1,6}\s+.*$", "", text)
    body = re.sub(r"<!--.*?-->|\{\*.*?\*\}|<a\b[^>]*>\s*</a>", "", body, flags=re.DOTALL | re.IGNORECASE)
    body = re.sub(r"\{\s*#[^}\n]+\}", "", body)
    return bool(body.strip(" \t\r\n`~#-*"))


def _paragraph_ranges(text: str, start: int, stop: int, size: int, overlap: int):
    """Prefer paragraph/sentence boundaries, keeping every source character traceable."""
    preferred, sentences, lines = [], [], []
    position = start
    fence = None
    for line in text[start:stop].splitlines(keepends=True):
        end = position + len(line)
        lines.append(end)
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
        if fence is not None:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
                preferred.append(end)
        elif marker:
            fence = marker[1]
            preferred.append(position)
        else:
            if not line.strip():
                preferred.append(end)
            sentences.extend(position + match.end() for match in re.finditer(r"[。！？]|[.!?](?:[ \t]+|$)", line.rstrip("\r\n")))
        position = end
    boundaries = sorted(set(preferred + sentences + lines))
    while start < stop:
        limit = min(start + size, stop)
        end = limit
        if limit < stop:
            for choices in (preferred, sentences, lines):
                index = bisect_right(choices, limit) - 1
                if index >= 0 and choices[index] >= start + max(1, size // 3):
                    end = choices[index]
                    break
            else:
                spaces = list(re.finditer(r"\s+", text[start:limit]))
                if spaces and spaces[-1].end() >= size // 2:
                    end = start + spaces[-1].end()
        yield start, end
        if end == stop:
            break
        first = bisect_right(boundaries, max(start, end - overlap) - 1)
        next_start = boundaries[first] if first < len(boundaries) and start < boundaries[first] < end else end
        start = next_start


def split_document(doc: dict, chunk_size: int, chunk_overlap: int, *, strategy: str = "window", exclude_boilerplate: bool = False) -> list[dict]:
    """Keep source line ranges, heading paths and revision metadata on every chunk."""
    if chunk_size <= 0 or not 0 <= chunk_overlap < chunk_size:
        raise ValueError("chunk_overlap 必须大于等于 0 且小于正数 chunk_size")
    if strategy not in ("window", "paragraph") or type(exclude_boilerplate) is not bool:
        raise ValueError("分块策略或清洗开关无效")
    text = doc["text"]
    metadata = dict(doc.get("metadata") or {})
    metadata.update({
        "doc_id": doc["doc_id"],
        "title": doc.get("title") or metadata.get("title") or doc["doc_id"],
        "source_type": "markdown",
        "source_path": doc.get("source_path") or doc.get("relative_path") or "",
        "relative_path": doc.get("relative_path") or doc.get("source_path") or "",
        "revision_id": doc.get("revision_id") or metadata.get("revision_id") or "",
    })
    line_offsets = []
    sections = []
    heading_stack = []
    current_heading = ""
    section_start = 0
    offset = 0
    for line in text.splitlines(keepends=True):
        line_offsets.append(offset)
        offset += len(line)
    for offset, level, label in iter_markdown_headings(text):
        if offset > section_start:
            sections.append((section_start, offset, current_heading))
        heading_stack = [(depth, title) for depth, title in heading_stack if depth < level]
        heading_stack.append((level, label))
        current_heading = " / ".join(title for _, title in heading_stack)
        section_start = offset
    sections.append((section_start, len(text), current_heading))
    chunks = []
    first_line = doc.get("body_line_start", 1)
    step = chunk_size - chunk_overlap
    for section_start, section_end, heading in sections:
        if exclude_boilerplate and (_boilerplate_heading(heading) or not _has_body(text[section_start:section_end])):
            continue
        ranges = ((start, min(start + chunk_size, section_end)) for start in range(section_start, section_end, step))
        if strategy == "paragraph":
            ranges = _paragraph_ranges(text, section_start, section_end, chunk_size, chunk_overlap)
        for start, end in ranges:
            raw_piece = text[start:end]
            piece = raw_piece.strip()
            if piece:
                actual_start = start + len(raw_piece) - len(raw_piece.lstrip())
                actual_end = end - len(raw_piece) + len(raw_piece.rstrip())
                chunk_meta = dict(metadata)
                chunk_meta.update({
                    "heading": heading,
                    "line_start": first_line + bisect_right(line_offsets, actual_start) - 1,
                    "line_end": first_line + bisect_right(line_offsets, actual_end - 1) - 1,
                    "char_start": actual_start,
                    "char_end": actual_end,
                })
                if exclude_boilerplate:
                    chunk_meta["index_text_cleaned"] = True
                chunks.append({
                    "chunk_id": f"{doc['doc_id']}#{len(chunks)}",
                    "text": piece,
                    "metadata": chunk_meta,
                })
            if end == section_end:
                break
    return chunks
