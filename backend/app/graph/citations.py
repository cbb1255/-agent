"""Extract numbered prose citations while preserving programming examples."""
import re


def extract_source_numbers(text: str) -> set[int]:
    """Read bracketed source numbers outside inline and fenced Markdown code."""
    prose_lines = []
    fence_character = None
    fence_length = 0
    for line in text.splitlines(keepends=True):
        marker = re.match(r" {0,3}(`{3,}|~{3,})([^\r\n]*)$", line.rstrip("\r\n"))
        if fence_character is not None:
            if (marker and marker.group(1)[0] == fence_character
                    and len(marker.group(1)) >= fence_length
                    and not marker.group(2).strip()):
                fence_character = None
            prose_lines.append("\n")
            continue
        if marker and (marker.group(1)[0] != "`" or "`" not in marker.group(2)):
            fence_character = marker.group(1)[0]
            fence_length = len(marker.group(1))
            prose_lines.append("\n")
        else:
            prose_lines.append(line)

    prose = re.sub(
        r"(?<!`)(`+)(?!`)(.*?)(?<!`)\1(?!`)", " ", "".join(prose_lines), flags=re.DOTALL,
    )
    # Match an entire citation chain so values[0][1] cannot contribute its
    # second subscript, while prose [1][2] keeps both source numbers.
    chains = re.finditer(r"(?<![A-Za-z0-9_\]])(?:\[\d+\])+", prose)
    return {
        int(number)
        for chain in chains
        for number in re.findall(r"\[(\d+)\]", chain.group(0))
    }
