import random

import config
from util import (
    CPP_KEYWORDS,
    find_vm_protected_regions,
    generate_barcode_name,
    is_escaped,
    replace_keywords_with_macros,
    vlog,
)


DEFINE_OBFUSCATION_TOKENS = (CPP_KEYWORDS - {"try", "catch", "throw"}) | {"cout", "cin", "endl"}


def apply_define_obfuscation(source_text):
    """Obfuscates used C++ keywords via scattered macro aliases."""
    if not config.ENABLE_DEFINE_OBFUSCATION:
        vlog("define_obf", "disabled")
        return source_text

    protected_regions = find_vm_protected_regions(source_text)
    protected_starts = {start for start, _ in protected_regions}
    protected_ends = {end for _, end in protected_regions}
    if protected_regions:
        source_for_scan = source_text
        for start, end in reversed(protected_regions):
            source_for_scan = source_for_scan[:start] + source_for_scan[end:]
    else:
        source_for_scan = source_text

    lines = source_text.splitlines(keepends=True)
    used_keywords = []
    seen = set()
    first_use_line = {}
    token = []
    in_string = False
    in_char = False
    in_line_comment = False
    in_block_comment = False
    in_preprocessor = False
    line_start = True
    current_line = 0

    def flush_token():
        nonlocal token
        if token:
            value = "".join(token)
            if value in DEFINE_OBFUSCATION_TOKENS and value not in seen:
                seen.add(value)
                used_keywords.append(value)
                first_use_line[value] = current_line
            elif value in DEFINE_OBFUSCATION_TOKENS and value not in first_use_line:
                first_use_line[value] = current_line
            token = []

    for index, char in enumerate(source_for_scan):
        next_char = source_for_scan[index + 1] if index + 1 < len(source_for_scan) else ""

        if in_line_comment:
            if char == "\n":
                in_line_comment = False
                in_preprocessor = False
                line_start = True
                current_line += 1
            continue
        if in_block_comment:
            if char == "*" and next_char == "/":
                in_block_comment = False
            continue
        if in_string:
            if char == '"' and not is_escaped(source_for_scan, index):
                in_string = False
            continue
        if in_char:
            if char == "'" and not is_escaped(source_for_scan, index):
                in_char = False
            continue

        if line_start:
            if char in " \t":
                continue
            in_preprocessor = (char == "#")
            line_start = False

        if char == "\n":
            flush_token()
            in_preprocessor = False
            line_start = True
            current_line += 1
            continue

        if in_preprocessor:
            continue

        if char == "/" and next_char == "/":
            flush_token()
            in_line_comment = True
            continue
        if char == "/" and next_char == "*":
            flush_token()
            in_block_comment = True
            continue
        if char == '"':
            flush_token()
            in_string = True
            continue
        if char == "'":
            flush_token()
            in_char = True
            continue

        if char.isalnum() or char == "_":
            token.append(char)
        else:
            flush_token()

    flush_token()

    if not used_keywords:
        vlog("define_obf", "no keywords detected; no-op")
        return source_text

    keyword_to_macro = {keyword: generate_barcode_name(18) for keyword in used_keywords}

    insert_base = 0
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("#") or not stripped.strip():
            insert_base = index + 1
            continue
        break

    define_block = []
    shuffled_keywords = used_keywords[:]
    random.shuffle(shuffled_keywords)
    for keyword in shuffled_keywords:
        define_block.append(f"#define {keyword_to_macro[keyword]} {keyword}\n")
        if random.random() > 0.6:
            define_block.append("\n")

    lines[insert_base:insert_base] = define_block

    assembled = "".join(lines)
    if not protected_regions:
        out = replace_keywords_with_macros(assembled, keyword_to_macro)
    else:
        parts = []
        cursor = 0
        for start, end in protected_regions:
            if cursor < start:
                parts.append(replace_keywords_with_macros(assembled[cursor:start], keyword_to_macro))
            parts.append(assembled[start:end])
            cursor = end
        if cursor < len(assembled):
            parts.append(replace_keywords_with_macros(assembled[cursor:], keyword_to_macro))
        out = "".join(parts)
    vlog(
        "define_obf",
        f"keywords={len(used_keywords)}, inserted_defines={len(define_block)}, bytes {len(source_text)} -> {len(out)}",
    )
    return out
