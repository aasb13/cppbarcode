import os
import random
import re
import textwrap
from bisect import bisect_right
from functools import lru_cache

import clang.cindex
import config


INTEGER_LITERAL_RE = re.compile(
    r"^(?P<body>(?:0[xX][0-9a-fA-F']+)|(?:0[bB][01']+)|(?:0[0-7']*)|(?:[1-9][0-9']*|0))(?P<suffix>(?:[uU](?:ll|LL|l|L)?|(?:ll|LL|l|L)[uU]?|z[uU]?|[uU]z)?)$"
)
FLOAT_LITERAL_RE = re.compile(
    r"^(?P<body>(?:(?:\d+\.\d*|\.\d+|\d+[eE][+-]?\d+|\d+\.\d*[eE][+-]?\d+|\.\d+[eE][+-]?\d+)))(?P<suffix>[fFlL]?)$"
)
IDENTIFIER_RE = re.compile(r"[A-Za-z_]\w*(?:::\w+)*(?:<[^;=(){}]+>)?")
CPP_KEYWORDS = {
    "alignas", "alignof", "asm", "auto", "bool", "break", "case", "catch", "char",
    "class", "const", "constexpr", "const_cast", "continue", "decltype", "default",
    "delete", "do", "double", "else", "enum", "explicit", "export", "extern",
    "false", "float", "for", "friend", "goto", "if", "inline", "int", "long",
    "mutable", "namespace", "new", "noexcept", "nullptr", "operator", "private",
    "protected", "public", "register", "reinterpret_cast", "return", "short",
    "signed", "sizeof", "static", "static_assert", "static_cast", "struct", "switch",
    "template", "this", "throw", "true", "try", "typedef", "typeid", "typename",
    "union", "unsigned", "using", "virtual", "void", "volatile", "while",
}
FUNCTION_HEADER_SCAN_WINDOW = 300
FUNCTION_LIKE_SKIP_NAMES = {"if", "for", "while", "switch", "catch"}
VM_SKIP_MARKER = "/*__VM_SKIP__*/"
CHERRY_SKIP_MARKER = "/*__CHERRY_SKIP__*/"

def vlog(tag: str, message: str) -> None:
    """Verbose logger controlled by config.VERBOSE_LOGGING."""
    if getattr(config, "VERBOSE_LOGGING", False):
        print(f"[{tag}] {message}")


def generate_barcode_name(length=16):
    """Generates a unique identifier composed of 'l', 'I', and '1'."""
    prefix = random.choice(["l", "I"])
    return prefix + "".join(random.choices(["l", "I", "1"], k=length - 1))


@lru_cache(maxsize=None)
def normalize_path(path):
    return os.path.realpath(path)


def is_local(node, target_realpath):
    """Checks if the AST node belongs to the target source file."""
    if not node.location.file:
        return False
    return normalize_path(node.location.file.name) == target_realpath


def build_name_map(node, target_realpath, name_map):
    """Scans the AST for user-defined declarations to rename."""
    is_translation_unit = node.kind == clang.cindex.CursorKind.TRANSLATION_UNIT
    local_node = is_local(node, target_realpath)

    if not is_translation_unit and not local_node:
        return

    if local_node and node.kind.is_declaration() and node.spelling and node.spelling != "main":
        if node.spelling not in name_map:
            name_map[node.spelling] = generate_barcode_name()

    for child in node.get_children():
        build_name_map(child, target_realpath, name_map)


def parse_integer_literal(value_str):
    """Returns parsed integer literal metadata for safe mutations."""
    match = INTEGER_LITERAL_RE.fullmatch(value_str)
    if not match:
        return None

    body = match.group("body")
    suffix = match.group("suffix")
    normalized_body = body.replace("'", "")

    if normalized_body.startswith(("0x", "0X")):
        base = 16
    elif normalized_body.startswith(("0b", "0B")):
        base = 2
    elif normalized_body.startswith("0") and normalized_body != "0":
        base = 8
    else:
        base = 10

    return {
        "body": body,
        "suffix": suffix,
        "value": int(normalized_body, base),
    }


def parse_floating_literal(value_str):
    """Returns parsed floating literal metadata for safe mutations."""
    match = FLOAT_LITERAL_RE.fullmatch(value_str)
    if not match:
        return None

    body = match.group("body")
    suffix = match.group("suffix")
    try:
        value = float(body)
    except ValueError:
        return None

    if suffix in {"f", "F"}:
        cast_type = "float"
    elif suffix in {"l", "L"}:
        cast_type = "long double"
    else:
        cast_type = "double"

    return {
        "body": body,
        "suffix": suffix,
        "value": value,
        "cast_type": cast_type,
    }


def format_literal(value, suffix):
    return f"{value}{suffix}"


def is_escaped(text, index):
    backslashes = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 1


def get_constant_mutation(value_str, runtime_wrapper=None):
    """Generates safe bitwise identities for integer constants."""
    literal = parse_integer_literal(value_str)
    if literal is None:
        return value_str

    source = value_str
    suffix = literal["suffix"]
    value = literal["value"]

    key_min = 1
    key_max = max(0xFF, min(max(value, 1) * 2, 0xFFFF))
    key1 = format_literal(random.randint(key_min, key_max), suffix)
    key2 = format_literal(random.randint(key_min, key_max), suffix)

    strategies = [
        f"(({source} ^ {key1}) ^ {key1})",
        f"(({source} & {key1}) | ({source} & ~{key1}))",
    ]

    if value == 0:
        strategies.append(f"({key1} ^ {key1})")
        strategies.append(f"({key2} & ~{key2})")
    elif value == 1:
        odd_key = format_literal(random.randrange(1, 0xFFFF, 2), suffix)
        strategies.append(f"({odd_key} & {format_literal(1, suffix)})")

    result = random.choice(strategies)
    if random.random() > 0.5:
        extra_key = format_literal(random.randint(1, 0xFF), suffix)
        result = f"(({result} ^ {extra_key}) ^ {extra_key})"

    if config.ENABLE_RUNTIME_CONSTANT_OBFUSCATION and runtime_wrapper is not None:
        return runtime_wrapper(result)

    return result


def get_floating_constant_mutation(value_str):
    """Replaces floating literals with lookup-table based runtime helpers."""
    literal = parse_floating_literal(value_str)
    if literal is None or not getattr(config, "ENABLE_FLOATING_CONSTANT_ENCODING", False):
        return value_str

    import state

    state.init_floating_constant_names()
    for entry in state.FLOAT_CONSTANT_ENTRIES:
        if entry["source"] == value_str:
            ticket = entry["ticket"]
            break
    else:
        ticket = random.randint(0x1000, 0x7FFFFFFF)
        state.FLOAT_CONSTANT_ENTRIES.append(
            {
                "source": value_str,
                "ticket": ticket,
                "value": literal["value"],
                "cast_type": literal["cast_type"],
            }
        )

    helper_expr = f"{state.FLOAT_CONSTANT_HELPER_NAME}({format_literal(ticket, '')}U)"
    if literal["cast_type"] == "double":
        return helper_expr
    return f"static_cast<{literal['cast_type']}>({helper_expr})"


def strip_comments(source_text):
    """Removes line and block comments while preserving line structure."""
    if not config.ENABLE_COMMENT_STRIPPING:
        return source_text

    result = []
    index = 0
    in_string = False
    in_char = False
    in_line_comment = False
    in_block_comment = False

    while index < len(source_text):
        char = source_text[index]
        next_char = source_text[index + 1] if index + 1 < len(source_text) else ""

        if in_line_comment:
            if char == "\n":
                result.append("\n")
                in_line_comment = False
            index += 1
            continue

        if in_block_comment:
            if char == "\n":
                result.append("\n")
            if char == "*" and next_char == "/":
                in_block_comment = False
                index += 2
            else:
                index += 1
            continue

        if in_string:
            result.append(char)
            if char == '"' and not is_escaped(source_text, index):
                in_string = False
            index += 1
            continue

        if in_char:
            result.append(char)
            if char == "'" and not is_escaped(source_text, index):
                in_char = False
            index += 1
            continue

        if char == "/" and next_char == "/":
            in_line_comment = True
            index += 2
            continue

        if char == "/" and next_char == "*":
            in_block_comment = True
            index += 2
            continue

        if char == '"':
            in_string = True
        elif char == "'":
            in_char = True

        result.append(char)
        index += 1

    return "".join(result)


def replace_keywords_with_macros(source_text, keyword_to_macro):
    """Rewrites keyword tokens outside strings/comments/preprocessor lines."""
    result = []
    index = 0
    line_start = True
    in_string = False
    in_char = False
    in_line_comment = False
    in_block_comment = False
    in_preprocessor = False

    while index < len(source_text):
        char = source_text[index]
        next_char = source_text[index + 1] if index + 1 < len(source_text) else ""

        if in_line_comment:
            result.append(char)
            if char == "\n":
                in_line_comment = False
                line_start = True
                in_preprocessor = False
            index += 1
            continue

        if in_block_comment:
            result.append(char)
            if char == "*" and next_char == "/":
                result.append(next_char)
                in_block_comment = False
                index += 2
            else:
                index += 1
            continue

        if in_string:
            result.append(char)
            if char == '"' and not is_escaped(source_text, index):
                in_string = False
            if char == "\n":
                line_start = True
            index += 1
            continue

        if in_char:
            result.append(char)
            if char == "'" and not is_escaped(source_text, index):
                in_char = False
            if char == "\n":
                line_start = True
            index += 1
            continue

        if line_start:
            whitespace_end = index
            while whitespace_end < len(source_text) and source_text[whitespace_end] in " \t":
                whitespace_end += 1
            if whitespace_end < len(source_text) and source_text[whitespace_end] == "#":
                in_preprocessor = True

        if char == "/" and next_char == "/":
            result.append(char)
            result.append(next_char)
            in_line_comment = True
            index += 2
            continue

        if char == "/" and next_char == "*":
            result.append(char)
            result.append(next_char)
            in_block_comment = True
            index += 2
            continue

        if char == '"':
            result.append(char)
            in_string = True
            index += 1
            line_start = False
            continue

        if char == "'":
            result.append(char)
            in_char = True
            index += 1
            line_start = False
            continue

        if not in_preprocessor and (char.isalpha() or char == "_"):
            start = index
            index += 1
            while index < len(source_text) and (source_text[index].isalnum() or source_text[index] == "_"):
                index += 1
            token = source_text[start:index]
            result.append(keyword_to_macro.get(token, token))
            line_start = False
            continue

        result.append(char)
        if char == "\n":
            line_start = True
            in_preprocessor = False
        elif char not in " \t":
            line_start = False
        index += 1

    return "".join(result)


def split_top_level_statements(body_text):
    """Splits a function body into top-level statements."""
    statements = []
    current = []
    paren_depth = 0
    bracket_depth = 0
    brace_depth = 0
    in_string = False
    in_char = False
    in_line_comment = False
    in_block_comment = False

    for index, char in enumerate(body_text):
        next_char = body_text[index + 1] if index + 1 < len(body_text) else ""
        current.append(char)

        if in_line_comment:
            if char == "\n":
                in_line_comment = False
            continue
        if in_block_comment:
            if char == "*" and next_char == "/":
                current.append(next_char)
                in_block_comment = False
            continue
        if in_string:
            if char == '"' and not is_escaped(body_text, index):
                in_string = False
            continue
        if in_char:
            if char == "'" and not is_escaped(body_text, index):
                in_char = False
            continue

        if char == "/" and next_char == "/":
            in_line_comment = True
            continue
        if char == "/" and next_char == "*":
            in_block_comment = True
            continue
        if char == '"':
            in_string = True
            continue
        if char == "'":
            in_char = True
            continue

        if char == "(":
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
            brace_depth = max(0, brace_depth - 1)

        if paren_depth == 0 and bracket_depth == 0 and brace_depth == 0:
            stripped = "".join(current).strip()
            if char == ";" or (char == "}" and stripped and stripped != "}"):
                statements.append("".join(current).strip())
                current = []

    trailing = "".join(current).strip()
    if trailing:
        statements.append(trailing)
    return statements


def looks_like_declaration(statement):
    stripped = statement.strip()
    if not stripped.endswith(";"):
        return False

    forbidden_prefixes = (
        "return", "break", "continue", "goto", "throw", "if", "for", "while",
        "switch", "case", "default", "else", "do", "try", "catch", "asm",
        "__asm__", "static_assert",
    )
    if re.match(rf"^(?:{'|'.join(re.escape(prefix) for prefix in forbidden_prefixes)})\b", stripped):
        return False

    head = stripped[:-1].strip()
    if not head:
        return False

    if head.startswith(("typedef ", "using ", "enum ", "struct ", "class ")):
        return True

    if "=" in head:
        prefix = head.split("=", 1)[0].strip()
    else:
        prefix = head

    identifiers = IDENTIFIER_RE.findall(prefix)
    if len(identifiers) >= 2:
        return True

    type_keywords = {
        "const", "constexpr", "static", "volatile", "unsigned", "signed", "short",
        "long", "int", "char", "float", "double", "bool", "auto", "size_t",
        "ssize_t", "typename", "mutable", "register",
    }
    return any(keyword in prefix.split() for keyword in type_keywords)


def indent_block(text, indent):
    lines = textwrap.dedent(text).strip().splitlines()
    if not lines:
        return [indent.rstrip()]
    return [indent + line.rstrip() for line in lines]


def find_matching_brace(source_text, brace_index):
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

    return cursor if depth == 0 else None


def extract_function_like_metadata(source_text, brace_index):
    header = source_text[max(0, brace_index - FUNCTION_HEADER_SCAN_WINDOW):brace_index]
    stripped_header = header.rstrip()
    if not stripped_header.endswith(")"):
        return None

    open_paren_index = stripped_header.rfind("(")
    if open_paren_index == -1:
        return None

    prefix = stripped_header[:open_paren_index].rstrip()
    name_match = re.search(r"([A-Za-z_]\w*)\s*$", prefix)
    if not name_match:
        return None

    name = name_match.group(1)
    if name in FUNCTION_LIKE_SKIP_NAMES:
        return None
    if prefix.endswith(("namespace", "class", "struct", "enum")) or "template" in prefix:
        return None

    return {
        "name": name,
        "prefix": prefix,
        "params_text": stripped_header[open_paren_index + 1:],
        "open_paren_index": open_paren_index,
        "header_text": stripped_header,
    }


def iter_function_definitions(source_text):
    scan_index = 0
    while scan_index < len(source_text):
        brace_index = source_text.find("{", scan_index)
        if brace_index == -1:
            break

        metadata = extract_function_like_metadata(source_text, brace_index)
        if metadata is None:
            scan_index = brace_index + 1
            continue

        end_index = find_matching_brace(source_text, brace_index)
        if end_index is None:
            break

        line_start = source_text.rfind("\n", 0, brace_index) + 1
        base_indent = re.match(r"\s*", source_text[line_start:brace_index]).group(0)
        body_text = source_text[brace_index + 1:end_index - 1]
        skip_structural = (
            has_vm_skip_marker(source_text, line_start)
            or CHERRY_SKIP_MARKER in body_text[:512]
            or is_performance_sensitive_function(
            metadata["header_text"],
            body_text,
            )
        )
        yield {
            "brace_index": brace_index,
            "end_index": end_index,
            "body_text": body_text,
            "base_indent": base_indent,
            "skip_structural": skip_structural,
            **metadata,
        }
        scan_index = end_index


def has_vm_skip_marker(source_text, line_start):
    marker_index = source_text.rfind(VM_SKIP_MARKER, 0, line_start)
    if marker_index == -1:
        return False
    # The VM skip marker is emitted immediately ahead of the wrapped function
    # signature. If a prior function already closed before this line, the marker
    # belongs to that previous function and should not suppress new transforms.
    closing_brace_index = source_text.find("}", marker_index, line_start)
    return closing_brace_index == -1


def is_performance_sensitive_function(header_text, body_text):
    combined = f"{header_text}\n{body_text}"
    stl_search_tokens = (
        "upper_bound(",
        "lower_bound(",
        "binary_search(",
        "equal_range(",
        "std::upper_bound(",
        "std::lower_bound(",
        "std::binary_search(",
        "std::equal_range(",
    )
    if any(token in combined for token in stl_search_tokens):
        return True

    # Passing STL containers by value is already expensive; avoid piling on
    # structural transforms that amplify the cost in query-heavy code.
    container_by_value_patterns = (
        r"\b(?:std::)?vector\s*<[^>]+>\s+[A-Za-z_]\w*",
        r"\b(?:std::)?deque\s*<[^>]+>\s+[A-Za-z_]\w*",
        r"\b(?:std::)?string\s+[A-Za-z_]\w*",
    )
    return any(re.search(pattern, header_text) for pattern in container_by_value_patterns)


def find_vm_protected_regions(source_text):
    regions = []
    search_index = 0
    while True:
        marker_index = source_text.find(VM_SKIP_MARKER, search_index)
        if marker_index == -1:
            break
        brace_index = source_text.find("{", marker_index)
        if brace_index == -1:
            break
        end_index = find_matching_brace(source_text, brace_index)
        if end_index is None:
            break
        regions.append((marker_index, end_index))
        search_index = end_index
    return regions


def split_parameter_list(params_text):
    parts = []
    current = []
    angle_depth = 0
    paren_depth = 0
    bracket_depth = 0

    for char in params_text:
        if char == "<":
            angle_depth += 1
        elif char == ">":
            angle_depth = max(0, angle_depth - 1)
        elif char == "(":
            paren_depth += 1
        elif char == ")":
            paren_depth = max(0, paren_depth - 1)
        elif char == "[":
            bracket_depth += 1
        elif char == "]":
            bracket_depth = max(0, bracket_depth - 1)

        if char == "," and angle_depth == 0 and paren_depth == 0 and bracket_depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)

    trailing = "".join(current).strip()
    if trailing:
        parts.append(trailing)
    return parts


def extract_parameter_name(param_text):
    normalized = param_text.strip()
    if not normalized or normalized in {"void", "..."}:
        return None
    normalized = re.sub(r"\s*=\s*.*$", "", normalized)
    match = re.search(r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*$", normalized)
    if not match:
        return None
    candidate = match.group(1)
    return None if candidate in CPP_KEYWORDS else candidate


def extract_declared_local_name(statement):
    stripped = statement.strip()
    if not stripped.endswith(";") or "," in stripped:
        return None
    head = stripped[:-1].strip()
    prefix = head.split("=", 1)[0].strip()
    identifiers = IDENTIFIER_RE.findall(prefix)
    if len(identifiers) < 2:
        return None
    candidate = identifiers[-1]
    return None if candidate in CPP_KEYWORDS else candidate


def replace_identifier_text(source_text, old_name, new_name):
    return re.sub(rf"\b{re.escape(old_name)}\b", new_name, source_text)


def apply_name_map_to_fragment(fragment, name_map):
    rewritten = fragment
    for original_name, obfuscated_name in sorted(name_map.items(), key=lambda item: len(item[0]), reverse=True):
        rewritten = replace_identifier_text(rewritten, original_name, obfuscated_name)
    return rewritten


def build_protected_range_index(ranges):
    if not ranges:
        return [], []

    merged = []
    for start, end in sorted(ranges):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    intervals = [(start, end) for start, end in merged]
    starts = [start for start, _ in intervals]
    return intervals, starts


def is_in_protected_range(token_start, token_end, protected_ranges, protected_range_starts):
    if not protected_ranges:
        return False

    index = bisect_right(protected_range_starts, token_start) - 1
    if index < 0:
        return False
    start, end = protected_ranges[index]
    return start <= token_start and token_end <= end
