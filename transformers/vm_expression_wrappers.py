import re

import clang.cindex

import config
import state
from util import generate_barcode_name, is_local, vlog


WRAPPER_KINDS = {
    clang.cindex.CursorKind.UNEXPOSED_EXPR,
    clang.cindex.CursorKind.PAREN_EXPR,
}
CAST_KINDS = {
    clang.cindex.CursorKind.CSTYLE_CAST_EXPR,
    clang.cindex.CursorKind.CXX_STATIC_CAST_EXPR,
    clang.cindex.CursorKind.CXX_FUNCTIONAL_CAST_EXPR,
}
FUNCTION_KINDS = {
    clang.cindex.CursorKind.FUNCTION_DECL,
    clang.cindex.CursorKind.CXX_METHOD,
    clang.cindex.CursorKind.CONSTRUCTOR,
    clang.cindex.CursorKind.CONVERSION_FUNCTION,
}
LOOP_KINDS = {
    clang.cindex.CursorKind.FOR_STMT,
    clang.cindex.CursorKind.WHILE_STMT,
    clang.cindex.CursorKind.DO_STMT,
}
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
FLOAT_TYPE_KINDS = {
    clang.cindex.TypeKind.FLOAT,
    clang.cindex.TypeKind.DOUBLE,
}
BLOCKED_TYPE_FRAGMENTS = {
    "istream",
    "ostream",
    "stream",
    "string",
    "vector",
    "deque",
    "queue",
    "stack",
    "map",
    "set",
    "tuple",
    "optional",
    "variant",
    "std",
}
SUPPORTED_BINARY_OPS = {
    "+",
    "-",
    "*",
    "/",
}


def _unwrap_expr(cursor):
    node = cursor
    while node.kind in WRAPPER_KINDS:
        children = [
            child for child in node.get_children()
            if child.extent.start.offset < child.extent.end.offset
        ]
        if len(children) != 1:
            break
        node = children[0]
    return node


def _extract_binary_operator(cursor, source_text):
    children = [
        child for child in cursor.get_children()
        if child.extent.start.offset < child.extent.end.offset
    ]
    if len(children) != 2:
        return None
    left_end = children[0].extent.end.offset
    right_start = children[1].extent.start.offset
    for token in cursor.get_tokens():
        token_start = token.extent.start.offset
        token_end = token.extent.end.offset
        if token_start >= left_end and token_end <= right_start and token.spelling in SUPPORTED_BINARY_OPS:
            return token.spelling
    return None


def _extract_unary_operator(cursor, source_text):
    children = [
        child for child in cursor.get_children()
        if child.extent.start.offset < child.extent.end.offset
    ]
    if len(children) != 1:
        return None
    child = children[0]
    if child.extent.start.offset > cursor.extent.start.offset:
        prefix = re.sub(r"\s+", "", source_text[cursor.extent.start.offset:child.extent.start.offset])
        if prefix:
            return prefix
    if child.extent.end.offset < cursor.extent.end.offset:
        suffix = re.sub(r"\s+", "", source_text[child.extent.end.offset:cursor.extent.end.offset])
        if suffix:
            return suffix
    return None


def _is_supported_type(type_obj):
    spelling = type_obj.spelling.replace("const", " ").replace("&", " ").replace("*", " ").strip()
    if any(fragment in spelling for fragment in BLOCKED_TYPE_FRAGMENTS):
        return False
    canonical = type_obj.get_canonical()
    return canonical.kind in INTEGRAL_TYPE_KINDS or canonical.kind in FLOAT_TYPE_KINDS


def _is_side_effect_free(cursor, source_text):
    node = _unwrap_expr(cursor)
    kind = node.kind

    if kind in {
        clang.cindex.CursorKind.DECL_REF_EXPR,
        clang.cindex.CursorKind.INTEGER_LITERAL,
        clang.cindex.CursorKind.FLOATING_LITERAL,
        clang.cindex.CursorKind.CXX_BOOL_LITERAL_EXPR,
    }:
        return True

    if kind in CAST_KINDS:
        children = [child for child in node.get_children() if child.extent.start.offset < child.extent.end.offset]
        return len(children) == 1 and _is_side_effect_free(children[0], source_text)

    if kind == clang.cindex.CursorKind.BINARY_OPERATOR:
        children = [child for child in node.get_children() if child.extent.start.offset < child.extent.end.offset]
        if len(children) != 2:
            return False
        operator_text = _extract_binary_operator(node, source_text)
        return (
            operator_text in SUPPORTED_BINARY_OPS
            and _is_supported_type(node.type)
            and all(_is_supported_type(child.type) for child in children)
            and all(_is_side_effect_free(child, source_text) for child in children)
        )

    if kind == clang.cindex.CursorKind.UNARY_OPERATOR:
        children = [child for child in node.get_children() if child.extent.start.offset < child.extent.end.offset]
        if len(children) != 1:
            return False
        return _extract_unary_operator(node, source_text) in {"+", "-", "~"} and _is_side_effect_free(children[0], source_text)

    return False


def _is_supported_wrapper_expression(cursor, source_text, in_function, in_loop):
    node = _unwrap_expr(cursor)
    if node.kind != clang.cindex.CursorKind.BINARY_OPERATOR:
        return False
    if not in_function:
        return False
    if in_loop:
        return False

    children = [
        child for child in node.get_children()
        if child.extent.start.offset < child.extent.end.offset
    ]
    if len(children) != 2:
        return False

    operator_text = _extract_binary_operator(node, source_text)
    if operator_text not in SUPPORTED_BINARY_OPS:
        return False

    if not _is_supported_type(node.type):
        return False
    if not all(_is_supported_type(child.type) for child in children):
        return False
    if not all(_is_side_effect_free(child, source_text) for child in children):
        return False

    return True


def _build_helper_definition(name, return_type, lhs_type, rhs_type, operator_text):
    lhs_name = generate_barcode_name(18)
    rhs_name = generate_barcode_name(18)
    return "\n".join(
        [
            f"__attribute__((noinline, noipa)) static {return_type} {name}({lhs_type} {lhs_name}, {rhs_type} {rhs_name}) {{",
            f"    return {lhs_name} {operator_text} {rhs_name};",
            "}",
            "",
        ]
    )


def _render_helper_type(type_obj):
    return type_obj.get_canonical().spelling


def _collect_wrapper_replacements(
    node,
    target_realpath,
    source_text,
    replacements,
    helper_defs,
    stats,
    in_function=False,
    in_loop=False,
):
    is_translation_unit = node.kind == clang.cindex.CursorKind.TRANSLATION_UNIT
    if not is_translation_unit and not is_local(node, target_realpath):
        return

    next_in_function = in_function or node.kind in FUNCTION_KINDS
    next_in_loop = in_loop or node.kind in LOOP_KINDS

    if _is_supported_wrapper_expression(node, source_text, next_in_function, next_in_loop):
        children = [
            child for child in _unwrap_expr(node).get_children()
            if child.extent.start.offset < child.extent.end.offset
        ]
        lhs, rhs = children
        helper_name = generate_barcode_name(18)
        operator_text = _extract_binary_operator(node, source_text)
        lhs_text = source_text[lhs.extent.start.offset:lhs.extent.end.offset]
        rhs_text = source_text[rhs.extent.start.offset:rhs.extent.end.offset]
        replacements.append(
            (
                node.extent.start.offset,
                node.extent.end.offset,
                f"{helper_name}(({lhs_text}), ({rhs_text}))",
            )
        )
        state.VM_EXPRESSION_WRAPPER_NAMES.add(helper_name)
        helper_defs.append(
            _build_helper_definition(
                helper_name,
                _render_helper_type(node.type),
                _render_helper_type(lhs.type),
                _render_helper_type(rhs.type),
                operator_text,
            )
        )
        stats["wrapped"] += 1
        return

    for child in node.get_children():
        _collect_wrapper_replacements(
            child,
            target_realpath,
            source_text,
            replacements,
            helper_defs,
            stats,
            in_function=next_in_function,
            in_loop=next_in_loop,
        )


def _inject_helpers(source_text, helper_defs):
    if not helper_defs:
        return source_text

    helper_block = "namespace {\n" + "".join(helper_defs) + "}\n\n"
    lines = source_text.splitlines(keepends=True)
    insert_line_index = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            insert_line_index = index + 1
            continue
        if stripped.startswith("#"):
            insert_line_index = index + 1
            continue
        break
    return "".join(lines[:insert_line_index]) + helper_block + "".join(lines[insert_line_index:])


def apply_vm_expression_wrappers(tu_cursor, target_realpath, source_text):
    if not config.ENABLE_VM_EXPRESSION_WRAPPERS:
        vlog("vm_wrap", "disabled")
        return source_text

    replacements = []
    helper_defs = []
    stats = {"wrapped": 0}
    _collect_wrapper_replacements(tu_cursor, target_realpath, source_text, replacements, helper_defs, stats)
    if not replacements:
        vlog("vm_wrap", "wrapped=0")
        return source_text

    content = list(source_text)
    for start, end, new_val in sorted(replacements, key=lambda item: item[0], reverse=True):
        content[start:end] = list(new_val)
    rewritten = "".join(content)
    out = _inject_helpers(rewritten, helper_defs)
    vlog("vm_wrap", f"wrapped={stats['wrapped']}, bytes {len(source_text)} -> {len(out)}")
    return out
