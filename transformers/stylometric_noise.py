import random
import re

from util import vlog


def apply_stylometric_noise(source_text: str) -> str:
    """Injects small randomized formatting and operator-style tics."""
    result = source_text
    changes = 0

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

    vlog("stylometry", f"substitutions={changes}, bytes {len(source_text)} -> {len(result)}")
    return result
