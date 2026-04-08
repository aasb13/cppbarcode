import math
import random
import re

import clang.cindex

import config
import state
from util import (
    apply_name_map_to_fragment,
    find_matching_brace,
    generate_barcode_name,
    indent_block,
    is_in_protected_range,
    is_local,
    is_escaped,
    looks_like_declaration,
    normalize_path,
    replace_identifier_text,
    split_parameter_list,
    split_top_level_statements,
    strip_comments,
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
    clang.cindex.TypeKind.MEMBERPOINTER,
    clang.cindex.TypeKind.OBJCCLASS,
    clang.cindex.TypeKind.OBJCOBJECTPOINTER,
    clang.cindex.TypeKind.BLOCKPOINTER,
}


def is_integral_cursor_type(cursor) -> bool:
    type_obj = cursor.type
    if type_obj.kind == clang.cindex.TypeKind.INVALID:
        return False
    canonical = type_obj.get_canonical()
    return canonical.kind in INTEGRAL_TYPE_KINDS


def is_pointer_like_cursor_type(cursor) -> bool:
    type_obj = cursor.type
    if type_obj.kind == clang.cindex.TypeKind.INVALID:
        return False
    canonical = type_obj.get_canonical()
    if canonical.kind in POINTER_LIKE_TYPE_KINDS:
        return True
    if canonical.kind == clang.cindex.TypeKind.RECORD:
        return "*" in str(canonical.spelling)
    return False


def get_memory_access_replacement(node, source_text: str, name_map: dict, depth: int = 0) -> str | None:
    if not config.ENABLE_MEMORY_ACCESS_OBFUSCATION:
        return None
    if depth > 6:
        return None
    if node.kind not in {clang.cindex.CursorKind.ARRAY_SUBSCRIPT_EXPR, clang.cindex.CursorKind.UNARY_OPERATOR}:
        return None

    tokens = list(node.get_tokens())
    if not tokens:
        return None

    token_spellings = [token.spelling for token in tokens]
    if node.kind == clang.cindex.CursorKind.ARRAY_SUBSCRIPT_EXPR and len(token_spellings) >= 4:
        # pattern: base [ index ]
        if "[" not in token_spellings or "]" not in token_spellings:
            return None

        left_bracket = token_spellings.index("[")
        right_bracket = len(token_spellings) - 1 - token_spellings[::-1].index("]")
        base_text = "".join(token_spellings[:left_bracket]).strip()
        index_text = "".join(token_spellings[left_bracket + 1 : right_bracket]).strip()
        if not base_text or not index_text:
            return None

        state.init_memory_access_names()
        base_expr = name_map.get(base_text, base_text)
        idx_expr = apply_name_map_to_fragment(index_text, name_map)
        return f"*{state.MEMORY_INDEX_HELPER_NAME}({base_expr}, {idx_expr})"

    if node.kind == clang.cindex.CursorKind.UNARY_OPERATOR and token_spellings and token_spellings[0] == "*":
        # pointer dereference: *expr
        expr_text = "".join(token_spellings[1:]).strip()
        if not expr_text:
            return None
        state.init_memory_access_names()
        expr_val = apply_name_map_to_fragment(expr_text, name_map)
        return f"*{state.MEMORY_PTR_ADVANCE_HELPER_NAME}({expr_val}, 0)"

    return None


def render_obfuscated_expression(cursor, source_text: str, name_map: dict) -> str:
    # Default: return exact slice with renames applied.
    raw = source_text[cursor.extent.start.offset : cursor.extent.end.offset]
    return apply_name_map_to_fragment(raw, name_map)


def is_supported_tmp_addition_node(cursor, source_text: str) -> bool:
    if cursor.kind != clang.cindex.CursorKind.BINARY_OPERATOR:
        return False
    tokens = list(cursor.get_tokens())
    token_spellings = [token.spelling for token in tokens]
    return "+" in token_spellings


def get_integral_binary_operator(cursor, source_text: str) -> str | None:
    tokens = list(cursor.get_tokens())
    for token in tokens:
        if token.spelling in {"+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>"}:
            return token.spelling
    return None


def collect_commutative_terms(cursor, source_text: str, operator_text: str) -> list[str]:
    children = list(cursor.get_children())
    if len(children) != 2:
        return [source_text[cursor.extent.start.offset : cursor.extent.end.offset]]
    left, right = children
    op = get_integral_binary_operator(cursor, source_text)
    if op != operator_text:
        return [source_text[cursor.extent.start.offset : cursor.extent.end.offset]]
    return collect_commutative_terms(left, source_text, operator_text) + collect_commutative_terms(right, source_text, operator_text)


def collect_additive_terms(cursor, source_text: str) -> list[str]:
    return collect_commutative_terms(cursor, source_text, "+")


def build_randomized_additive_expression(term_texts: list[str]) -> str:
    if not term_texts:
        return "0"
    shuffled = term_texts[:]
    random.shuffle(shuffled)
    expr = shuffled[0]
    for term in shuffled[1:]:
        expr = f"({expr} + {term})"
    return expr


def randomize_commutative_expression(cursor, source_text: str, name_map: dict, depth: int) -> str:
    operator_text = get_integral_binary_operator(cursor, source_text)
    if operator_text not in COMMUTATIVE_ASSOCIATIVE_OPERATORS:
        return render_obfuscated_expression(cursor, source_text, name_map)
    terms = collect_commutative_terms(cursor, source_text, operator_text)
    terms = [apply_name_map_to_fragment(term, name_map) for term in terms]
    random.shuffle(terms)
    expr = terms[0]
    for term in terms[1:]:
        expr = f"({expr} {operator_text} {term})"
    return expr


def render_recursive_expression(cursor, source_text: str, name_map: dict, depth: int = 0) -> str:
    if depth > config.TMP_MAX_RENDER_DEPTH:
        return render_obfuscated_expression(cursor, source_text, name_map)

    # tmp-addition: commutative randomization
    if config.ENABLE_TMP_ADDITION_OBFUSCATION and cursor.kind == clang.cindex.CursorKind.BINARY_OPERATOR:
        return randomize_commutative_expression(cursor, source_text, name_map, depth)

    children = list(cursor.get_children())
    if not children:
        return render_obfuscated_expression(cursor, source_text, name_map)
    return render_obfuscated_expression(cursor, source_text, name_map)


def unwrap_expression_cursor(cursor):
    current = cursor
    while True:
        children = list(current.get_children())
        if len(children) != 1:
            return current
        child = children[0]
        if child.kind in {clang.cindex.CursorKind.UNEXPOSED_EXPR, clang.cindex.CursorKind.PAREN_EXPR}:
            current = child
            continue
        return current


def get_function_pointer_call_replacement(node, source_text: str, name_map: dict, depth: int = 0) -> str | None:
    if not config.ENABLE_FUNCTION_POINTER_INDIRECTION:
        return None
    if depth > 4:
        return None
    if node.kind != clang.cindex.CursorKind.CALL_EXPR:
        return None

    children = list(node.get_children())
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


def get_type_level_replacement(node, source_text: str, name_map: dict) -> str | None:
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

    state.init_type_level_names()
    wrapper_name = state.OPAQUE_WRAPPER_NAME

    # Convert `T x = expr;` into `auto x = Wrapper<T>(expr);`
    eq_idx = decl_text.find("=")
    before = decl_text[:eq_idx].strip()
    after = decl_text[eq_idx + 1 :].strip().rstrip(";")
    parts = before.split()
    if len(parts) < 2:
        return None
    var_name = parts[-1]
    type_name = " ".join(parts[:-1])
    obf_var = name_map.get(var_name, var_name)
    after_expr = apply_name_map_to_fragment(after, name_map)
    return f"auto {obf_var} = {wrapper_name}<{type_name}>({after_expr});"


def get_data_flow_replacement(node, source_text: str, name_map: dict) -> str | None:
    if not config.ENABLE_DATA_FLOW_OBFUSCATION:
        return None
    if node.kind != clang.cindex.CursorKind.VAR_DECL:
        return None
    if not is_integral_cursor_type(node):
        return None
    if node.type.get_canonical().kind == clang.cindex.TypeKind.BOOL:
        return None

    token_list = list(node.get_tokens())
    assign_tokens = [token for token in token_list if token.spelling == "="]
    if len(assign_tokens) != 1:
        return None

    decl_text = source_text[node.extent.start.offset : node.extent.end.offset]
    if "," in decl_text:
        return None

    state.init_data_flow_names()
    struct_name = state.DATA_FLOW_STRUCT_NAME

    eq_idx = decl_text.find("=")
    before = decl_text[:eq_idx].strip()
    after = decl_text[eq_idx + 1 :].strip().rstrip(";")
    parts = before.split()
    if len(parts) < 2:
        return None
    var_name = parts[-1]
    type_name = " ".join(parts[:-1])
    obf_var = name_map.get(var_name, var_name)
    after_expr = apply_name_map_to_fragment(after, name_map)
    return f"{struct_name}<{type_name}> {obf_var} = {struct_name}<{type_name}>::pack({after_expr});"


def collect_ast_replacements(
    node,
    target_realpath: str,
    name_map: dict,
    source_text: str,
    replacements: list,
    _stats: dict | None = None,
    _root: bool = True,
) -> None:
    if _stats is None:
        _stats = {
            "memory_access": 0,
            "stl_sort": 0,
            "fnptr_call": 0,
            "type_level": 0,
            "data_flow": 0,
            "tmp_addition": 0,
            "visited": 0,
        }
    _stats["visited"] += 1
    if not node.location.file:
        return

    if normalize_path(node.location.file.name) != target_realpath:
        return

    if (
        node.kind == clang.cindex.CursorKind.ARRAY_SUBSCRIPT_EXPR
        and config.ENABLE_MEMORY_ACCESS_OBFUSCATION
        and is_pointer_like_cursor_type(node)
    ):
        memory_replacement = get_memory_access_replacement(node, source_text, name_map)
        if memory_replacement is not None:
            replacements.append((node.extent.start.offset, node.extent.end.offset, memory_replacement))
            _stats["memory_access"] += 1
            return

    elif node.kind == clang.cindex.CursorKind.CALL_EXPR and (
        config.ENABLE_STL_WRAPPER_REWRITES or config.ENABLE_FUNCTION_POINTER_INDIRECTION
    ):
        call_tokens = list(node.get_tokens())
        if call_tokens:
            token_spellings = [token.spelling for token in call_tokens]
            if (
                config.ENABLE_STL_WRAPPER_REWRITES
                and len(call_tokens) == 14
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
                    (
                        node.extent.start.offset,
                        node.extent.end.offset,
                        f"{state.STL_SORT_HELPER_NAME}({container_expr})",
                    )
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
            (
                node.extent.start.offset,
                node.extent.end.offset,
                render_recursive_expression(node, source_text, name_map),
            )
        )
        _stats["tmp_addition"] += 1
        return

    for child in node.get_children():
        collect_ast_replacements(child, target_realpath, name_map, source_text, replacements, _stats=_stats, _root=False)

    if _root:
        vlog(
            "ast",
            "visited={visited}, replacements: mem={memory_access}, stl_sort={stl_sort}, fnptr={fnptr_call}, type_level={type_level}, data_flow={data_flow}, tmp_add={tmp_addition}".format(
                **_stats
            ),
        )


def inject_memory_access_helpers(source_text: str) -> str:
    if not config.ENABLE_MEMORY_ACCESS_OBFUSCATION:
        vlog("mem", "disabled")
        return source_text
    if state.MEMORY_INDEX_HELPER_NAME is None:
        vlog("mem", "no AST rewrites created memory helpers; skip injection")
        return source_text

    state.init_memory_access_names()
    ptr_name = generate_barcode_name(18)
    idx_name = generate_barcode_name(18)
    helper_block = "\n".join(
        [
            "namespace {",
            f"template <typename T> __attribute__((noinline, noipa)) T* {state.MEMORY_INDEX_HELPER_NAME}(T* {ptr_name}, std::size_t {idx_name}) {{",
            f"    return {ptr_name} + {idx_name};",
            "}",
            f"template <typename T> __attribute__((noinline, noipa)) T* {state.MEMORY_PTR_ADVANCE_HELPER_NAME}(T* {ptr_name}, std::ptrdiff_t {idx_name}) {{",
            f"    return {ptr_name} + {idx_name};",
            "}",
            "}",
            "",
        ]
    )

    lines = source_text.splitlines(keepends=True)
    insert_line_index = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            insert_line_index = i + 1
            continue
        if stripped.startswith("#"):
            insert_line_index = i + 1
            continue
        break
    out = "".join(lines[:insert_line_index]) + helper_block + "".join(lines[insert_line_index:])
    vlog("mem", f"injected helpers index={state.MEMORY_INDEX_HELPER_NAME} adv={state.MEMORY_PTR_ADVANCE_HELPER_NAME}, bytes {len(source_text)} -> {len(out)}")
    return out


def inject_stl_wrappers(source_text: str) -> str:
    if not config.ENABLE_STL_WRAPPER_REWRITES:
        vlog("stl", "disabled")
        return source_text
    if state.STL_SORT_HELPER_NAME is None:
        vlog("stl", "no AST rewrites requested STL wrapper; skip injection")
        return source_text

    state.init_stl_helper_names()
    container_name = generate_barcode_name(18)
    helper_block = "\n".join(
        [
            "#include <algorithm>",
            "namespace {",
            f"template <typename T> __attribute__((noinline, noipa)) void {state.STL_SORT_HELPER_NAME}(T& {container_name}) {{",
            f"    std::sort({container_name}.begin(), {container_name}.end());",
            "}",
            "}",
            "",
        ]
    )

    lines = source_text.splitlines(keepends=True)
    insert_line_index = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            insert_line_index = i + 1
            continue
        if stripped.startswith("#"):
            insert_line_index = i + 1
            continue
        break
    out = "".join(lines[:insert_line_index]) + helper_block + "".join(lines[insert_line_index:])
    vlog("stl", f"injected sort wrapper={state.STL_SORT_HELPER_NAME}, bytes {len(source_text)} -> {len(out)}")
    return out


def inject_dead_code_helpers(source_text: str) -> str:
    if not config.ENABLE_DEAD_CODE_INJECTION:
        vlog("dead_helpers", "disabled")
        return source_text
    if not state.DEAD_CODE_HELPER_NAMES:
        vlog("dead_helpers", "no helper names initialized; skip injection")
        return source_text

    state.init_dead_code_helper_names()
    helper_a, helper_b = state.DEAD_CODE_HELPER_NAMES
    value_name = generate_barcode_name(18)
    helper_block = "\n".join(
        [
            "namespace {",
            f"__attribute__((noinline, noipa)) int {helper_a}(int {value_name}) {{ return ({value_name} * 33) ^ 0x55; }}",
            f"__attribute__((noinline, noipa)) int {helper_b}(int {value_name}) {{ return ({value_name} * 17) ^ 0x33; }}",
            "}",
            "",
        ]
    )

    lines = source_text.splitlines(keepends=True)
    insert_line_index = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            insert_line_index = i + 1
            continue
        if stripped.startswith("#"):
            insert_line_index = i + 1
            continue
        break
    out = "".join(lines[:insert_line_index]) + helper_block + "".join(lines[insert_line_index:])
    vlog("dead_helpers", f"injected helpers={state.DEAD_CODE_HELPER_NAMES}, bytes {len(source_text)} -> {len(out)}")
    return out


def inject_tmp_addition_helpers(source_text: str) -> str:
    if not config.ENABLE_TMP_ADDITION_OBFUSCATION:
        vlog("tmp_add", "disabled")
        return source_text
    if state.TMP_ADD_STRUCT_NAME is None or state.TMP_ADD_HELPER_NAME is None:
        vlog("tmp_add", "no helper names initialized; skip injection")
        return source_text

    state.init_tmp_addition_names()
    value_name = generate_barcode_name(18)
    helper_block = "\n".join(
        [
            "namespace {",
            f"template <typename T> struct {state.TMP_ADD_STRUCT_NAME} {{",
            f"    __attribute__((noinline, noipa)) static T add(T a, T b) {{ return a + b; }}",
            "};",
            f"template <typename T> __attribute__((noinline, noipa)) T {state.TMP_ADD_HELPER_NAME}(T a, T b) {{",
            f"    return {state.TMP_ADD_STRUCT_NAME}<T>::add(a, b);",
            "}",
            "}",
            "",
        ]
    )

    lines = source_text.splitlines(keepends=True)
    insert_line_index = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            insert_line_index = i + 1
            continue
        if stripped.startswith("#"):
            insert_line_index = i + 1
            continue
        break
    out = "".join(lines[:insert_line_index]) + helper_block + "".join(lines[insert_line_index:])
    vlog("tmp_add", f"injected struct={state.TMP_ADD_STRUCT_NAME} helper={state.TMP_ADD_HELPER_NAME}, bytes {len(source_text)} -> {len(out)}")
    return out


def inject_function_pointer_helpers(source_text: str) -> str:
    if not config.ENABLE_FUNCTION_POINTER_INDIRECTION:
        vlog("fnptr", "disabled")
        return source_text
    if state.FUNCTION_PTR_STAGE_HELPER_NAME is None or state.FUNCTION_PTR_INVOKE_HELPER_NAME is None:
        vlog("fnptr", "no helper names initialized; skip injection")
        return source_text

    state.init_function_pointer_helper_names()
    fn_name = generate_barcode_name(18)
    helper_block = "\n".join(
        [
            "namespace {",
            f"template <typename Fn> __attribute__((noinline, noipa)) Fn {state.FUNCTION_PTR_STAGE_HELPER_NAME}(Fn {fn_name}) {{ return {fn_name}; }}",
            f"template <typename Fn, typename... Args> __attribute__((noinline, noipa)) auto {state.FUNCTION_PTR_INVOKE_HELPER_NAME}(Fn {fn_name}, Args... args) -> decltype({fn_name}(args...)) {{",
            f"    return {fn_name}(args...);",
            "}",
            "}",
            "",
        ]
    )

    lines = source_text.splitlines(keepends=True)
    insert_line_index = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            insert_line_index = i + 1
            continue
        if stripped.startswith("#"):
            insert_line_index = i + 1
            continue
        break
    out = "".join(lines[:insert_line_index]) + helper_block + "".join(lines[insert_line_index:])
    vlog("fnptr", f"injected stage={state.FUNCTION_PTR_STAGE_HELPER_NAME} invoke={state.FUNCTION_PTR_INVOKE_HELPER_NAME}, bytes {len(source_text)} -> {len(out)}")
    return out


def inject_data_flow_helpers(source_text: str) -> str:
    if not config.ENABLE_DATA_FLOW_OBFUSCATION:
        vlog("dataflow", "disabled")
        return source_text
    if state.DATA_FLOW_STRUCT_NAME is None:
        vlog("dataflow", "no helper name initialized; skip injection")
        return source_text

    state.init_data_flow_names()
    struct_name = state.DATA_FLOW_STRUCT_NAME
    helper_block = "\n".join(
        [
            "#include <cstdint>",
            "namespace {",
            f"template <typename T> struct {struct_name} {{",
            "    T a{};",
            "    T b{};",
            "    __attribute__((noinline, noipa)) static " + struct_name + "<T> pack(T value) { return {value, static_cast<T>(0)}; }",
            "    __attribute__((noinline, noipa)) T unpack() const { return a; }",
            "};",
            "}",
            "",
        ]
    )

    lines = source_text.splitlines(keepends=True)
    insert_line_index = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            insert_line_index = i + 1
            continue
        if stripped.startswith("#"):
            insert_line_index = i + 1
            continue
        break
    out = "".join(lines[:insert_line_index]) + helper_block + "".join(lines[insert_line_index:])
    vlog("dataflow", f"injected struct={state.DATA_FLOW_STRUCT_NAME}, bytes {len(source_text)} -> {len(out)}")
    return out


def inject_type_level_helpers(source_text: str) -> str:
    """Injects primitive wrapper types with overloaded operators."""
    if not config.ENABLE_TYPE_LEVEL_OBFUSCATION:
        vlog("typelevel", "disabled")
        return source_text
    if state.OPAQUE_WRAPPER_NAME is None:
        vlog("typelevel", "no wrapper name initialized; skip injection")
        return source_text

    state.init_type_level_names()
    variant = state.OPAQUE_WRAPPER_VARIANT or {"key_a": 0x55, "key_b": 0x33}
    value_name = generate_barcode_name(18)
    storage_name = generate_barcode_name(18)
    key_a = variant["key_a"]
    key_b = variant["key_b"]
    rhs_name = generate_barcode_name(18)

    helper_block = "\n".join(
        [
            "#include <type_traits>",
            "namespace {",
            f"template <typename T> struct {state.OPAQUE_WRAPPER_NAME} {{",
            f"    T {storage_name}{{}};",
            f"    constexpr {state.OPAQUE_WRAPPER_NAME}() = default;",
            f"    constexpr explicit {state.OPAQUE_WRAPPER_NAME}(T {value_name}) : {storage_name}(encode({value_name})) {{}}",
            f"    static constexpr T encode(T value) {{ return static_cast<T>((static_cast<unsigned long long>(value) ^ {key_a}) + {key_b}); }}",
            f"    static constexpr T decode(T value) {{ return static_cast<T>((static_cast<unsigned long long>(value) - {key_b}) ^ {key_a}); }}",
            f"    constexpr T value() const {{ return decode({storage_name}); }}",
            "    constexpr operator T() const { return value(); }",
            f"    {state.OPAQUE_WRAPPER_NAME}& operator=(T {value_name}) {{ {storage_name} = encode({value_name}); return *this; }}",
            f"    {state.OPAQUE_WRAPPER_NAME}& operator++() {{ *this = static_cast<T>(value() + 1); return *this; }}",
            f"    {state.OPAQUE_WRAPPER_NAME} operator++(int) {{ auto copy = *this; ++(*this); return copy; }}",
            f"    {state.OPAQUE_WRAPPER_NAME}& operator+=(T {rhs_name}) {{ *this = static_cast<T>(value() + {rhs_name}); return *this; }}",
            f"    {state.OPAQUE_WRAPPER_NAME}& operator-=(T {rhs_name}) {{ *this = static_cast<T>(value() - {rhs_name}); return *this; }}",
            "};",
            f"template <typename T, typename U> auto operator+(const {state.OPAQUE_WRAPPER_NAME}<T>& lhs, const U& rhs) -> {state.OPAQUE_WRAPPER_NAME}<std::common_type_t<T, U>> {{ using R = std::common_type_t<T, U>; return {state.OPAQUE_WRAPPER_NAME}<R>(static_cast<R>(lhs.value()) + static_cast<R>(rhs)); }}",
            f"template <typename T, typename U> auto operator-(const {state.OPAQUE_WRAPPER_NAME}<T>& lhs, const U& rhs) -> {state.OPAQUE_WRAPPER_NAME}<std::common_type_t<T, U>> {{ using R = std::common_type_t<T, U>; return {state.OPAQUE_WRAPPER_NAME}<R>(static_cast<R>(lhs.value()) - static_cast<R>(rhs)); }}",
            f"template <typename T, typename U> auto operator*(const {state.OPAQUE_WRAPPER_NAME}<T>& lhs, const U& rhs) -> {state.OPAQUE_WRAPPER_NAME}<std::common_type_t<T, U>> {{ using R = std::common_type_t<T, U>; return {state.OPAQUE_WRAPPER_NAME}<R>(static_cast<R>(lhs.value()) * static_cast<R>(rhs)); }}",
            f"template <typename T, typename U> auto operator/(const {state.OPAQUE_WRAPPER_NAME}<T>& lhs, const U& rhs) -> {state.OPAQUE_WRAPPER_NAME}<std::common_type_t<T, U>> {{ using R = std::common_type_t<T, U>; return {state.OPAQUE_WRAPPER_NAME}<R>(static_cast<R>(lhs.value()) / static_cast<R>(rhs)); }}",
            f"template <typename T, typename U> bool operator==(const {state.OPAQUE_WRAPPER_NAME}<T>& lhs, const U& rhs) {{ return lhs.value() == static_cast<T>(rhs); }}",
            f"template <typename T, typename U> bool operator!=(const {state.OPAQUE_WRAPPER_NAME}<T>& lhs, const U& rhs) {{ return lhs.value() != static_cast<T>(rhs); }}",
            f"template <typename T, typename U> bool operator<(const {state.OPAQUE_WRAPPER_NAME}<T>& lhs, const U& rhs) {{ return lhs.value() < static_cast<T>(rhs); }}",
            f"template <typename T, typename U> bool operator<=(const {state.OPAQUE_WRAPPER_NAME}<T>& lhs, const U& rhs) {{ return lhs.value() <= static_cast<T>(rhs); }}",
            f"template <typename T, typename U> bool operator>(const {state.OPAQUE_WRAPPER_NAME}<T>& lhs, const U& rhs) {{ return lhs.value() > static_cast<T>(rhs); }}",
            f"template <typename T, typename U> bool operator>=(const {state.OPAQUE_WRAPPER_NAME}<T>& lhs, const U& rhs) {{ return lhs.value() >= static_cast<T>(rhs); }}",
            "}",
            "",
        ]
    )

    lines = source_text.splitlines(keepends=True)
    insert_line_index = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            insert_line_index = i + 1
            continue
        if stripped.startswith("#"):
            insert_line_index = i + 1
            continue
        break
    out = "".join(lines[:insert_line_index]) + helper_block + "".join(lines[insert_line_index:])
    vlog("typelevel", f"injected wrapper={state.OPAQUE_WRAPPER_NAME}, bytes {len(source_text)} -> {len(out)}")
    return out
