import random
import re

from util import generate_barcode_name
from util import vlog


INCLUDE_LINE_RE = re.compile(
    r"^(?P<indent>\s*)#\s*include\s*(?P<header><[^>]+>|\"[^\"]+\")(?P<trailing>[^\n\r]*)$",
    re.MULTILINE,
)


def wrap_includes_with_preprocessor_logic(source_text):
    """Wraps includes in redundant preprocessor branches without changing semantics."""
    include_count = 0

    def repl(match):
        nonlocal include_count
        include_count += 1
        indent = match.group("indent")
        header = match.group("header")
        trailing = match.group("trailing")
        guard_name = generate_barcode_name(20)
        branch_guard = generate_barcode_name(18)
        header_alias = generate_barcode_name(18)
        header_alias_2 = generate_barcode_name(18)
        magic_a = random.randint(0x10, 0xFFFF)
        magic_b = random.randint(0x10, 0xFFFF)
        include_alias_line = f"{indent}#include {header_alias_2}{trailing}"
        patterns = [
            [
                f"{indent}#if defined(__has_include) && __has_include({header})",
                f"{indent}#define {header_alias} {header}",
                f"{indent}#else",
                f"{indent}#define {header_alias} {header}",
                f"{indent}#define {branch_guard} {magic_a}",
                f"{indent}#endif",
                f"{indent}#define {header_alias_2} {header_alias}",
                include_alias_line,
                f"{indent}#undef {header_alias_2}",
                f"{indent}#undef {header_alias}",
            ],
            [
                f"{indent}#if !defined({branch_guard})",
                f"{indent}#define {branch_guard} ({magic_a}^{magic_a})",
                f"{indent}#endif",
                f"{indent}#if ({branch_guard} == 0) && defined(__has_include) && __has_include({header})",
                f"{indent}#define {header_alias} {header}",
                f"{indent}#else",
                f"{indent}#define {header_alias} {header}",
                f"{indent}#endif",
                f"{indent}#define {header_alias_2} {header_alias}",
                include_alias_line,
                f"{indent}#undef {header_alias_2}",
                f"{indent}#undef {branch_guard}",
                f"{indent}#undef {header_alias}",
            ],
            [
                f"{indent}#if defined(__has_include)",
                f"{indent}#define {guard_name} (({magic_a}&{magic_b}) == ({magic_a}&{magic_b}))",
                f"{indent}#if __has_include({header}) && {guard_name}",
                f"{indent}#define {header_alias} {header}",
                f"{indent}#else",
                f"{indent}#define {header_alias} {header}",
                f"{indent}#endif",
                f"{indent}#else",
                f"{indent}#define {header_alias} {header}",
                f"{indent}#endif",
                f"{indent}#define {header_alias_2} {header_alias}",
                include_alias_line,
                f"{indent}#undef {guard_name}",
                f"{indent}#undef {header_alias_2}",
                f"{indent}#undef {header_alias}",
            ],
            [
                f"{indent}#if defined(__has_include) && !defined({branch_guard})",
                f"{indent}#define {branch_guard} {magic_b}",
                f"{indent}#if __has_include({header})",
                f"{indent}#define {header_alias} {header}",
                f"{indent}#else",
                f"{indent}#define {header_alias} {header}",
                f"{indent}#endif",
                f"{indent}#elif ({magic_a}^{magic_a}) == 0",
                f"{indent}#define {header_alias} {header}",
                f"{indent}#else",
                f"{indent}#define {header_alias} {header}",
                f"{indent}#endif",
                f"{indent}#define {header_alias_2} {header_alias}",
                include_alias_line,
                f"{indent}#undef {header_alias_2}",
                f"{indent}#undef {header_alias}",
            ],
        ]

        if not header.startswith("<"):
            patterns.append([
                f"{indent}#if defined(__has_include) && __has_include({header})",
                f"{indent}#define {header_alias} {header}",
                f"{indent}#elif defined({branch_guard})",
                f"{indent}#define {header_alias} {header}",
                f"{indent}#else",
                f"{indent}#define {branch_guard} {magic_a}",
                f"{indent}#define {header_alias} {header}",
                f"{indent}#endif",
                f"{indent}#define {header_alias_2} {header_alias}",
                include_alias_line,
                f"{indent}#undef {header_alias_2}",
                f"{indent}#undef {header_alias}",
                f"{indent}#endif",
            ])

        return "\n".join(random.choice(patterns))

    out = INCLUDE_LINE_RE.sub(repl, source_text)
    vlog("includes", f"wrapped_includes={include_count}, bytes {len(source_text)} -> {len(out)}")
    return out
