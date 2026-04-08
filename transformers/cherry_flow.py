import random
import re

import config
from transformers.structural import _extract_flatten_return_type
from util import (
    CHERRY_SKIP_MARKER,
    generate_barcode_name,
    indent_block,
    iter_function_definitions,
    looks_like_declaration,
    split_top_level_statements,
    vlog,
)


NUMERIC_RETURN_RE = re.compile(
    r"^(?:unsigned\s+|signed\s+)?(?:(?:long\s+long|long|short)\s+)?(?:int|char|float|double|bool|size_t|ssize_t)$"
)


def _is_supported_return_type(return_type):
    normalized = " ".join(return_type.split())
    return bool(NUMERIC_RETURN_RE.fullmatch(normalized))


def _rewrite_return_statement(statement, cherry_name, return_type):
    stripped = statement.strip()
    if not stripped.startswith("return "):
        return None
    expr = stripped[len("return ") : -1].strip()
    if not expr:
        return None
    return (
        f"{cherry_name} = static_cast<long long>(0);\n"
        f"return static_cast<{return_type}>(({expr}) + static_cast<decltype({expr})>({cherry_name}));"
    )


def build_cherry_body(body_text, base_indent, return_type):
    if not _is_supported_return_type(return_type):
        return None
    if "goto " in body_text or "goto\t" in body_text:
        return None

    statements = split_top_level_statements(body_text)
    if len(statements) < 2:
        return None

    cherry_name = generate_barcode_name(18)
    branch_name = generate_barcode_name(18)
    ghost_name = generate_barcode_name(18)
    sink_name = generate_barcode_name(18)
    seed = random.randint(0x10, 0xFFFF)
    mask = random.randint(0x10, 0xFF)

    first_executable = None
    for index, statement in enumerate(statements):
        if not looks_like_declaration(statement):
            first_executable = index
            break
    if first_executable is None:
        return None

    rebuilt = []
    changed = 0
    rebuilt.append(f"{base_indent}    {CHERRY_SKIP_MARKER}")
    for statement in statements[:first_executable]:
        rebuilt.extend(indent_block(statement, base_indent + "    "))

    cherry_intro = [
        f"{base_indent}    volatile long long {cherry_name} = static_cast<long long>({seed});",
        f"{base_indent}    int {branch_name} = static_cast<int>(({cherry_name} ^ static_cast<long long>({seed})) & {mask});",
        f"{base_indent}    if ((sizeof(long long) == 0) && ({branch_name} != 0)) {{",
        f"{base_indent}        long long {ghost_name} = ({cherry_name} + {branch_name}) ^ static_cast<long long>({mask});",
        f"{base_indent}        {cherry_name} ^= ({ghost_name} & static_cast<long long>({mask}));",
        f"{base_indent}        volatile int {sink_name} = static_cast<int>({ghost_name});",
        f"{base_indent}        (void){sink_name};",
        f"{base_indent}    }}",
        f"{base_indent}    (void)({cherry_name});",
    ]
    rebuilt.extend(cherry_intro)

    for statement in statements[first_executable:]:
        rewritten = _rewrite_return_statement(statement, cherry_name, return_type)
        if rewritten is not None:
            rebuilt.extend(indent_block(rewritten, base_indent + "    "))
            changed += 1
            continue
        rebuilt.extend(indent_block(statement, base_indent + "    "))

    if changed == 0:
        return None
    return "{\n" + "\n".join(rebuilt) + f"\n{base_indent}}}"


def apply_cherry_flow_obfuscation(source_text):
    if not config.ENABLE_CHERRY_FLOW_OBFUSCATION:
        vlog("cherry", "disabled")
        return source_text

    replacements = []
    transformed = 0
    for function_info in iter_function_definitions(source_text):
        if function_info.get("skip_structural"):
            continue
        return_type = _extract_flatten_return_type(function_info["prefix"], function_info["name"])
        if return_type is None or not _is_supported_return_type(return_type):
            continue
        rewritten_body = build_cherry_body(function_info["body_text"], function_info["base_indent"], return_type)
        if rewritten_body is None:
            continue
        replacements.append((function_info["brace_index"], function_info["end_index"], rewritten_body))
        transformed += 1

    if not replacements:
        vlog("cherry", "no eligible functions; no-op")
        return source_text

    content = source_text
    for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
        content = content[:start] + replacement + content[end:]
    vlog("cherry", f"obfuscated_functions={transformed}, bytes {len(source_text)} -> {len(content)}")
    return content
