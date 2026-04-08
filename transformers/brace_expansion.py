import config
from util import is_escaped, vlog


CONTROL_KEYWORDS = ("if", "for", "while")


def _skip_ws(source_text, index):
    while index < len(source_text) and source_text[index].isspace():
        index += 1
    return index


def _match_keyword_at(source_text, index, keyword):
    end = index + len(keyword)
    if not source_text.startswith(keyword, index):
        return False
    if index > 0 and (source_text[index - 1].isalnum() or source_text[index - 1] == "_"):
        return False
    if end < len(source_text) and (source_text[end].isalnum() or source_text[end] == "_"):
        return False
    return True


def _find_matching_paren(source_text, paren_index):
    depth = 1
    cursor = paren_index + 1
    in_string = False
    in_char = False

    while cursor < len(source_text) and depth > 0:
        char = source_text[cursor]
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


def _find_matching_brace(source_text, brace_index):
    depth = 1
    cursor = brace_index + 1
    in_string = False
    in_char = False

    while cursor < len(source_text) and depth > 0:
        char = source_text[cursor]
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
        if char == '"':
            in_string = True
        elif char == "'":
            in_char = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        cursor += 1

    return cursor if depth == 0 else None


def _find_simple_statement_end(source_text, start_index):
    cursor = start_index
    paren_depth = 0
    bracket_depth = 0
    brace_depth = 0
    in_string = False
    in_char = False

    while cursor < len(source_text):
        char = source_text[cursor]
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
        if char == '"':
            in_string = True
        elif char == "'":
            in_char = True
        elif char == "(":
            paren_depth += 1
        elif char == ")":
            paren_depth = max(0, paren_depth - 1)
        elif char == "[":
            bracket_depth += 1
        elif char == "]":
            bracket_depth = max(0, bracket_depth - 1)
        elif char == "{":
            brace_depth += 1
        elif char == "}":
            if paren_depth == 0 and bracket_depth == 0 and brace_depth == 0:
                return cursor
            brace_depth = max(0, brace_depth - 1)
        elif char == ";" and paren_depth == 0 and bracket_depth == 0 and brace_depth == 0:
            return cursor + 1
        cursor += 1
    return cursor


def _find_statement_end(source_text, start_index):
    start_index = _skip_ws(source_text, start_index)
    if start_index >= len(source_text):
        return start_index

    if source_text[start_index] == "{":
        return _find_matching_brace(source_text, start_index)

    if _match_keyword_at(source_text, start_index, "else"):
        body_start = _skip_ws(source_text, start_index + 4)
        return _find_statement_end(source_text, body_start)

    if _match_keyword_at(source_text, start_index, "do"):
        body_start = _skip_ws(source_text, start_index + 2)
        body_end = _find_statement_end(source_text, body_start)
        while_start = _skip_ws(source_text, body_end)
        if _match_keyword_at(source_text, while_start, "while"):
            paren_start = _skip_ws(source_text, while_start + 5)
            if paren_start < len(source_text) and source_text[paren_start] == "(":
                paren_end = _find_matching_paren(source_text, paren_start)
                if paren_end is not None:
                    semi_end = _find_simple_statement_end(source_text, paren_end)
                    return semi_end
        return body_end

    for keyword in CONTROL_KEYWORDS:
        if _match_keyword_at(source_text, start_index, keyword):
            paren_start = _skip_ws(source_text, start_index + len(keyword))
            if paren_start >= len(source_text) or source_text[paren_start] != "(":
                return start_index
            paren_end = _find_matching_paren(source_text, paren_start)
            if paren_end is None:
                return start_index
            body_start = _skip_ws(source_text, paren_end)
            body_end = _find_statement_end(source_text, body_start)
            if keyword == "if":
                else_start = _skip_ws(source_text, body_end)
                if _match_keyword_at(source_text, else_start, "else"):
                    else_body_start = _skip_ws(source_text, else_start + 4)
                    return _find_statement_end(source_text, else_body_start)
            return body_end

    return _find_simple_statement_end(source_text, start_index)


def _wrap_control_body(source_text, keyword_index, keyword):
    paren_start = _skip_ws(source_text, keyword_index + len(keyword))
    if paren_start >= len(source_text) or source_text[paren_start] != "(":
        return None
    paren_end = _find_matching_paren(source_text, paren_start)
    if paren_end is None:
        return None

    body_start = _skip_ws(source_text, paren_end)
    if body_start >= len(source_text) or source_text[body_start] == "{":
        return None

    body_end = _find_statement_end(source_text, body_start)
    if body_end is None or body_end <= body_start:
        return None

    body_text = source_text[body_start:body_end].strip()
    if not body_text:
        return None
    return body_start, body_end, "{\n" + body_text + "\n}"


def _wrap_else_body(source_text, else_index):
    body_start = _skip_ws(source_text, else_index + 4)
    if body_start >= len(source_text) or source_text[body_start] == "{":
        return None
    body_end = _find_statement_end(source_text, body_start)
    if body_end is None or body_end <= body_start:
        return None
    body_text = source_text[body_start:body_end].strip()
    if not body_text:
        return None
    return body_start, body_end, "{\n" + body_text + "\n}"


def expand_inline_control_bodies(source_text):
    if not getattr(config, "ENABLE_CONTROL_BODY_BRACING", True):
        vlog("brace_expand", "disabled")
        return source_text

    replacements = []
    cursor = 0
    in_string = False
    in_char = False

    while cursor < len(source_text):
        char = source_text[cursor]
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
        if char == '"':
            in_string = True
            cursor += 1
            continue
        if char == "'":
            in_char = True
            cursor += 1
            continue

        replacement = None
        for keyword in CONTROL_KEYWORDS:
            if _match_keyword_at(source_text, cursor, keyword):
                replacement = _wrap_control_body(source_text, cursor, keyword)
                break
        if replacement is None and _match_keyword_at(source_text, cursor, "else"):
            replacement = _wrap_else_body(source_text, cursor)

        if replacement is not None:
            replacements.append(replacement)
            cursor = replacement[1]
            continue
        cursor += 1

    if not replacements:
        vlog("brace_expand", "no inline bodies found; no-op")
        return source_text

    content = source_text
    for start, end, new_val in sorted(replacements, key=lambda item: item[0], reverse=True):
        content = content[:start] + new_val + content[end:]
    vlog("brace_expand", f"wrapped_bodies={len(replacements)}, bytes {len(source_text)} -> {len(content)}")
    return content
