import re

import config
from util import (
    extract_parameter_name,
    find_matching_brace,
    is_escaped,
    iter_function_definitions,
    split_parameter_list,
    split_top_level_statements,
    vlog,
)


FALSE_CONDITION_RE = re.compile(r"^\(*\s*(?:false|0[xX]0+|0[bB]0+|0+)\s*\)*$")
TRUE_CONDITION_RE = re.compile(r"^\(*\s*(?:true|1)\s*\)*$")
TERMINAL_PREFIXES = ("return", "throw", "break", "continue", "goto")


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


def find_matching_paren_backward(source_text, close_paren_index):
    depth = 1
    cursor = close_paren_index - 1
    while cursor >= 0 and depth > 0:
        char = source_text[cursor]
        if char == ")":
            depth += 1
        elif char == "(":
            depth -= 1
        cursor -= 1
    return cursor + 1 if depth == 0 else None


def is_constant_false(condition_text):
    return bool(FALSE_CONDITION_RE.fullmatch(condition_text.strip()))


def is_constant_true(condition_text):
    return bool(TRUE_CONDITION_RE.fullmatch(condition_text.strip()))


def clean_braced_block(block_text):
    stripped = block_text.strip()
    if not stripped.startswith("{"):
        return block_text
    end_index = find_matching_brace(stripped, 0)
    if end_index is None:
        return block_text
    inner = stripped[1:end_index - 1]
    cleaned_inner, removed = clean_block(inner)
    return "{\n" + cleaned_inner + "\n}", removed


def split_if_parts(statement):
    stripped = statement.strip()
    if not stripped.startswith("if"):
        return None
    paren_index = stripped.find("(")
    if paren_index == -1:
        return None
    paren_end = find_matching_paren(stripped, paren_index)
    if paren_end is None:
        return None
    condition_text = stripped[paren_index + 1:paren_end - 1]
    tail = stripped[paren_end:].lstrip()
    if not tail.startswith("{"):
        return None
    then_end = find_matching_brace(tail, 0)
    if then_end is None:
        return None
    then_block = tail[:then_end]
    remainder = tail[then_end:].strip()
    else_block = None
    if remainder.startswith("else"):
        else_tail = remainder[4:].lstrip()
        if else_tail.startswith("{"):
            else_end = find_matching_brace(else_tail, 0)
            if else_end is not None:
                else_block = else_tail[:else_end]
    return condition_text, then_block, else_block


def split_while_parts(statement):
    stripped = statement.strip()
    if not stripped.startswith("while"):
        return None
    paren_index = stripped.find("(")
    if paren_index == -1:
        return None
    paren_end = find_matching_paren(stripped, paren_index)
    if paren_end is None:
        return None
    condition_text = stripped[paren_index + 1:paren_end - 1]
    tail = stripped[paren_end:].lstrip()
    if not tail.startswith("{"):
        return None
    body_end = find_matching_brace(tail, 0)
    if body_end is None:
        return None
    return condition_text, tail[:body_end]


def clean_statement(statement):
    stripped = statement.strip()
    if not stripped:
        return "", 0

    if stripped.startswith("{"):
        return clean_braced_block(stripped)

    if stripped.startswith("if"):
        parsed = split_if_parts(stripped)
        if parsed is None:
            return statement, 0
        condition_text, then_block, else_block = parsed
        cleaned_then, removed_then = clean_braced_block(then_block)
        removed = removed_then
        cleaned_else = None
        if else_block is not None:
            cleaned_else, removed_else = clean_braced_block(else_block)
            removed += removed_else

        if is_constant_false(condition_text):
            if cleaned_else is not None:
                return cleaned_else, removed + 1
            return "", removed + 1

        if is_constant_true(condition_text):
            return cleaned_then, removed + (1 if cleaned_else is not None else 0)

        rebuilt = f"if ({condition_text}) {cleaned_then}"
        if cleaned_else is not None:
            rebuilt += f" else {cleaned_else}"
        return rebuilt, removed

    if stripped.startswith("while"):
        parsed = split_while_parts(stripped)
        if parsed is None:
            return statement, 0
        condition_text, body_block = parsed
        if is_constant_false(condition_text):
            return "", 1
        cleaned_body, removed = clean_braced_block(body_block)
        if is_constant_true(condition_text):
            return f"while ({condition_text}) {cleaned_body}", removed
        return f"while ({condition_text}) {cleaned_body}", removed

    return statement, 0


def clean_block(body_text):
    statements = split_top_level_statements(body_text)
    if not statements:
        return body_text.strip(), 0

    cleaned_statements = []
    removed = 0
    terminated = False
    index = 0
    while index < len(statements):
        statement = statements[index]
        stripped = statement.strip()
        if not stripped:
            index += 1
            continue
        if stripped.startswith("if") and index + 1 < len(statements):
            next_statement = statements[index + 1].strip()
            if next_statement.startswith("else"):
                statement = statement.rstrip() + " " + next_statement
                stripped = statement.strip()
                index += 1
        if terminated:
            removed += 1
            index += 1
            continue

        cleaned_statement, removed_in_statement = clean_statement(statement)
        removed += removed_in_statement
        if cleaned_statement.strip():
            cleaned_statements.append(cleaned_statement.strip())
            if cleaned_statement.lstrip().startswith(TERMINAL_PREFIXES):
                terminated = True
        index += 1

    return "\n".join(cleaned_statements), removed


def strip_unused_parameter_name(param_text):
    match = re.search(r"(?P<name>[A-Za-z_]\w*)\s*(?P<array>\[[^\]]*\])?\s*$", param_text)
    if match is None:
        return param_text
    start, end = match.span("name")
    array_suffix = match.group("array") or ""
    return (param_text[:start] + array_suffix).rstrip()


def remove_unused_parameter_names(source_text):
    replacements = []
    removed = 0

    for function_info in iter_function_definitions(source_text):
        close_paren = function_info["brace_index"] - 1
        while close_paren >= 0 and source_text[close_paren].isspace():
            close_paren -= 1
        if close_paren < 0 or source_text[close_paren] != ")":
            continue
        open_paren = find_matching_paren_backward(source_text, close_paren)
        if open_paren is None:
            continue

        params_text = source_text[open_paren + 1:close_paren]
        param_parts = split_parameter_list(params_text)
        if not param_parts:
            continue

        updated_parts = []
        changed = False
        for part in param_parts:
            param_name = extract_parameter_name(part)
            if param_name is None or "=" in part:
                updated_parts.append(part)
                continue
            if re.search(rf"\b{re.escape(param_name)}\b", function_info["body_text"]):
                updated_parts.append(part)
                continue
            updated_parts.append(strip_unused_parameter_name(part))
            removed += 1
            changed = True

        if not changed:
            continue

        replacement_text = ", ".join(updated_parts)
        if function_info["name"] == "main":
            remaining_names = [extract_parameter_name(part) for part in updated_parts]
            if all(name is None for name in remaining_names):
                replacement_text = "void"

        replacements.append((open_paren + 1, close_paren, replacement_text))

    if not replacements:
        return source_text, 0

    content = source_text
    for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
        content = content[:start] + replacement + content[end:]
    return content, removed


def remove_dead_code(source_text):
    if not config.ENABLE_DEAD_CODE_REMOVAL:
        vlog("dead_rm", "disabled")
        return source_text

    content, removed_params = remove_unused_parameter_names(source_text)
    replacements = []
    removed = removed_params
    for function_info in iter_function_definitions(content):
        cleaned_body, removed_in_body = clean_block(function_info["body_text"])
        if removed_in_body == 0:
            continue
        replacements.append(
            (
                function_info["brace_index"],
                function_info["end_index"],
                "{\n" + cleaned_body + f"\n{function_info['base_indent']}" + "}",
            )
        )
        removed += removed_in_body

    if not replacements and removed_params == 0:
        vlog("dead_rm", "no-op")
        return source_text

    cleaned = content
    for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
        cleaned = cleaned[:start] + replacement + cleaned[end:]

    vlog("dead_rm", f"removed_nodes={removed}, bytes {len(source_text)} -> {len(cleaned)}")
    return cleaned
