import random
import re

from util import find_vm_protected_regions, vlog


def _apply_outside_vm_regions(source_text, transform):
    regions = find_vm_protected_regions(source_text)
    if not regions:
        return transform(source_text)

    parts = []
    cursor = 0
    for start, end in regions:
        if cursor < start:
            parts.append(transform(source_text[cursor:start]))
        parts.append(source_text[start:end])
        cursor = end
    if cursor < len(source_text):
        parts.append(transform(source_text[cursor:]))
    return "".join(parts)


def degrade_whitespace_formatting(source_text):
    """Destroys indentation and most non-essential spacing to reduce readability."""
    in_lines = source_text.count("\n") + (0 if not source_text else 1)
    degraded_lines = []
    statement_buffer = []
    removed_blank = 0

    def flush_statement_buffer():
        if not statement_buffer:
            return

        if len(statement_buffer) > 1 and random.random() > 0.35:
            joiner = "\t" * random.randint(1, 4)
            degraded_lines.append(joiner.join(statement_buffer))
        else:
            degraded_lines.extend(statement_buffer)
        statement_buffer.clear()

    for line in source_text.splitlines():
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
    out = "\n".join(degraded_lines) + ("\n" if source_text.endswith("\n") else "")
    out_lines = out.count("\n") + (0 if not out else 1)
    vlog(
        "whitespace",
        f"lines {in_lines} -> {out_lines} (removed_blank={removed_blank}), bytes {len(source_text)} -> {len(out)}",
    )
    return out


def apply_stylometric_noise(source_text):
    """Injects small randomized formatting and operator-style tics."""
    changes = 0

    def _transform(segment):
        nonlocal changes
        result = segment

        if random.random() > 0.4:
            result, n = re.subn(r"\+\+\s*([A-Za-z_]\w*)", r"\1 += 1", result)
            changes += n
        if random.random() > 0.4:
            result, n = re.subn(r"\b([A-Za-z_]\w*)\s*\+=\s*1\s*;", r"++\1;", result)
            changes += n

        if random.random() > 0.5:
            result, n = re.subn(
                r"\b(if|while|switch)\s*\(([^()\n]+)\)",
                lambda m: f"{m.group(1)} (({m.group(2)}))",
                result,
            )
            changes += n

        if random.random() > 0.5:
            result, n = re.subn(r"\b(if|while|switch|else)\b([^{\n]*?)\{", r"\1\2\n{", result)
        else:
            result, n = re.subn(r"\b(if|while|switch|else)\b([^{\n]*?)\n\s*\{", r"\1\2 {", result)
        changes += n
        return result

    result = _apply_outside_vm_regions(source_text, _transform)
    vlog("stylometry", f"substitutions={changes}, bytes {len(source_text)} -> {len(result)}")
    return result
