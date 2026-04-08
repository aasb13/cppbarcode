import ast
import base64
import random
import re

import clang.cindex

import config
import state
from transformers.runtime import runtime_wrap_constant
from util import (
    apply_name_map_to_fragment,
    generate_barcode_name,
    get_constant_mutation,
    is_local,
    is_escaped,
    parse_integer_literal,
    vlog,
)


INTEGRAL_TYPE_KINDS = {
    clang.cindex.TypeKind.BOOL,
    clang.cindex.TypeKind.CHAR_U,
    clang.cindex.TypeKind.UCHAR,
    clang.cindex.TypeKind.CHAR16,
    clang.cindex.TypeKind.CHAR32,
    clang.cindex.TypeKind.USHORT,
    clang.cindex.TypeKind.UINT,
    clang.cindex.TypeKind.ULONG,
    clang.cindex.TypeKind.ULONGLONG,
    clang.cindex.TypeKind.UINT128,
    clang.cindex.TypeKind.CHAR_S,
    clang.cindex.TypeKind.SCHAR,
    clang.cindex.TypeKind.WCHAR,
    clang.cindex.TypeKind.SHORT,
    clang.cindex.TypeKind.INT,
    clang.cindex.TypeKind.LONG,
    clang.cindex.TypeKind.LONGLONG,
    clang.cindex.TypeKind.INT128,
}
COMMUTATIVE_ASSOCIATIVE_OPERATORS = {"+", "*", "&", "|", "^"}
POINTER_LIKE_TYPE_KINDS = {
    clang.cindex.TypeKind.POINTER,
    clang.cindex.TypeKind.CONSTANTARRAY,
    clang.cindex.TypeKind.INCOMPLETEARRAY,
    clang.cindex.TypeKind.VARIABLEARRAY,
}


def is_integral_cursor_type(cursor):
    type_obj = cursor.type
    if type_obj.kind == clang.cindex.TypeKind.INVALID:
        return False
    canonical = type_obj.get_canonical()
    return canonical.kind in INTEGRAL_TYPE_KINDS


def is_pointer_like_cursor_type(cursor):
    type_obj = cursor.type
    if type_obj.kind == clang.cindex.TypeKind.INVALID:
        return False
    canonical = type_obj.get_canonical()
    return canonical.kind in POINTER_LIKE_TYPE_KINDS


def unwrap_expression_cursor(cursor):
    current = cursor
    while current.kind == clang.cindex.CursorKind.UNEXPOSED_EXPR:
        children = [
            child
            for child in current.get_children()
            if child.extent.start.offset < child.extent.end.offset
        ]
        if len(children) != 1:
            break
        current = children[0]
    return current


def render_obfuscated_expression(cursor, source_text, name_map):
    """Renders a source slice while preserving spacing and local token obfuscation."""
    result = []
    cursor_offset = cursor.extent.start.offset

    for token in cursor.get_tokens():
        token_start = token.extent.start.offset
        token_end = token.extent.end.offset
        result.append(source_text[cursor_offset:token_start])

        spelling = token.spelling
        if config.ENABLE_IDENTIFIER_OBFUSCATION and spelling in name_map:
            spelling = name_map[spelling]
        elif (
            config.ENABLE_CONSTANT_MUTATION
            and token.kind == clang.cindex.TokenKind.LITERAL
            and parse_integer_literal(spelling) is not None
        ):
            spelling = get_constant_mutation(spelling, runtime_wrapper=runtime_wrap_constant)
        elif config.ENABLE_BOOLEAN_OBFUSCATION and spelling == "true":
            spelling = "(0x7 > 0x1)"
        elif config.ENABLE_BOOLEAN_OBFUSCATION and spelling == "false":
            spelling = "(0x7 < 0x1)"

        result.append(spelling)
        cursor_offset = token_end

    result.append(source_text[cursor_offset:cursor.extent.end.offset])
    return "".join(result)


def is_supported_tmp_addition_node(cursor, source_text):
    if cursor.kind != clang.cindex.CursorKind.BINARY_OPERATOR or not is_integral_cursor_type(cursor):
        return False

    children = [
        child for child in cursor.get_children() if child.extent.start.offset < child.extent.end.offset
    ]
    if len(children) != 2 or not all(is_integral_cursor_type(child) for child in children):
        return False

    lhs_node, rhs_node = children
    operator_text = source_text[lhs_node.extent.end.offset:rhs_node.extent.start.offset].strip()
    return operator_text == "+"


def get_integral_binary_operator(cursor, source_text):
    normalized = unwrap_expression_cursor(cursor)
    if normalized.kind != clang.cindex.CursorKind.BINARY_OPERATOR or not is_integral_cursor_type(normalized):
        return None, None

    children = [
        child for child in normalized.get_children() if child.extent.start.offset < child.extent.end.offset
    ]
    if len(children) != 2 or not all(is_integral_cursor_type(child) for child in children):
        return None, None

    lhs_node, rhs_node = children
    operator_text = source_text[lhs_node.extent.end.offset:rhs_node.extent.start.offset].strip()
    return operator_text, children


def collect_commutative_terms(cursor, source_text, operator_text):
    normalized = unwrap_expression_cursor(cursor)
    current_op, children = get_integral_binary_operator(normalized, source_text)
    if current_op != operator_text or operator_text not in COMMUTATIVE_ASSOCIATIVE_OPERATORS:
        return [normalized]

    lhs_node, rhs_node = children
    return collect_commutative_terms(lhs_node, source_text, operator_text) + collect_commutative_terms(
        rhs_node, source_text, operator_text
    )


def collect_additive_terms(cursor, source_text):
    normalized = unwrap_expression_cursor(cursor)
    if not is_supported_tmp_addition_node(normalized, source_text):
        return [normalized]

    lhs_node, rhs_node = [
        child for child in normalized.get_children() if child.extent.start.offset < child.extent.end.offset
    ]
    return collect_additive_terms(lhs_node, source_text) + collect_additive_terms(rhs_node, source_text)


def build_randomized_additive_expression(term_texts):
    if len(term_texts) == 1:
        return term_texts[0]

    terms = list(term_texts)
    if random.random() > 0.45:
        random.shuffle(terms)

    work = terms[:]
    while len(work) > 1:
        if random.random() > 0.55:
            left = work.pop(0)
            right = work.pop(0)
        else:
            idx = random.randrange(len(work) - 1)
            left = work.pop(idx)
            right = work.pop(idx)

        combine_variants = [
            f"({left} + {right})",
            f"(({left}) + ({right}))",
            f"(({left}) - (static_cast<decltype({right})>(0) - ({right})))",
        ]
        combined = random.choice(combine_variants)
        if random.random() > 0.5:
            work.insert(0, combined)
        else:
            work.append(combined)

    return work[0]


def randomize_commutative_expression(cursor, source_text, name_map, depth):
    operator_text, children = get_integral_binary_operator(cursor, source_text)
    if operator_text not in COMMUTATIVE_ASSOCIATIVE_OPERATORS:
        return None

    terms = collect_commutative_terms(cursor, source_text, operator_text)
    if len(terms) < 2:
        return None

    rendered_terms = [
        render_recursive_expression(term, source_text, name_map, depth + 1) for term in terms
    ]
    if operator_text == "+":
        return build_randomized_additive_expression(rendered_terms)

    randomized_terms = rendered_terms[:]
    if random.random() > 0.35:
        random.shuffle(randomized_terms)

    work = randomized_terms[:]
    while len(work) > 1:
        if random.random() > 0.5:
            left = work.pop(0)
            right = work.pop(0)
        else:
            idx = random.randrange(len(work) - 1)
            left = work.pop(idx)
            right = work.pop(idx)
        combined = random.choice(
            [
                f"({left} {operator_text} {right})",
                f"(({left}) {operator_text} ({right}))",
            ]
        )
        if random.random() > 0.5:
            work.insert(0, combined)
        else:
            work.append(combined)
    return work[0]


def get_memory_access_replacement(node, source_text, name_map, depth=0):
    if not config.ENABLE_MEMORY_ACCESS_OBFUSCATION:
        return None

    normalized = unwrap_expression_cursor(node)

    if normalized.kind == clang.cindex.CursorKind.ARRAY_SUBSCRIPT_EXPR:
        children = [
            child for child in normalized.get_children() if child.extent.start.offset < child.extent.end.offset
        ]
        if len(children) != 2 or not is_pointer_like_cursor_type(children[0]):
            return None
        state.init_memory_access_names()
        base_text = render_recursive_expression(children[0], source_text, name_map, depth + 1)
        index_text = render_recursive_expression(children[1], source_text, name_map, depth + 1)
        return f"(*{state.MEMORY_PTR_ADVANCE_HELPER_NAME}(({base_text}), {state.MEMORY_INDEX_HELPER_NAME}(({index_text}))))"

    if normalized.kind == clang.cindex.CursorKind.MEMBER_REF_EXPR:
        token_spellings = [token.spelling for token in normalized.get_tokens()]
        if "->" not in token_spellings and "." not in token_spellings:
            return None
        operator_symbol = "->" if "->" in token_spellings else "."
        operator_index = token_spellings.index(operator_symbol)
        token_texts = [
            source_text[token.extent.start.offset : token.extent.end.offset]
            for token in normalized.get_tokens()
        ]
        base_text = "".join(token_texts[:operator_index]).strip()
        member_text = "".join(token_texts[operator_index + 1 :]).strip()
        if not base_text or not member_text:
            return None
        if member_text in {"begin", "end", "size", "front", "back"}:
            return None
        base_text = apply_name_map_to_fragment(base_text, name_map)
        if operator_symbol == "." and not re.fullmatch(
            r"(?:this|[A-Za-z_]\w*(?:(?:\.|->)[A-Za-z_]\w*)*)", base_text
        ):
            return None
        state.init_memory_access_names()
        if operator_symbol == "->":
            return f"{state.MEMORY_MEMBER_HELPER_NAME}(({base_text})){operator_symbol}{member_text}"
        return f"(*{state.MEMORY_MEMBER_HELPER_NAME}(&({base_text}))).{member_text}"

    return None


def get_function_pointer_call_replacement(node, source_text, name_map, depth=0):
    if not config.ENABLE_FUNCTION_POINTER_INDIRECTION:
        return None

    referenced = node.referenced
    if referenced is None or referenced.kind != clang.cindex.CursorKind.FUNCTION_DECL:
        return None

    if not node.spelling or node.spelling in {"sort", "main"}:
        return None

    children = [child for child in node.get_children() if child.extent.start.offset < child.extent.end.offset]
    if not children:
        return None

    callee = unwrap_expression_cursor(children[0])
    if callee.kind != clang.cindex.CursorKind.DECL_REF_EXPR:
        return None

    call_tokens = list(node.get_tokens())
    if len(call_tokens) < 3 or call_tokens[1].spelling != "(" or call_tokens[-1].spelling != ")":
        return None

    callee_text = source_text[callee.extent.start.offset : callee.extent.end.offset].strip()
    if not callee_text or "::" in callee_text:
        return None
    if callee_text in state.VM_EXPRESSION_WRAPPER_NAMES:
        return None

    state.init_function_pointer_helper_names()
    callee_name = name_map.get(callee_text, callee_text)
    arg_exprs = [render_recursive_expression(child, source_text, name_map, depth + 1) for child in children[1:]]
    staged_pointer = f"{state.FUNCTION_PTR_STAGE_HELPER_NAME}(&{callee_name})"
    call_variants = [
        f"{state.FUNCTION_PTR_INVOKE_HELPER_NAME}({staged_pointer}{', ' if arg_exprs else ''}{', '.join(arg_exprs)})",
        f"{state.FUNCTION_PTR_INVOKE_HELPER_NAME}(({staged_pointer}){', ' if arg_exprs else ''}{', '.join(arg_exprs)})",
        f"{state.FUNCTION_PTR_INVOKE_HELPER_NAME}({state.FUNCTION_PTR_STAGE_HELPER_NAME}({state.FUNCTION_PTR_STAGE_HELPER_NAME}(&{callee_name})){', ' if arg_exprs else ''}{', '.join(arg_exprs)})",
    ]
    return random.choice(call_variants)


def render_recursive_expression(cursor, source_text, name_map, depth=0):
    if depth >= config.TMP_MAX_RENDER_DEPTH:
        return render_obfuscated_expression(cursor, source_text, name_map)

    normalized = unwrap_expression_cursor(cursor)

    randomized_expr = randomize_commutative_expression(normalized, source_text, name_map, depth)
    if randomized_expr is not None and random.random() > 0.45:
        return randomized_expr

    if is_supported_tmp_addition_node(normalized, source_text):
        state.init_tmp_addition_names()
        additive_terms = collect_additive_terms(normalized, source_text)
        rendered_terms = [
            render_recursive_expression(term, source_text, name_map, depth + 1) for term in additive_terms
        ]
        randomized_expr = build_randomized_additive_expression(rendered_terms)
        if len(additive_terms) > 2 and random.random() > 0.4:
            return randomized_expr

        lhs_node, rhs_node = [
            child for child in normalized.get_children() if child.extent.start.offset < child.extent.end.offset
        ]
        lhs_text = render_recursive_expression(lhs_node, source_text, name_map, depth + 1)
        rhs_text = render_recursive_expression(rhs_node, source_text, name_map, depth + 1)
        call_variants = [
            f"{state.TMP_ADD_HELPER_NAME}(({lhs_text}), ({rhs_text}))",
            f"{state.TMP_ADD_HELPER_NAME}(static_cast<decltype(({lhs_text}) + ({rhs_text}))>({lhs_text}), ({rhs_text}))",
            f"{state.TMP_ADD_HELPER_NAME}((({lhs_text}) + static_cast<decltype({lhs_text})>(0)), ({rhs_text}))",
            f"{state.TMP_ADD_HELPER_NAME}(({lhs_text}), (({rhs_text}) + static_cast<decltype({rhs_text})>(0)))",
            randomized_expr,
        ]
        return random.choice(call_variants)

    memory_access_rewrite = get_memory_access_replacement(normalized, source_text, name_map, depth=depth + 1)
    if memory_access_rewrite is not None:
        return memory_access_rewrite

    function_pointer_call = get_function_pointer_call_replacement(normalized, source_text, name_map, depth=depth + 1)
    if function_pointer_call is not None:
        return function_pointer_call

    return render_obfuscated_expression(normalized, source_text, name_map)


def encode_string_literal(value_str):
    if not value_str.startswith('"') or not value_str.endswith('"'):
        return None

    try:
        decoded = ast.literal_eval(value_str)
    except (SyntaxError, ValueError):
        return None

    if not isinstance(decoded, str):
        return None

    raw_bytes = decoded.encode("utf-8")
    key_a = random.randint(1, 255)
    key_b = random.randint(1, 255)
    xor_stage = bytes(byte ^ key_a for byte in raw_bytes)
    base64_stage = base64.b64encode(xor_stage)
    layered = bytes(byte ^ key_b for byte in base64_stage)
    encoded_literal = '"' + "".join(f"\\x{byte:02x}" for byte in layered) + '"'
    return {
        "encoded_literal": encoded_literal,
        "key_a": key_a,
        "key_b": key_b,
        "output_size": len(raw_bytes),
    }


def build_string_literal_replacement(value_str):
    if not config.ENABLE_STRING_LITERAL_ENCRYPTION:
        return None

    encoded = encode_string_literal(value_str)
    if encoded is None:
        return None

    state.init_string_literal_names()
    lambda_name = generate_barcode_name(18)
    vlog(
        "strings",
        f"encoded_literal out_size={encoded['output_size']} key_a={encoded['key_a']} key_b={encoded['key_b']}",
    )
    return (
        f"([]() -> const char* {{ "
        f"static constexpr auto {lambda_name} = {state.STRING_DECODE_HELPER_NAME}<{encoded['output_size']}>"
        f"({encoded['encoded_literal']}, {encoded['key_a']}, {encoded['key_b']}); "
        f"return {lambda_name}.data(); "
        f"}}())"
    )


def get_type_level_replacement(node, source_text, name_map):
    if not config.ENABLE_TYPE_LEVEL_OBFUSCATION:
        return None
    if node.kind != clang.cindex.CursorKind.VAR_DECL or not is_integral_cursor_type(node):
        return None
    if node.type.get_canonical().kind == clang.cindex.TypeKind.BOOL:
        return None

    semantic_parent = node.semantic_parent
    if semantic_parent is None or not semantic_parent.kind.is_declaration():
        return None
    if semantic_parent.kind not in {
        clang.cindex.CursorKind.FUNCTION_DECL,
        clang.cindex.CursorKind.CXX_METHOD,
        clang.cindex.CursorKind.CONSTRUCTOR,
        clang.cindex.CursorKind.CONVERSION_FUNCTION,
    }:
        return None

    token_list = list(node.get_tokens())
    assign_tokens = [token for token in token_list if token.spelling == "="]
    if len(assign_tokens) != 1:
        return None

    cursor = node.extent.start.offset - 1
    while cursor >= 0 and source_text[cursor].isspace():
        cursor -= 1
    if cursor >= 0 and source_text[cursor] == "(":
        return None

    decl_text = source_text[node.extent.start.offset : node.extent.end.offset]
    if "," in decl_text or "const " in decl_text:
        return None

    children = [child for child in node.get_children() if child.extent.start.offset < child.extent.end.offset]
    if len(children) != 1:
        return None

    state.init_type_level_names()
    initializer = children[0]
    var_name = name_map.get(node.spelling, node.spelling)
    init_text = render_recursive_expression(initializer, source_text, name_map, depth=1)
    return f"{state.OPAQUE_WRAPPER_NAME}<{node.type.spelling}> {var_name} = {init_text};"


def get_data_flow_replacement(node, source_text, name_map):
    if not config.ENABLE_DATA_FLOW_OBFUSCATION:
        return None
    if node.kind != clang.cindex.CursorKind.VAR_DECL or not is_integral_cursor_type(node):
        return None

    semantic_parent = node.semantic_parent
    if semantic_parent is None or not semantic_parent.kind.is_declaration():
        return None
    if semantic_parent.kind not in {
        clang.cindex.CursorKind.FUNCTION_DECL,
        clang.cindex.CursorKind.CXX_METHOD,
        clang.cindex.CursorKind.CONSTRUCTOR,
        clang.cindex.CursorKind.CONVERSION_FUNCTION,
    }:
        return None

    children = [child for child in node.get_children() if child.extent.start.offset < child.extent.end.offset]
    if len(children) != 1:
        return None

    initializer = children[0]
    decl_text = source_text[node.extent.start.offset : node.extent.end.offset]
    token_list = list(node.get_tokens())
    assign_tokens = [token for token in token_list if token.spelling == "="]
    if len(assign_tokens) != 1:
        return None

    assign_token = assign_tokens[0]
    prefix_parts = []
    cursor_offset = node.extent.start.offset
    for token in token_list:
        if token.extent.start.offset >= assign_token.extent.start.offset:
            break
        prefix_parts.append(source_text[cursor_offset : token.extent.start.offset])
        prefix_parts.append(name_map.get(token.spelling, token.spelling))
        cursor_offset = token.extent.end.offset
    prefix_parts.append(source_text[cursor_offset : assign_token.extent.start.offset])
    prefix_text = "".join(prefix_parts).rstrip()
    if not prefix_text or "," in decl_text:
        return None

    cursor = node.extent.start.offset - 1
    while cursor >= 0 and source_text[cursor].isspace():
        cursor -= 1
    if cursor >= 0 and source_text[cursor] == "(":
        return None

    state.init_data_flow_names()
    op_kind = "expr"
    normalized_init = unwrap_expression_cursor(initializer)
    if normalized_init.kind == clang.cindex.CursorKind.BINARY_OPERATOR and is_integral_cursor_type(normalized_init):
        init_children = [
            child
            for child in normalized_init.get_children()
            if child.extent.start.offset < child.extent.end.offset
        ]
        if len(init_children) == 2:
            lhs_node, rhs_node = init_children
            op_text = source_text[lhs_node.extent.end.offset : rhs_node.extent.start.offset].strip()
            if op_text in {"+", "-"}:
                op_kind = op_text

    temp_a = generate_barcode_name(18)
    temp_b = generate_barcode_name(18)
    temp_c = generate_barcode_name(18)
    lines = []

    if op_kind in {"+", "-"}:
        lhs_node, rhs_node = [
            child
            for child in normalized_init.get_children()
            if child.extent.start.offset < child.extent.end.offset
        ]
        lhs_text = render_recursive_expression(lhs_node, source_text, name_map, depth=1)
        rhs_text = render_recursive_expression(rhs_node, source_text, name_map, depth=1)
        lines.append(
            f"auto {temp_a} = {state.DATA_FLOW_PACK_HELPER_NAME}(static_cast<decltype(({lhs_text}) {op_kind} ({rhs_text}))>({lhs_text}));"
        )
        lines.append(
            f"auto {temp_b} = {state.DATA_FLOW_PACK_HELPER_NAME}(static_cast<decltype(({lhs_text}) {op_kind} ({rhs_text}))>({rhs_text}));"
        )
        merge_call = f"{state.DATA_FLOW_MERGE_HELPER_NAME}({temp_a}, {temp_b}, '{op_kind}')"
        if state.DATA_FLOW_VARIANT["merge_mode"] == "restaged":
            merge_call = (
                f"{state.DATA_FLOW_MERGE_HELPER_NAME}({state.DATA_FLOW_PACK_HELPER_NAME}({state.DATA_FLOW_UNPACK_HELPER_NAME}({temp_a})), {temp_b}, '{op_kind}')"
            )
        lines.append(f"{prefix_text} = {state.DATA_FLOW_UNPACK_HELPER_NAME}({merge_call});")
        return "\n".join(lines)

    init_text = render_recursive_expression(initializer, source_text, name_map, depth=1)
    lines.append(
        f"auto {temp_c} = {state.DATA_FLOW_PACK_HELPER_NAME}(static_cast<decltype({init_text})>({init_text}));"
    )
    if state.DATA_FLOW_VARIANT["merge_mode"] == "restaged":
        lines.append(f"auto {temp_a} = {state.DATA_FLOW_PACK_HELPER_NAME}({state.DATA_FLOW_UNPACK_HELPER_NAME}({temp_c}));")
        lines.append(f"{prefix_text} = {state.DATA_FLOW_UNPACK_HELPER_NAME}({temp_a});")
    else:
        lines.append(f"{prefix_text} = {state.DATA_FLOW_UNPACK_HELPER_NAME}({temp_c});")
    return "\n".join(lines)


def collect_ast_replacements(
    node,
    target_realpath,
    name_map,
    source_text,
    replacements,
    _stats=None,
    _root=True,
):
    """Collects all AST-driven source rewrites in one traversal."""
    if _stats is None:
        _stats = {
            "visited": 0,
            "memory_access": 0,
            "container_accessor": 0,
            "stl_sort": 0,
            "fnptr_call": 0,
            "type_level": 0,
            "data_flow": 0,
            "tmp_add": 0,
        }
    _stats["visited"] += 1
    is_translation_unit = node.kind == clang.cindex.CursorKind.TRANSLATION_UNIT
    local_node = is_translation_unit or is_local(node, target_realpath)
    if not local_node:
        return

    if node.kind in {clang.cindex.CursorKind.ARRAY_SUBSCRIPT_EXPR, clang.cindex.CursorKind.MEMBER_REF_EXPR}:
        memory_access_rewrite = get_memory_access_replacement(node, source_text, name_map)
        if memory_access_rewrite is not None:
            replacements.append((node.extent.start.offset, node.extent.end.offset, memory_access_rewrite))
            _stats["memory_access"] += 1
            return

    if node.kind == clang.cindex.CursorKind.CALL_EXPR:
        call_tokens = list(node.get_tokens())
        if len(call_tokens) == 5:
            last_four = [token.spelling for token in call_tokens[-4:]]
            expr_text = "".join(name_map.get(token.spelling, token.spelling) for token in call_tokens[:-4])
            if last_four == [".", "front", "(", ")"] and expr_text:
                replacements.append((node.extent.start.offset, node.extent.end.offset, f"{expr_text}[0]"))
                _stats["container_accessor"] += 1
            elif last_four == [".", "back", "(", ")"] and expr_text:
                replacements.append(
                    (node.extent.start.offset, node.extent.end.offset, f"{expr_text}[{expr_text}.size() - 1]")
                )
                _stats["container_accessor"] += 1

        token_spellings = [token.spelling for token in call_tokens]
        if (
            len(call_tokens) == 14
            and token_spellings[0] == "sort"
            and token_spellings[1] == "("
            and token_spellings[7] == ","
            and token_spellings[-1] == ")"
            and token_spellings[5:7] == ["(", ")"]
            and token_spellings[11:13] == ["(", ")"]
            and token_spellings[2] == token_spellings[8]
            and token_spellings[3] == "."
            and token_spellings[4] == "begin"
            and token_spellings[9] == "."
            and token_spellings[10] == "end"
        ):
            state.init_stl_helper_names()
            container_expr = name_map.get(token_spellings[2], token_spellings[2])
            replacements.append(
                (node.extent.start.offset, node.extent.end.offset, f"{state.STL_SORT_HELPER_NAME}({container_expr})")
            )
            _stats["stl_sort"] += 1

        function_pointer_call = get_function_pointer_call_replacement(node, source_text, name_map)
        if function_pointer_call is not None:
            replacements.append((node.extent.start.offset, node.extent.end.offset, function_pointer_call))
            _stats["fnptr_call"] += 1
            return

    elif node.kind == clang.cindex.CursorKind.VAR_DECL and config.ENABLE_TYPE_LEVEL_OBFUSCATION:
        type_level_replacement = get_type_level_replacement(node, source_text, name_map)
        if type_level_replacement is not None:
            replacements.append((node.extent.start.offset, node.extent.end.offset, type_level_replacement))
            _stats["type_level"] += 1
            return

    elif node.kind == clang.cindex.CursorKind.VAR_DECL and config.ENABLE_DATA_FLOW_OBFUSCATION:
        data_flow_replacement = get_data_flow_replacement(node, source_text, name_map)
        if data_flow_replacement is not None:
            replacements.append((node.extent.start.offset, node.extent.end.offset, data_flow_replacement))
            _stats["data_flow"] += 1
            return

    elif (
        node.kind == clang.cindex.CursorKind.BINARY_OPERATOR
        and config.ENABLE_TMP_ADDITION_OBFUSCATION
        and is_supported_tmp_addition_node(node, source_text)
    ):
        replacements.append(
            (node.extent.start.offset, node.extent.end.offset, render_recursive_expression(node, source_text, name_map))
        )
        _stats["tmp_add"] += 1
        return

    for child in node.get_children():
        collect_ast_replacements(
            child,
            target_realpath,
            name_map,
            source_text,
            replacements,
            _stats=_stats,
            _root=False,
        )

    if _root:
        vlog(
            "ast",
            "visited={visited}, replacements: mem={memory_access}, accessor={container_accessor}, stl_sort={stl_sort}, fnptr={fnptr_call}, type_level={type_level}, data_flow={data_flow}, tmp_add={tmp_add}".format(
                **_stats
            ),
        )
