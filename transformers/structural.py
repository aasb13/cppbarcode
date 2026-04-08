import random
import re

import config
import state
from util import (
    extract_declared_local_name,
    extract_parameter_name,
    find_matching_brace,
    generate_barcode_name,
    has_vm_skip_marker,
    is_performance_sensitive_function,
    indent_block,
    is_escaped,
    iter_function_definitions,
    looks_like_declaration,
    replace_identifier_text,
    split_parameter_list,
    split_top_level_statements,
    vlog,
)


CANONICAL_FOR_PATTERN = re.compile(
    r"^\s*(?P<type>.+?)\s+(?P<var>[A-Za-z_]\w*)\s*=\s*(?P<start>.+?)\s*;\s*"
    r"(?P=var)\s*<\s*(?P<bound>.+?)\s*;\s*(?:\+\+\s*(?P=var)|(?P=var)\s*\+\+)\s*$",
    re.DOTALL,
)


def build_cloned_body_variant(body_text, variant_index):
    cloned = body_text
    local_renames = {}
    for statement in split_top_level_statements(body_text):
        if not looks_like_declaration(statement):
            continue
        local_name = extract_declared_local_name(statement)
        if local_name and local_name not in local_renames:
            local_renames[local_name] = generate_barcode_name(16)

    for original_name, obfuscated_name in local_renames.items():
        cloned = replace_identifier_text(cloned, original_name, obfuscated_name)

    if variant_index % 2 == 1:
        cloned = re.sub(
            r"\breturn\s+([^;]+);",
            lambda match: f"return (({match.group(1).strip()}));",
            cloned,
        )

    statements = split_top_level_statements(cloned)
    first_executable = None
    for index, statement in enumerate(statements):
        if not looks_like_declaration(statement):
            first_executable = index
            break
    if first_executable is not None:
        decls = statements[:first_executable]
        execs = statements[first_executable:]
        if len(decls) > 1 and all(execs):
            random.shuffle(decls)
            cloned = "\n".join(decls + execs)

    return cloned


def is_statement_reorder_safe(statement):
    stripped = statement.strip()
    if not stripped:
        return False
    if re.search(r"\b(?:return|goto|co_return|co_yield)\b", stripped):
        return False
    if re.match(r"^[A-Za-z_]\w*\s*:", stripped):
        return False
    if stripped.endswith(";"):
        expression_patterns = (
            r"^[A-Za-z_]\w*(?:\[[^\]]+\])?\s*(?:[+\-*/%^&|]|<<|>>)?=\s*.+;$",
            r"^(?:\+\+|--)?[A-Za-z_]\w*(?:\[[^\]]+\])?(?:\+\+|--)?;$",
            r"^[A-Za-z_]\w*(?:\s*(?:<<|>>)\s*.+)+;$",
            r"^[A-Za-z_]\w*(?:::\w+)*(?:<[^;{}]+>)?\s*\(.*\)\s*;$",
        )
        if any(re.match(pattern, stripped, re.DOTALL) for pattern in expression_patterns):
            return True
    if looks_like_declaration(stripped):
        return False
    return True


def build_reordered_statement_block(statements, base_indent):
    inner_indent = base_indent + "    "
    helper_names = [generate_barcode_name(18) for _ in statements]
    declaration_order = list(range(len(statements)))
    random.shuffle(declaration_order)
    lines = [f"{inner_indent}{{"]

    for physical_index in declaration_order:
        helper_name = helper_names[physical_index]
        lines.append(f"{inner_indent}    auto {helper_name} = [&]() {{")
        lines.extend(indent_block(statements[physical_index], f"{inner_indent}        "))
        lines.append(f"{inner_indent}    }};")

    for helper_name in helper_names:
        lines.append(f"{inner_indent}    {helper_name}();")

    lines.append(f"{inner_indent}}}")
    return "\n".join(lines)


def reorder_function_body(body_text, base_indent):
    statements = split_top_level_statements(body_text)
    if len(statements) < 3:
        return None

    best_run = None
    current_start = None
    for index, statement in enumerate(statements):
        if is_statement_reorder_safe(statement):
            if current_start is None:
                current_start = index
            continue
        if current_start is not None:
            run = (current_start, index)
            if best_run is None or (run[1] - run[0]) > (best_run[1] - best_run[0]):
                best_run = run
            current_start = None
    if current_start is not None:
        run = (current_start, len(statements))
        if best_run is None or (run[1] - run[0]) > (best_run[1] - best_run[0]):
            best_run = run

    if best_run is None or (best_run[1] - best_run[0]) < 2:
        return None

    run_start, run_end = best_run
    reorderable = statements[run_start:run_end]

    rebuilt = []
    for statement in statements[:run_start]:
        rebuilt.extend(indent_block(statement, base_indent + "    "))
    rebuilt.append(build_reordered_statement_block(reorderable, base_indent))
    for statement in statements[run_end:]:
        rebuilt.extend(indent_block(statement, base_indent + "    "))

    return "{\n" + "\n".join(rebuilt) + f"\n{base_indent}}}"


def apply_statement_reordering(source_text):
    if not config.ENABLE_STATEMENT_REORDERING:
        vlog("stmt_reorder", "disabled")
        return source_text

    replacements = []
    reordered = 0
    for function_info in iter_function_definitions(source_text):
        if function_info.get("skip_structural"):
            continue
        rewritten_body = reorder_function_body(function_info["body_text"], function_info["base_indent"])
        if rewritten_body is None:
            continue
        replacements.append((function_info["brace_index"], function_info["end_index"], rewritten_body))
        reordered += 1

    if not replacements:
        vlog("stmt_reorder", "no eligible functions; no-op")
        return source_text

    content = source_text
    for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
        content = content[:start] + replacement + content[end:]
    vlog("stmt_reorder", f"reordered_functions={reordered}, bytes {len(source_text)} -> {len(content)}")
    return content


def is_simple_reduction_statement(statement):
    stripped = statement.strip()
    if not stripped.endswith(";"):
        return False
    return bool(re.match(r"^[A-Za-z_]\w*(?:\s*[\)\]])?\s*(?:\+=|\^=|\|=|&=|\*=)\s*.+;$", stripped))


def is_remap_safe_loop_body(body_text, var_name):
    if re.search(r"\b(?:break|continue|goto|return)\b", body_text):
        return False
    subscript_pattern = re.compile(rf"\[[^\]\n]*\b{re.escape(var_name)}\b[^\]]*\]")
    if not subscript_pattern.search(body_text):
        return False
    stripped = subscript_pattern.sub("[]", body_text)
    if re.search(rf"\b{re.escape(var_name)}\b", stripped):
        return False
    statements = split_top_level_statements(body_text)
    return bool(statements) and all(is_simple_reduction_statement(statement) for statement in statements)


def build_loop_idiom_block(type_text, var_name, start_expr, bound_expr, body_text, base_indent, remap_index):
    inner_indent = base_indent + "    "
    start_name = generate_barcode_name(18)
    bound_name = generate_barcode_name(18)
    span_name = generate_barcode_name(18)
    iter_name = generate_barcode_name(18)
    mapped_name = generate_barcode_name(18)
    lines = [
        f"{inner_indent}{{",
        f"{inner_indent}    auto {start_name} = ({start_expr});",
        f"{inner_indent}    auto {bound_name} = ({bound_expr});",
        f"{inner_indent}    if ({start_name} < {bound_name}) {{",
        f"{inner_indent}        auto {span_name} = {bound_name} - {start_name};",
        f"{inner_indent}        decltype({span_name}) {iter_name} = {span_name};",
        f"{inner_indent}        while ({iter_name}-- > 0) {{",
    ]
    if remap_index:
        salt_value = random.randint(1, 0xFF)
        lines.append(
            f"{inner_indent}            auto {mapped_name} = {start_name} + ((({iter_name}) + static_cast<decltype({span_name})>({salt_value})) % {span_name});"
        )
        lines.append(
            f"{inner_indent}            {type_text} {var_name} = static_cast<{type_text}>({mapped_name});"
        )
    else:
        lines.append(
            f"{inner_indent}            {type_text} {var_name} = static_cast<{type_text}>({start_name} + (({span_name} - static_cast<decltype({span_name})>(1)) - {iter_name}));"
        )
    lines.extend(indent_block(body_text, f"{inner_indent}            "))
    lines.append(f"{inner_indent}        }}")
    lines.append(f"{inner_indent}    }}")
    lines.append(f"{inner_indent}}}")
    return "\n".join(lines)


def parse_canonical_for_loop(statement):
    stripped = statement.strip()
    if not stripped.startswith("for"):
        return None

    header_start = stripped.find("(")
    if header_start == -1:
        return None
    header_end = find_matching_paren(stripped, header_start)
    if header_end is None:
        return None

    header_text = stripped[header_start + 1 : header_end - 1]
    match = CANONICAL_FOR_PATTERN.fullmatch(header_text)
    if match is None:
        return None

    body_suffix = stripped[header_end:].strip()
    if not body_suffix.startswith("{"):
        return None
    body_end = find_matching_brace(body_suffix, 0)
    if body_end is None:
        return None
    body_text = body_suffix[1 : body_end - 1].strip()
    return {
        "type_text": match.group("type").strip(),
        "var_name": match.group("var"),
        "start_expr": match.group("start").strip(),
        "bound_expr": match.group("bound").strip(),
        "body_text": body_text,
    }


def rewrite_loop_statements(body_text, base_indent):
    statements = split_top_level_statements(body_text)
    if not statements:
        return None, 0, 0

    rebuilt = []
    transformed = 0
    remapped = 0
    for statement in statements:
        loop_info = parse_canonical_for_loop(statement)
        if loop_info is None:
            rebuilt.extend(indent_block(statement, base_indent + "    "))
            continue
        if re.search(r"\b(?:break|continue|goto|return)\b", loop_info["body_text"]):
            rebuilt.extend(indent_block(statement, base_indent + "    "))
            continue

        remap_index = is_remap_safe_loop_body(loop_info["body_text"], loop_info["var_name"])
        rebuilt.append(
            build_loop_idiom_block(
                loop_info["type_text"],
                loop_info["var_name"],
                loop_info["start_expr"],
                loop_info["bound_expr"],
                loop_info["body_text"],
                base_indent,
                remap_index,
            )
        )
        transformed += 1
        if remap_index:
            remapped += 1

    if transformed == 0:
        return None, 0, 0
    return "{\n" + "\n".join(rebuilt) + f"\n{base_indent}}}", transformed, remapped


def find_matching_paren(source_text, paren_index):
    depth = 1
    cursor = paren_index + 1
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
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1

        cursor += 1

    return cursor if depth == 0 else None


def apply_loop_idiom_transformation(source_text):
    if not config.ENABLE_LOOP_IDIOM_TRANSFORMATION:
        vlog("loop_idiom", "disabled")
        return source_text

    replacements = []
    transformed_loops = 0
    remapped_loops = 0
    for function_info in iter_function_definitions(source_text):
        if function_info.get("skip_structural"):
            continue
        rewritten_body, transformed, remapped = rewrite_loop_statements(
            function_info["body_text"],
            function_info["base_indent"],
        )
        if rewritten_body is None:
            continue
        replacements.append((function_info["brace_index"], function_info["end_index"], rewritten_body))
        transformed_loops += transformed
        remapped_loops += remapped

    if not replacements:
        vlog("loop_idiom", "no eligible loops; no-op")
        return source_text

    content = source_text
    for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
        content = content[:start] + replacement + content[end:]
    vlog(
        "loop_idiom",
        f"transformed_loops={transformed_loops}, remapped_loops={remapped_loops}, bytes {len(source_text)} -> {len(content)}",
    )
    return content


def build_clone_dispatcher(function_name, signature_text, params_text, clone_names, base_indent):
    state.init_cfg_pollution_names()
    param_names = [
        name
        for name in (extract_parameter_name(part) for part in split_parameter_list(params_text))
        if name
    ]
    if len(param_names) != len(
        [part for part in split_parameter_list(params_text) if part not in {"void", "..."}]
    ):
        return None

    inner_indent = base_indent + "    "
    args_expr = ", ".join(param_names)
    selector_name = generate_barcode_name(18)
    salt_value = random.randint(0x100, 0xFFFF)
    lines = [
        f"{signature_text} {{",
        f"{inner_indent}int {selector_name} = {state.CFG_CLONE_SELECT_HELPER_NAME}({len(clone_names)}, {salt_value}ULL);",
    ]
    for index, clone_name in enumerate(clone_names[:-1]):
        call_expr = f"{clone_name}({args_expr})"
        lines.append(f"{inner_indent}if ({selector_name} == {index}) {{")
        lines.append(f"{inner_indent}    return {call_expr};")
        lines.append(f"{inner_indent}}}")
    lines.append(f"{inner_indent}return {clone_names[-1]}({args_expr});")
    lines.append(f"{base_indent}}}")
    return "\n".join(lines)


def is_supported_for_flattening(body_text):
    unsupported_markers = ("goto ", "goto\t", "try", "catch", " co_")
    return not any(marker in body_text for marker in unsupported_markers)


def _extract_flatten_return_type(prefix, function_name):
    name_index = prefix.rfind(function_name)
    if name_index == -1:
        return None
    return_type = prefix[:name_index].strip()
    if not return_type:
        return None
    tokens = return_type.split()
    while tokens and tokens[0] in {
        "static", "inline", "constexpr", "virtual", "friend", "extern", "__attribute__",
    }:
        tokens.pop(0)
    cleaned = " ".join(tokens).strip()
    if not cleaned:
        return None
    if "(" in cleaned or ")" in cleaned or "auto" == cleaned or cleaned.endswith("&") or cleaned.endswith("&&"):
        return None
    return cleaned


def flatten_function_body(body_text, base_indent, return_type=None):
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
    flattened_lines = []
    case_order = list(range(len(executable)))
    random.shuffle(case_order)
    physical_labels = {
        logical_index: case_order[logical_index] for logical_index in range(len(executable))
    }
    fake_case_order = list(range(len(executable), len(executable) * 2))
    random.shuffle(fake_case_order)
    fake_labels = {
        logical_index: fake_case_order[logical_index] for logical_index in range(len(executable))
    }
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
    flattened_lines.append(f"{indent}{exit_label}:")
    if return_type is None or return_type == "void":
        flattened_lines.append(f"{indent}    ;")
    else:
        flattened_lines.append(f"{indent}    return {return_type}{{}};")

    return "{\n" + "\n".join(flattened_lines) + f"\n{base_indent}}}"


def apply_control_flow_flattening(source_text):
    """Flattens supported function bodies into a state-machine dispatcher."""
    if not config.ENABLE_CONTROL_FLOW_FLATTENING:
        vlog("cflow", "disabled")
        return source_text

    result = []
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
        if is_performance_sensitive_function(stripped_header, body_text):
            scan_index = cursor
            continue
        return_type = _extract_flatten_return_type(prefix, name_match.group(1))
        if return_type is None and prefix.strip() != "void":
            scan_index = cursor
            continue
        leading_segment = source_text[line_start:brace_index]
        base_indent = re.match(r"\s*", leading_segment).group(0)
        flattened_body = flatten_function_body(body_text, base_indent, return_type=return_type)
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


def apply_function_cloning(source_text):
    """Clones eligible free functions and routes calls through an opaque dispatcher."""
    if not config.ENABLE_FUNCTION_CLONING:
        vlog("cloning", "disabled")
        return source_text

    replacements = []
    cloned_functions = 0
    emitted_clones = 0
    for function_info in iter_function_definitions(source_text):
        if function_info.get("skip_structural"):
            continue
        function_name = function_info["name"]
        if function_name == "main":
            continue
        if function_info["prefix"].endswith("operator"):
            continue

        brace_index = function_info["brace_index"]
        line_start = source_text.rfind("\n", 0, brace_index) + 1
        signature_text = source_text[line_start:brace_index].rstrip()
        if not signature_text or "\n#" in signature_text or function_name not in signature_text:
            continue
        if re.search(r"\)\s*(?:const|noexcept|->|\[\[|requires)", signature_text):
            continue
        if re.search(rf"\b{re.escape(function_name)}\s*\(", function_info["body_text"]):
            continue

        params_text = function_info["params_text"][:-1]
        param_parts = split_parameter_list(params_text)
        if any(part == "..." for part in param_parts):
            continue

        clone_count = random.randint(2, 4)
        clone_names = [generate_barcode_name(18) for _ in range(clone_count)]
        clone_variants = []
        for variant_index, clone_name in enumerate(clone_names):
            variant_body = build_cloned_body_variant(function_info["body_text"], variant_index)
            clone_signature = re.sub(
                rf"\b{re.escape(function_name)}\b(?=\s*\()",
                clone_name,
                signature_text,
                count=1,
            )
            clone_variants.append(
                f"__attribute__((unused, noinline, noipa)) {clone_signature}\n{function_info['base_indent']}{{{variant_body}\n{function_info['base_indent']}}}"
            )
            emitted_clones += 1

        dispatcher = build_clone_dispatcher(
            function_name,
            signature_text,
            params_text,
            clone_names,
            function_info["base_indent"],
        )
        if dispatcher is None:
            continue

        replacement = "\n\n".join(clone_variants + [dispatcher])
        replacements.append((line_start, function_info["end_index"], replacement))
        cloned_functions += 1

    if not replacements:
        vlog("cloning", "no eligible functions; no-op")
        return source_text

    content = source_text
    for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
        content = content[:start] + replacement + content[end:]
    vlog(
        "cloning",
        f"cloned_functions={cloned_functions}, emitted_clones={emitted_clones}, bytes {len(source_text)} -> {len(content)}",
    )
    return content
