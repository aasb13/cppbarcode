import random
import re

import config
from transformers.structural import _extract_flatten_return_type
from util import (
    generate_barcode_name,
    indent_block,
    iter_function_definitions,
    looks_like_declaration,
    split_top_level_statements,
    vlog,
)


def is_throw_flow_candidate(statement):
    stripped = statement.strip()
    if not stripped:
        return False
    if looks_like_declaration(stripped):
        return False
    if re.match(r"^[A-Za-z_]\w*\s*:", stripped):
        return False
    if stripped.startswith(("try", "catch")):
        return False
    return True


def _build_disruptive_dead_transfer(indent, return_type=None):
    entry_label = generate_barcode_name(18)
    exit_label = generate_barcode_name(18)
    lines = [
        f"{indent}goto {entry_label};",
    ]
    if return_type == "void":
        lines.append(f"{indent}return;")
    elif return_type is not None:
        lines.append(f"{indent}return {return_type}{{}};")
    lines.extend(
        [
            f"{indent}{entry_label}:",
            f"{indent}goto {exit_label};",
            f"{indent}{exit_label}:",
            f"{indent}throw {random.randint(0x10, 0xFFFF)};",
        ]
    )
    return lines


def _build_dead_throw_flow_block(statement, base_indent, return_type=None):
    indent = base_indent + "    "
    key_name = generate_barcode_name(18)
    sink_name = generate_barcode_name(18)
    throw_value = random.randint(0x10, 0xFFFF)
    gate_name = generate_barcode_name(18)
    lines = [
        f"{indent}try {{",
        f"{indent}    int {key_name} = (({throw_value} ^ {throw_value}) & 0);",
        f"{indent}    if ({key_name} != 0) throw {throw_value};",
        f"{indent}    int {gate_name} = ({key_name} & 0);",
        f"{indent}    if ({gate_name} != 0) {{",
    ]
    lines.extend(_build_disruptive_dead_transfer(f"{indent}        ", return_type))
    lines.append(f"{indent}    }}")
    lines.extend(indent_block(statement, f"{indent}    "))
    lines.append(f"{indent}}} catch (...) {{")
    lines.append(f"{indent}    volatile int {sink_name} = {random.randint(0x10, 0xFF)};")
    lines.append(f"{indent}    (void){sink_name};")
    lines.append(f"{indent}}}")
    return "\n".join(lines)


def _build_live_throw_flow_block(statement, base_indent):
    indent = base_indent + "    "
    key_name = generate_barcode_name(18)
    lines = [
        f"{indent}try {{",
        f"{indent}    int {key_name} = (({random.randint(0x10, 0xFFFF)} ^ {random.randint(0x10, 0xFFFF)}) | 1);",
        f"{indent}    if ({key_name} != 0) throw {random.randint(0x10, 0xFFFF)};",
        f"{indent}}} catch (...) {{",
    ]
    lines.extend(indent_block(statement, f"{indent}    "))
    lines.append(f"{indent}}}")
    return "\n".join(lines)


def build_throw_flow_body(body_text, base_indent, return_type=None):
    statements = split_top_level_statements(body_text)
    if len(statements) < 2:
        return None
    if "try" in body_text or "catch" in body_text:
        return None

    candidate_indexes = [
        index for index, statement in enumerate(statements)
        if is_throw_flow_candidate(statement)
    ]
    if not candidate_indexes:
        return None

    mutate_count = min(len(candidate_indexes), random.randint(1, 2))
    selected = set(random.sample(candidate_indexes, mutate_count))
    rebuilt = []
    changed = 0
    for index, statement in enumerate(statements):
        if index not in selected:
            rebuilt.extend(indent_block(statement, base_indent + "    "))
            continue
        if random.random() > 0.45:
            rebuilt.append(_build_live_throw_flow_block(statement, base_indent))
        else:
            rebuilt.append(_build_dead_throw_flow_block(statement, base_indent, return_type))
        changed += 1

    if changed == 0:
        return None
    return "{\n" + "\n".join(rebuilt) + f"\n{base_indent}}}"


def apply_throw_flow_obfuscation(source_text):
    if not config.ENABLE_THROW_FLOW_OBFUSCATION:
        vlog("throw", "disabled")
        return source_text

    replacements = []
    transformed = 0
    for function_info in iter_function_definitions(source_text):
        if function_info.get("skip_structural"):
            continue
        return_type = _extract_flatten_return_type(function_info["prefix"], function_info["name"])
        if return_type is None and function_info["prefix"].strip() == "void":
            return_type = "void"
        rewritten_body = build_throw_flow_body(
            function_info["body_text"],
            function_info["base_indent"],
            return_type=return_type,
        )
        if rewritten_body is None:
            continue
        replacements.append((function_info["brace_index"], function_info["end_index"], rewritten_body))
        transformed += 1

    if not replacements:
        vlog("throw", "no eligible functions; no-op")
        return source_text

    content = source_text
    for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
        content = content[:start] + replacement + content[end:]
    vlog("throw", f"obfuscated_functions={transformed}, bytes {len(source_text)} -> {len(content)}")
    return content
