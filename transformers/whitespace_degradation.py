import random
import re

from util import find_vm_protected_regions, vlog


def degrade_whitespace_formatting(source_text: str) -> str:
    """Destroys indentation and most non-essential spacing to reduce readability."""
    def _transform(segment):
        in_lines = segment.count("\n") + (0 if not segment else 1)
        degraded_lines: list[str] = []
        statement_buffer: list[str] = []
        removed_blank = 0

        def flush_statement_buffer() -> None:
            if not statement_buffer:
                return
            if len(statement_buffer) > 1 and random.random() > 0.35:
                joiner = "\t" * random.randint(1, 4)
                degraded_lines.append(joiner.join(statement_buffer))
            else:
                degraded_lines.extend(statement_buffer)
            statement_buffer.clear()

        for line in segment.splitlines():
            stripped = line.strip()
            if not stripped:
                removed_blank += 1
                continue

            if stripped.startswith("#"):
                flush_statement_buffer()
                degraded_lines.append(stripped)
                continue

            collapsed = re.sub(r"[ \t]+", " ", stripped)
            collapsed = re.sub(r"\s*([{}();,])\s*", r"\1", collapsed)
            collapsed = re.sub(r"\s*([\[\]])\s*", r"\1", collapsed)
            collapsed = re.sub(r"\s*([?:~])\s*", r"\1", collapsed)
            collapsed = re.sub(
                r"\s*(<<|>>|<=|>=|==|!=|\+=|-=|\*=|/=|%=|&=|\|=|\^=|&&|\|\||::|->)\s*",
                r"\1",
                collapsed,
            )
            collapsed = re.sub(r"\s*([=+\-*/%<>|&^])\s*", r"\1", collapsed)

            if collapsed in {"{", "}"} or collapsed.endswith(":"):
                flush_statement_buffer()
                degraded_lines.append(("\t" * random.randint(0, 5)) + collapsed)
                continue

            if collapsed.endswith(";"):
                statement_buffer.append(("\t" * random.randint(0, 5)) + collapsed)
                continue

            flush_statement_buffer()
            degraded_lines.append(("\t" * random.randint(0, 5)) + collapsed)

        flush_statement_buffer()
        out = "\n".join(degraded_lines) + ("\n" if segment.endswith("\n") else "")
        out_lines = out.count("\n") + (0 if not out else 1)
        return out, in_lines, out_lines, removed_blank

    regions = find_vm_protected_regions(source_text)
    if not regions:
        out, in_lines, out_lines, removed_blank = _transform(source_text)
        vlog(
            "whitespace",
            f"lines {in_lines} -> {out_lines} (removed_blank={removed_blank}), bytes {len(source_text)} -> {len(out)}",
        )
        return out

    pieces = []
    cursor = 0
    total_removed = 0
    for start, end in regions:
        if cursor < start:
            transformed, _, _, removed = _transform(source_text[cursor:start])
            pieces.append(transformed)
            total_removed += removed
        pieces.append(source_text[start:end])
        cursor = end
    if cursor < len(source_text):
        transformed, _, _, removed = _transform(source_text[cursor:])
        pieces.append(transformed)
        total_removed += removed
    out = "".join(pieces)
    in_lines = source_text.count("\n") + (0 if not source_text else 1)
    out_lines = out.count("\n") + (0 if not out else 1)
    vlog(
        "whitespace",
        f"lines {in_lines} -> {out_lines} (removed_blank={total_removed}), bytes {len(source_text)} -> {len(out)}",
    )
    return out
