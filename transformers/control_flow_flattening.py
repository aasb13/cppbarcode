import random
import re

import config
import state
from util import (
    generate_barcode_name,
    has_vm_skip_marker,
    indent_block,
    is_escaped,
    looks_like_declaration,
    split_top_level_statements,
)
from util import vlog


def is_supported_for_flattening(body_text: str) -> bool:
    unsupported_markers = ("goto ", "goto\t", "try", "catch", " co_")
    return not any(marker in body_text for marker in unsupported_markers)


def flatten_function_body(body_text: str, base_indent: str) -> str | None:
    statements = split_top_level_statements(body_text)
    if len(statements) < 2:
        return None

    first_executable = None
    for index, statement in enumerate(statements):
        if not looks_like_declaration(statement):
            first_executable = index
            break

    if first_executable is None:
        return None

    declarations = statements[:first_executable]
    executable = statements[first_executable:]
    if len(executable) < 2:
        return None
    if any(looks_like_declaration(statement) for statement in executable):
        return None
    if not is_supported_for_flattening(body_text):
        return None

    state_name = generate_barcode_name(18)
    next_state_name = generate_barcode_name(18)
    jump_map_name = generate_barcode_name(18)
    fake_jump_map_name = generate_barcode_name(18)
    exit_label = generate_barcode_name(18)
    indent = base_indent + "    "
    flattened_lines: list[str] = []
    case_order = list(range(len(executable)))
    random.shuffle(case_order)
    physical_labels = {logical_index: case_order[logical_index] for logical_index in range(len(executable))}
    fake_case_order = list(range(len(executable), len(executable) * 2))
    random.shuffle(fake_case_order)
    fake_labels = {logical_index: fake_case_order[logical_index] for logical_index in range(len(executable))}
    cfg_enabled = config.ENABLE_CFG_POLLUTION
    if cfg_enabled:
        state.init_cfg_pollution_names()

    for declaration in declarations:
        flattened_lines.extend(indent_block(declaration, indent))

    flattened_lines.append(
        f"{indent}int {jump_map_name}[{len(executable)}] = {{{', '.join(str(physical_labels[index]) for index in range(len(executable)))}}};"
    )
    if cfg_enabled:
        flattened_lines.append(
            f"{indent}int {fake_jump_map_name}[{len(executable)}] = {{{', '.join(str(fake_labels[index]) for index in range(len(executable)))}}};"
        )
        flattened_lines.append(
            f"{indent}int {state_name} = {state.CFG_EDGE_HELPER_NAME}({jump_map_name}[0], {fake_jump_map_name}[0], {random.randint(0x100, 0xFFFF)}ULL);"
        )
    else:
        flattened_lines.append(f"{indent}int {state_name} = {jump_map_name}[0];")
    flattened_lines.append(f"{indent}while (true) {{")
    flattened_lines.append(f"{indent}    switch ({state_name}) {{")

    for index, statement in enumerate(executable):
        flattened_lines.append(f"{indent}    case {physical_labels[index]}: {{")
        flattened_lines.extend(indent_block(statement, f"{indent}        "))

        terminal = statement.lstrip().startswith(("return", "throw"))
        if not terminal:
            if index + 1 < len(executable):
                flattened_lines.append(f"{indent}        int {next_state_name} = {index + 1};")
                if cfg_enabled:
                    flattened_lines.append(
                        f"{indent}        {state_name} = {state.CFG_EDGE_HELPER_NAME}({jump_map_name}[{next_state_name}], {fake_jump_map_name}[{next_state_name}], {random.randint(0x100, 0xFFFF)}ULL);"
                    )
                else:
                    flattened_lines.append(f"{indent}        {state_name} = {jump_map_name}[{next_state_name}];")
                flattened_lines.append(f"{indent}        break;")
            else:
                flattened_lines.append(f"{indent}        goto {exit_label};")

        flattened_lines.append(f"{indent}    }}")

        if cfg_enabled:
            flattened_lines.append(f"{indent}    case {fake_labels[index]}: {{")
            flattened_lines.append(
                f"{indent}        volatile int {generate_barcode_name(18)} = {random.randint(0x10, 0xFF)};"
            )
            if not terminal and index + 1 < len(executable):
                flattened_lines.append(f"{indent}        int {next_state_name} = {index + 1};")
                flattened_lines.append(
                    f"{indent}        {state_name} = {state.CFG_EDGE_HELPER_NAME}({jump_map_name}[{next_state_name}], {fake_jump_map_name}[{next_state_name}], {random.randint(0x100, 0xFFFF)}ULL);"
                )
                flattened_lines.append(f"{indent}        break;")
            else:
                flattened_lines.append(f"{indent}        goto {exit_label};")
            flattened_lines.append(f"{indent}    }}")

    flattened_lines.append(f"{indent}    default: goto {exit_label};")
    flattened_lines.append(f"{indent}    }}")
    flattened_lines.append(f"{indent}}}")
    flattened_lines.append(f"{indent}{exit_label}: ;")

    return "{\n" + "\n".join(flattened_lines) + f"\n{base_indent}}}"


def apply_control_flow_flattening(source_text: str) -> str:
    """Flattens supported function bodies into a state-machine dispatcher."""
    if not config.ENABLE_CONTROL_FLOW_FLATTENING:
        vlog("cflow", "disabled")
        return source_text

    result: list[str] = []
    last_index = 0
    scan_index = 0
    flattened = 0

    while scan_index < len(source_text):
        brace_index = source_text.find("{", scan_index)
        if brace_index == -1:
            break

        header = source_text[max(0, brace_index - 300) : brace_index]
        stripped_header = header.rstrip()
        if not stripped_header.endswith(")"):
            scan_index = brace_index + 1
            continue

        open_paren_index = stripped_header.rfind("(")
        if open_paren_index == -1:
            scan_index = brace_index + 1
            continue

        prefix = stripped_header[:open_paren_index].rstrip()
        name_match = re.search(r"([A-Za-z_]\w*)\s*$", prefix)
        if not name_match or name_match.group(1) in {"if", "for", "while", "switch", "catch"}:
            scan_index = brace_index + 1
            continue
        if prefix.endswith(("namespace", "class", "struct", "enum")) or "template" in prefix:
            scan_index = brace_index + 1
            continue

        depth = 1
        cursor = brace_index + 1
        in_string = False
        in_char = False
        in_line_comment = False
        in_block_comment = False

        while cursor < len(source_text) and depth > 0:
            char = source_text[cursor]
            next_char = source_text[cursor + 1] if cursor + 1 < len(source_text) else ""

            if in_line_comment:
                if char == "\n":
                    in_line_comment = False
                cursor += 1
                continue
            if in_block_comment:
                if char == "*" and next_char == "/":
                    in_block_comment = False
                    cursor += 2
                    continue
                cursor += 1
                continue
            if in_string:
                if char == '"' and not is_escaped(source_text, cursor):
                    in_string = False
                cursor += 1
                continue
            if in_char:
                if char == "'" and not is_escaped(source_text, cursor):
                    in_char = False
                cursor += 1
                continue

            if char == "/" and next_char == "/":
                in_line_comment = True
                cursor += 2
                continue
            if char == "/" and next_char == "*":
                in_block_comment = True
                cursor += 2
                continue
            if char == '"':
                in_string = True
            elif char == "'":
                in_char = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1

            cursor += 1

        if depth != 0:
            break

        body_text = source_text[brace_index + 1 : cursor - 1]
        line_start = source_text.rfind("\n", 0, brace_index) + 1
        if has_vm_skip_marker(source_text, line_start):
            scan_index = cursor
            continue
        leading_segment = source_text[line_start:brace_index]
        base_indent = re.match(r"\s*", leading_segment).group(0)
        flattened_body = flatten_function_body(body_text, base_indent)
        if flattened_body is None:
            scan_index = brace_index + 1
            continue

        result.append(source_text[last_index:brace_index])
        result.append(flattened_body)
        last_index = cursor
        scan_index = cursor
        flattened += 1

    result.append(source_text[last_index:])
    out = "".join(result)
    vlog("cflow", f"flattened_functions={flattened}, bytes {len(source_text)} -> {len(out)}")
    return out
