import os
import random
import re
import struct

import clang.cindex

import config
import state
from util import (
    VM_SKIP_MARKER,
    generate_barcode_name,
    is_local,
    parse_integer_literal,
    strip_comments,
    vlog,
)


WRAPPER_KINDS = {
    clang.cindex.CursorKind.UNEXPOSED_EXPR,
    clang.cindex.CursorKind.PAREN_EXPR,
}
CAST_KINDS = {
    clang.cindex.CursorKind.CSTYLE_CAST_EXPR,
    clang.cindex.CursorKind.CXX_STATIC_CAST_EXPR,
    clang.cindex.CursorKind.CXX_FUNCTIONAL_CAST_EXPR,
}
SIGNED_TYPES = {
    "bool",
    "char",
    "signed char",
    "short",
    "short int",
    "signed short",
    "signed short int",
    "int",
    "signed",
    "signed int",
    "long",
    "long int",
    "signed long",
    "signed long int",
    "long long",
    "long long int",
    "signed long long",
    "signed long long int",
}
UNSIGNED_TYPES = {
    "unsigned char",
    "unsigned short",
    "unsigned short int",
    "unsigned",
    "unsigned int",
    "unsigned long",
    "unsigned long int",
    "unsigned long long",
    "unsigned long long int",
    "size_t",
    "std::size_t",
}
FLOAT_TYPES = {"float", "double"}
RETURN_KIND_INT = 0
RETURN_KIND_FP = 1
RUNTIME_IDENTIFIER_PATTERN = re.compile(r"\b[A-Za-z_]\w*\b")
RUNTIME_IDENTIFIER_EXCLUDES = {
    "alignas", "alignof", "asm", "auto", "bool", "break", "case", "catch", "char",
    "class", "const", "constexpr", "const_cast", "continue", "decltype", "default",
    "delete", "do", "double", "else", "enum", "explicit", "export", "extern",
    "false", "float", "for", "friend", "goto", "if", "inline", "int", "long",
    "mutable", "namespace", "new", "noexcept", "nullptr", "operator", "private",
    "protected", "public", "register", "reinterpret_cast", "return", "short",
    "signed", "sizeof", "static", "static_assert", "static_cast", "struct", "switch",
    "template", "this", "throw", "true", "try", "typedef", "typeid", "typename",
    "union", "unsigned", "using", "virtual", "void", "volatile", "while",
    "std", "size_t", "uint8_t", "uint16_t", "uint32_t", "uint64_t", "int32_t",
    "int64_t", "double_t", "float_t", "memcpy", "sqrt", "sin", "cos", "tan",
    "atan2", "pow", "log", "exp", "floor", "ceil", "fabs", "round", "trunc",
    "array", "tuple", "index_sequence", "index_sequence_for", "apply", "isnan",
    "numeric_limits", "quiet_NaN", "nullptr_t",
    "__asm__", "__volatile__", "__attribute__", "__builtin_trap", "__debugbreak",
    "__GNUC__", "__clang__", "_MSC_VER", "ifdef", "elif", "else", "endif",
    "include", "define", "noinline", "noipa", "optimize", "execute",
}


def _unwrap_expr(cursor):
    node = cursor
    while node.kind in WRAPPER_KINDS:
        children = list(node.get_children())
        if len(children) != 1:
            break
        node = children[0]
    return node


def _canonical_type_spelling(type_obj):
    return type_obj.get_canonical().spelling.replace("_Bool", "bool").strip()


def _is_integral_type(type_obj):
    return _canonical_type_spelling(type_obj) in SIGNED_TYPES | UNSIGNED_TYPES


def _is_unsigned_type(type_obj):
    return _canonical_type_spelling(type_obj) in UNSIGNED_TYPES


def _is_fp_type(type_obj):
    return _canonical_type_spelling(type_obj) in FLOAT_TYPES


def _is_pointer_type(type_obj):
    return type_obj.get_canonical().kind == clang.cindex.TypeKind.POINTER


def _type_class(type_obj):
    if _is_fp_type(type_obj):
        return "fp"
    return "int"


def _extract_binary_operator(cursor, source_text):
    children = list(cursor.get_children())
    if len(children) != 2:
        return None
    between = source_text[children[0].extent.end.offset:children[1].extent.start.offset]
    match = re.search(r"(<<=|>>=|==|!=|<=|>=|\|\||&&|\+=|-=|\*=|/=|%=|<<|>>|[+\-*/%&|^<>=])", between)
    return match.group(1) if match else None


def _extract_unary_operator(cursor, source_text):
    children = list(cursor.get_children())
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


def _extract_integer_literal(cursor):
    tokens = list(cursor.get_tokens())
    if not tokens:
        return None
    return parse_integer_literal(tokens[0].spelling)


def _extract_floating_literal(cursor):
    tokens = list(cursor.get_tokens())
    if not tokens:
        return None
    spelling = tokens[0].spelling.rstrip("fFlL")
    try:
        return float(spelling)
    except ValueError:
        return None


def _extract_bool_literal(cursor):
    tokens = list(cursor.get_tokens())
    if not tokens:
        return None
    if tokens[0].spelling == "true":
        return 1
    if tokens[0].spelling == "false":
        return 0
    return None


def _function_parameters(cursor):
    return [child for child in cursor.get_children() if child.kind == clang.cindex.CursorKind.PARM_DECL]


def _get_function_body_cursor(cursor):
    for child in cursor.get_children():
        if child.kind == clang.cindex.CursorKind.COMPOUND_STMT:
            return child
    return None


def _type_is_vm_supported(type_obj):
    return _is_integral_type(type_obj) or _is_fp_type(type_obj) or _is_pointer_type(type_obj)


def _is_function_signature_supported(cursor):
    if not _type_is_vm_supported(cursor.result_type):
        return False
    params = _function_parameters(cursor)
    if len(params) > 8:
        return False
    for param in params:
        canonical = param.type.get_canonical()
        if canonical.kind in {clang.cindex.TypeKind.LVALUEREFERENCE, clang.cindex.TypeKind.RVALUEREFERENCE}:
            return False
        if not _type_is_vm_supported(param.type):
            return False
    return True


def _escape_type_for_cast(type_spelling):
    return type_spelling.strip()


def _render_arg_unpack(param_type, int_index_name, fp_index_name):
    type_spelling = _escape_type_for_cast(param_type.spelling)
    if _is_fp_type(param_type):
        return f"static_cast<{type_spelling}>({{fps}}[{fp_index_name}++])"
    if _is_pointer_type(param_type):
        return f"reinterpret_cast<{type_spelling}>({{ints}}[{int_index_name}++])"
    return f"static_cast<{type_spelling}>({{ints}}[{int_index_name}++])"


class BytecodeBuilder:
    OPCODES = {
        "PUSH_IMM64": 0x02,
        "PUSH_REG": 0x03,
        "POP_REG": 0x04,
        "F_PUSH_IMM64": 0x07,
        "F_PUSH_REG": 0x08,
        "F_POP_REG": 0x09,
        "I2F": 0x0B,
        "F2I": 0x0C,
        "LOAD8": 0x10,
        "LOAD16": 0x11,
        "LOAD32": 0x12,
        "LOAD64": 0x13,
        "STORE8": 0x14,
        "STORE16": 0x15,
        "STORE32": 0x16,
        "STORE64": 0x17,
        "F_LOAD32": 0x18,
        "F_LOAD64": 0x19,
        "F_STORE32": 0x1A,
        "F_STORE64": 0x1B,
        "ADD": 0x20,
        "SUB": 0x21,
        "MUL": 0x22,
        "DIV": 0x23,
        "MOD": 0x24,
        "SHL": 0x25,
        "SHR": 0x26,
        "SAR": 0x27,
        "F_ADD": 0x28,
        "F_SUB": 0x29,
        "F_MUL": 0x2A,
        "F_DIV": 0x2B,
        "F_SQRT": 0x2C,
        "AND": 0x30,
        "OR": 0x31,
        "XOR": 0x32,
        "NOT": 0x33,
        "EQ": 0x40,
        "NE": 0x41,
        "LT": 0x42,
        "LE": 0x43,
        "GT": 0x44,
        "GE": 0x45,
        "LT_U": 0x46,
        "LE_U": 0x47,
        "GT_U": 0x48,
        "GE_U": 0x49,
        "F_EQ": 0x4A,
        "F_NE": 0x4B,
        "F_LT": 0x4C,
        "F_LE": 0x4D,
        "F_GT": 0x4E,
        "F_GE": 0x4F,
        "JMP": 0x51,
        "JZ": 0x52,
        "HALT": 0xFF,
        "CALL_NATIVE_PACKED": 0x63,
    }

    def __init__(self):
        self.bytes = []
        self.labels = {}
        self.fixups = []
        self.label_index = 0

    def emit_opcode(self, opcode_name):
        self.bytes.append(self.OPCODES[opcode_name])

    def emit_u8(self, value):
        self.bytes.append(value & 0xFF)

    def emit_u32(self, value):
        encoded = value & 0xFFFFFFFF
        for shift in range(0, 32, 8):
            self.bytes.append((encoded >> shift) & 0xFF)

    def emit_u64(self, value):
        encoded = value & 0xFFFFFFFFFFFFFFFF
        for shift in range(0, 64, 8):
            self.bytes.append((encoded >> shift) & 0xFF)

    def emit_push_imm64(self, value):
        self.emit_opcode("PUSH_IMM64")
        self.emit_u64(value)

    def emit_f_push_imm64(self, value):
        self.emit_opcode("F_PUSH_IMM64")
        bits = struct.unpack("<Q", struct.pack("<d", float(value)))[0]
        self.emit_u64(bits)

    def emit_push_reg(self, reg_index, stack_class):
        self.emit_opcode("F_PUSH_REG" if stack_class == "fp" else "PUSH_REG")
        self.emit_u8(reg_index)

    def emit_pop_reg(self, reg_index, stack_class):
        self.emit_opcode("F_POP_REG" if stack_class == "fp" else "POP_REG")
        self.emit_u8(reg_index)

    def emit_simple(self, opcode_name):
        self.emit_opcode(opcode_name)

    def emit_jump(self, opcode_name, label):
        self.emit_opcode(opcode_name)
        fixup_at = len(self.bytes)
        self.emit_u32(0)
        self.fixups.append((fixup_at, label))

    def emit_call_native_packed(self, func_idx, int_count, fp_count, return_kind):
        self.emit_opcode("CALL_NATIVE_PACKED")
        self.emit_u32(func_idx)
        self.emit_u32(int_count)
        self.emit_u32(fp_count)
        self.emit_u8(return_kind)

    def new_label(self):
        label = f"vm_label_{self.label_index}"
        self.label_index += 1
        return label

    def mark_label(self, label):
        self.labels[label] = len(self.bytes)

    def finalize(self):
        for fixup_at, label in self.fixups:
            if label not in self.labels:
                raise ValueError(f"Unknown label {label}")
            target = self.labels[label]
            offset = target - (fixup_at + 4)
            self.bytes[fixup_at:fixup_at + 4] = list((offset & 0xFFFFFFFF).to_bytes(4, "little", signed=False))
        return self.bytes


class VmCompileContext:
    def __init__(self, function_cursor, source_text, name_map):
        self.function_cursor = function_cursor
        self.source_text = source_text
        self.name_map = name_map
        self.builder = BytecodeBuilder()
        self.int_registers = {}
        self.fp_registers = {}
        self.int_types = {}
        self.fp_types = {}
        self.next_int_reg = 12
        self.next_fp_reg = 12
        self.native_functions = []
        self.native_function_lookup = {}
        self.break_targets = []
        self.continue_targets = []

    def reserve_symbol(self, cursor, preferred_reg=None):
        symbol_type = cursor.type
        if _type_class(symbol_type) == "fp":
            reg_index = preferred_reg if preferred_reg is not None else self.next_fp_reg
            if preferred_reg is None:
                self.next_fp_reg += 1
            if reg_index >= 32:
                raise ValueError("Out of FP registers")
            self.fp_registers[cursor.spelling] = reg_index
            self.fp_types[cursor.spelling] = symbol_type
            return "fp", reg_index
        reg_index = preferred_reg if preferred_reg is not None else self.next_int_reg
        if preferred_reg is None:
            self.next_int_reg += 1
        if reg_index >= 32:
            raise ValueError("Out of integer registers")
        self.int_registers[cursor.spelling] = reg_index
        self.int_types[cursor.spelling] = symbol_type
        return "int", reg_index

    def lookup_symbol(self, name):
        if name in self.fp_registers:
            return "fp", self.fp_registers[name], self.fp_types[name]
        if name in self.int_registers:
            return "int", self.int_registers[name], self.int_types[name]
        return None

    def register_native_function(self, cursor):
        key = cursor.get_usr() or cursor.spelling
        if key in self.native_function_lookup:
            return self.native_function_lookup[key]
        index = len(self.native_functions)
        bridge_name = generate_barcode_name(18)
        self.native_functions.append({"cursor": cursor, "bridge_name": bridge_name})
        self.native_function_lookup[key] = index
        return index

    def push_break_target(self, label):
        self.break_targets.append(label)

    def pop_break_target(self):
        self.break_targets.pop()

    def current_break_target(self):
        return self.break_targets[-1] if self.break_targets else None

    def push_continue_target(self, label):
        self.continue_targets.append(label)

    def pop_continue_target(self):
        self.continue_targets.pop()

    def current_continue_target(self):
        return self.continue_targets[-1] if self.continue_targets else None


def _normalize_bool(builder):
    builder.emit_push_imm64(0)
    builder.emit_simple("NE")


def _compile_address(node, ctx):
    node = _unwrap_expr(node)
    if node.kind == clang.cindex.CursorKind.DECL_REF_EXPR:
        symbol = ctx.lookup_symbol(node.spelling)
        if symbol is None:
            return False
        stack_class, reg_index, _ = symbol
        if stack_class != "int":
            return False
        ctx.builder.emit_push_reg(reg_index, "int")
        return True
    if node.kind == clang.cindex.CursorKind.ARRAY_SUBSCRIPT_EXPR:
        children = list(node.get_children())
        if len(children) != 2:
            return False
        base_expr, index_expr = children
        if not _compile_expression(base_expr, ctx, "int"):
            return False
        if not _compile_expression(index_expr, ctx, "int"):
            return False
        elem_type = node.type.get_canonical()
        elem_size = elem_type.get_size()
        if elem_size <= 0:
            return False
        if elem_size != 1:
            ctx.builder.emit_push_imm64(elem_size)
            ctx.builder.emit_simple("MUL")
        ctx.builder.emit_simple("ADD")
        return True
    if node.kind == clang.cindex.CursorKind.UNARY_OPERATOR and _extract_unary_operator(node, ctx.source_text) == "*":
        children = list(node.get_children())
        return len(children) == 1 and _compile_expression(children[0], ctx, "int")
    return False


def _emit_load_for_type(type_obj, ctx):
    canonical = type_obj.get_canonical()
    if _is_fp_type(canonical):
        size = canonical.get_size()
        if size == 4:
            ctx.builder.emit_simple("F_LOAD32")
            return "fp"
        if size == 8:
            ctx.builder.emit_simple("F_LOAD64")
            return "fp"
        return None
    if _is_pointer_type(canonical):
        ctx.builder.emit_simple("LOAD64")
        return "int"
    size = canonical.get_size()
    if size == 1:
        ctx.builder.emit_simple("LOAD8")
    elif size == 2:
        ctx.builder.emit_simple("LOAD16")
    elif size == 4:
        ctx.builder.emit_simple("LOAD32")
    elif size == 8:
        ctx.builder.emit_simple("LOAD64")
    else:
        return None
    return "int"


def _emit_store_for_type(type_obj, ctx):
    canonical = type_obj.get_canonical()
    if _is_fp_type(canonical):
        size = canonical.get_size()
        if size == 4:
            ctx.builder.emit_simple("F_STORE32")
            return True
        if size == 8:
            ctx.builder.emit_simple("F_STORE64")
            return True
        return False
    size = 8 if _is_pointer_type(canonical) else canonical.get_size()
    if size == 1:
        ctx.builder.emit_simple("STORE8")
    elif size == 2:
        ctx.builder.emit_simple("STORE16")
    elif size == 4:
        ctx.builder.emit_simple("STORE32")
    elif size == 8:
        ctx.builder.emit_simple("STORE64")
    else:
        return False
    return True


def _compile_cast_expression(node, ctx, target_class):
    children = list(node.get_children())
    if len(children) != 1:
        return False
    child = children[0]
    source_class = _type_class(child.type)
    actual_target = target_class or _type_class(node.type)
    if actual_target == source_class:
        return _compile_expression(child, ctx, actual_target)
    if actual_target == "fp":
        if not _compile_expression(child, ctx, "int"):
            return False
        ctx.builder.emit_simple("I2F")
        return True
    if not _compile_expression(child, ctx, "fp"):
        return False
    ctx.builder.emit_simple("F2I")
    return True


def _compile_call_expression(node, ctx, target_class):
    children = list(node.get_children())
    if not children:
        return False
    callee = _unwrap_expr(children[0])
    if callee.kind != clang.cindex.CursorKind.DECL_REF_EXPR or callee.referenced is None:
        return False
    callee_cursor = callee.referenced
    if callee_cursor.kind != clang.cindex.CursorKind.FUNCTION_DECL:
        return False
    if not _is_function_signature_supported(callee_cursor):
        return False

    params = _function_parameters(callee_cursor)
    args = children[1:]
    if len(params) != len(args):
        return False

    int_count = 0
    fp_count = 0
    for param, arg in zip(params, args):
        desired_class = _type_class(param.type)
        if not _compile_expression(arg, ctx, desired_class):
            return False
        if desired_class == "fp":
            fp_count += 1
        else:
            int_count += 1

    func_idx = ctx.register_native_function(callee_cursor)
    return_kind = RETURN_KIND_FP if _is_fp_type(callee_cursor.result_type) else RETURN_KIND_INT
    ctx.builder.emit_call_native_packed(func_idx, int_count, fp_count, return_kind)
    actual_class = "fp" if return_kind == RETURN_KIND_FP else "int"
    if target_class and target_class != actual_class:
        if target_class == "fp":
            ctx.builder.emit_simple("I2F")
            return True
        ctx.builder.emit_simple("F2I")
        return True
    return True


def _compile_comparison(op, lhs, rhs, ctx):
    if _is_fp_type(lhs.type) or _is_fp_type(rhs.type):
        if not _compile_expression(lhs, ctx, "fp"):
            return False
        if not _compile_expression(rhs, ctx, "fp"):
            return False
        opcode_map = {
            "==": "F_EQ",
            "!=": "F_NE",
            "<": "F_LT",
            "<=": "F_LE",
            ">": "F_GT",
            ">=": "F_GE",
        }
        ctx.builder.emit_simple(opcode_map[op])
        return True
    if not _compile_expression(lhs, ctx, "int"):
        return False
    if not _compile_expression(rhs, ctx, "int"):
        return False
    opcode_map = {
        "==": "EQ",
        "!=": "NE",
        "<": "LT_U" if _is_unsigned_type(lhs.type) or _is_unsigned_type(rhs.type) else "LT",
        "<=": "LE_U" if _is_unsigned_type(lhs.type) or _is_unsigned_type(rhs.type) else "LE",
        ">": "GT_U" if _is_unsigned_type(lhs.type) or _is_unsigned_type(rhs.type) else "GT",
        ">=": "GE_U" if _is_unsigned_type(lhs.type) or _is_unsigned_type(rhs.type) else "GE",
    }
    ctx.builder.emit_simple(opcode_map[op])
    return True


def _compile_expression(cursor, ctx, target_class=None):
    node = _unwrap_expr(cursor)
    kind = node.kind

    if kind == clang.cindex.CursorKind.INTEGER_LITERAL:
        literal = _extract_integer_literal(node)
        if literal is None:
            return False
        if target_class == "fp":
            ctx.builder.emit_f_push_imm64(literal["value"])
        else:
            ctx.builder.emit_push_imm64(literal["value"])
        return True

    if kind == clang.cindex.CursorKind.FLOATING_LITERAL:
        literal = _extract_floating_literal(node)
        if literal is None:
            return False
        if target_class == "int":
            ctx.builder.emit_f_push_imm64(literal)
            ctx.builder.emit_simple("F2I")
        else:
            ctx.builder.emit_f_push_imm64(literal)
        return True

    if kind == clang.cindex.CursorKind.CXX_BOOL_LITERAL_EXPR:
        value = _extract_bool_literal(node)
        if value is None:
            return False
        if target_class == "fp":
            ctx.builder.emit_f_push_imm64(value)
        else:
            ctx.builder.emit_push_imm64(value)
        return True

    if kind == clang.cindex.CursorKind.DECL_REF_EXPR:
        symbol = ctx.lookup_symbol(node.spelling)
        if symbol is None:
            return False
        stack_class, reg_index, _ = symbol
        ctx.builder.emit_push_reg(reg_index, stack_class)
        if target_class and target_class != stack_class:
            ctx.builder.emit_simple("I2F" if target_class == "fp" else "F2I")
        return True

    if kind in CAST_KINDS:
        return _compile_cast_expression(node, ctx, target_class)

    if kind == clang.cindex.CursorKind.ARRAY_SUBSCRIPT_EXPR:
        if not _compile_address(node, ctx):
            return False
        loaded_class = _emit_load_for_type(node.type, ctx)
        if loaded_class is None:
            return False
        if target_class and target_class != loaded_class:
            ctx.builder.emit_simple("I2F" if target_class == "fp" else "F2I")
        return True

    if kind == clang.cindex.CursorKind.UNARY_OPERATOR:
        children = list(node.get_children())
        if len(children) != 1:
            return False
        op = _extract_unary_operator(node, ctx.source_text)
        child = children[0]
        if op == "*":
            if not _compile_address(node, ctx):
                return False
            loaded_class = _emit_load_for_type(node.type, ctx)
            if loaded_class is None:
                return False
            if target_class and target_class != loaded_class:
                ctx.builder.emit_simple("I2F" if target_class == "fp" else "F2I")
            return True
        if op == "+":
            return _compile_expression(child, ctx, target_class or _type_class(node.type))
        if op == "-":
            value_class = target_class or _type_class(node.type)
            if value_class == "fp":
                ctx.builder.emit_f_push_imm64(0.0)
                if not _compile_expression(child, ctx, "fp"):
                    return False
                ctx.builder.emit_simple("F_SUB")
                return True
            ctx.builder.emit_push_imm64(0)
            if not _compile_expression(child, ctx, "int"):
                return False
            ctx.builder.emit_simple("SUB")
            return True
        if op == "!":
            if not _compile_expression(child, ctx, "int"):
                return False
            ctx.builder.emit_push_imm64(0)
            ctx.builder.emit_simple("EQ")
            if target_class == "fp":
                ctx.builder.emit_simple("I2F")
            return True
        if op == "~":
            if not _compile_expression(child, ctx, "int"):
                return False
            ctx.builder.emit_simple("NOT")
            if target_class == "fp":
                ctx.builder.emit_simple("I2F")
            return True
        return False

    if kind == clang.cindex.CursorKind.CALL_EXPR:
        return _compile_call_expression(node, ctx, target_class)

    if kind in {
        clang.cindex.CursorKind.BINARY_OPERATOR,
        clang.cindex.CursorKind.COMPOUND_ASSIGNMENT_OPERATOR,
    }:
        children = list(node.get_children())
        if len(children) != 2:
            return False
        op = _extract_binary_operator(node, ctx.source_text)
        lhs, rhs = children
        if op in {"==", "!=", "<", "<=", ">", ">="}:
            if not _compile_comparison(op, lhs, rhs, ctx):
                return False
            if target_class == "fp":
                ctx.builder.emit_simple("I2F")
            return True
        if op == "&&":
            if not _compile_expression(lhs, ctx, "int"):
                return False
            _normalize_bool(ctx.builder)
            if not _compile_expression(rhs, ctx, "int"):
                return False
            _normalize_bool(ctx.builder)
            ctx.builder.emit_simple("AND")
            if target_class == "fp":
                ctx.builder.emit_simple("I2F")
            return True
        if op == "||":
            if not _compile_expression(lhs, ctx, "int"):
                return False
            _normalize_bool(ctx.builder)
            if not _compile_expression(rhs, ctx, "int"):
                return False
            _normalize_bool(ctx.builder)
            ctx.builder.emit_simple("OR")
            _normalize_bool(ctx.builder)
            if target_class == "fp":
                ctx.builder.emit_simple("I2F")
            return True
        if _is_fp_type(node.type) or target_class == "fp":
            if not _compile_expression(lhs, ctx, "fp"):
                return False
            if not _compile_expression(rhs, ctx, "fp"):
                return False
            opcode_map = {
                "+": "F_ADD",
                "-": "F_SUB",
                "*": "F_MUL",
                "/": "F_DIV",
            }
            opcode = opcode_map.get(op)
            if opcode is None:
                return False
            ctx.builder.emit_simple(opcode)
            if target_class == "int":
                ctx.builder.emit_simple("F2I")
            return True
        if not _compile_expression(lhs, ctx, "int"):
            return False
        if not _compile_expression(rhs, ctx, "int"):
            return False
        opcode_map = {
            "+": "ADD",
            "-": "SUB",
            "*": "MUL",
            "/": "DIV",
            "%": "MOD",
            "<<": "SHL",
            ">>": "SAR",
            "&": "AND",
            "|": "OR",
            "^": "XOR",
        }
        opcode = opcode_map.get(op)
        if opcode is None:
            return False
        ctx.builder.emit_simple(opcode)
        if target_class == "fp":
            ctx.builder.emit_simple("I2F")
        return True

    return False


def _compile_store(lhs, rhs, ctx, op):
    lhs = _unwrap_expr(lhs)
    if lhs.kind == clang.cindex.CursorKind.DECL_REF_EXPR:
        symbol = ctx.lookup_symbol(lhs.spelling)
        if symbol is None:
            return False
        stack_class, reg_index, _ = symbol
        if op == "=":
            if not _compile_expression(rhs, ctx, stack_class):
                return False
        else:
            arithmetic_opcode = {
                "+=": "F_ADD" if stack_class == "fp" else "ADD",
                "-=": "F_SUB" if stack_class == "fp" else "SUB",
                "*=": "F_MUL" if stack_class == "fp" else "MUL",
                "/=": "F_DIV" if stack_class == "fp" else "DIV",
                "%=": "MOD",
                "&=": "AND",
                "|=": "OR",
                "^=": "XOR",
                "<<=": "SHL",
                ">>=": "SAR",
            }.get(op)
            if arithmetic_opcode is None:
                return False
            ctx.builder.emit_push_reg(reg_index, stack_class)
            if not _compile_expression(rhs, ctx, stack_class):
                return False
            ctx.builder.emit_simple(arithmetic_opcode)
        ctx.builder.emit_pop_reg(reg_index, stack_class)
        return True

    if lhs.kind in {clang.cindex.CursorKind.ARRAY_SUBSCRIPT_EXPR, clang.cindex.CursorKind.UNARY_OPERATOR}:
        if not _compile_address(lhs, ctx):
            return False
        target_type = lhs.type
        value_class = _type_class(target_type)
        if not _compile_expression(rhs, ctx, value_class):
            return False
        return _emit_store_for_type(target_type, ctx)
    return False


def _flatten_switch_entries(cursor):
    entries = []
    current = cursor
    while True:
        if current.kind == clang.cindex.CursorKind.CASE_STMT:
            children = list(current.get_children())
            if len(children) != 2:
                return None
            entries.append(("case", children[0]))
            current = children[1]
            continue
        if current.kind == clang.cindex.CursorKind.DEFAULT_STMT:
            children = list(current.get_children())
            if len(children) != 1:
                return None
            entries.append(("default", None))
            current = children[0]
            continue
        break
    return entries, current


def _compile_switch_statement(cursor, ctx):
    children = list(cursor.get_children())
    if len(children) != 2:
        return False
    switch_expr, body = children
    if body.kind != clang.cindex.CursorKind.COMPOUND_STMT:
        return False

    switch_reg_name = generate_barcode_name(18)
    switch_end = ctx.builder.new_label()
    default_label = None
    case_blocks = []
    current_block = None

    if not _compile_expression(switch_expr, ctx, "int"):
        return False
    temp_cursor = type("TempCursor", (), {"spelling": switch_reg_name, "type": switch_expr.type})()
    _, switch_reg = ctx.reserve_symbol(temp_cursor)
    ctx.builder.emit_pop_reg(switch_reg, "int")

    for stmt in body.get_children():
        if stmt.kind in {clang.cindex.CursorKind.CASE_STMT, clang.cindex.CursorKind.DEFAULT_STMT}:
            flattened = _flatten_switch_entries(stmt)
            if flattened is None:
                return False
            entries, final_stmt = flattened
            label = ctx.builder.new_label()
            for entry_kind, expr in entries:
                if entry_kind == "default":
                    default_label = label
                else:
                    case_blocks.append((expr, label))
            current_block = {"label": label, "stmts": []}
            if final_stmt.kind != clang.cindex.CursorKind.COMPOUND_STMT:
                current_block["stmts"].append(final_stmt)
            else:
                current_block["stmts"].extend(list(final_stmt.get_children()))
            case_blocks.append(current_block)
            continue
        if current_block is None:
            return False
        current_block["stmts"].append(stmt)

    seen = set()
    body_blocks = []
    for block in case_blocks:
        if isinstance(block, tuple):
            continue
        if block["label"] in seen:
            continue
        seen.add(block["label"])
        body_blocks.append(block)

    for case_expr, label in [item for item in case_blocks if isinstance(item, tuple)]:
        ctx.builder.emit_push_reg(switch_reg, "int")
        if not _compile_expression(case_expr, ctx, "int"):
            return False
        ctx.builder.emit_simple("EQ")
        ctx.builder.emit_jump("JZ", ctx.builder.new_label())
        skip_label = ctx.builder.fixups[-1][1]
        ctx.builder.emit_jump("JMP", label)
        ctx.builder.mark_label(skip_label)

    ctx.builder.emit_jump("JMP", default_label or switch_end)

    ctx.push_break_target(switch_end)
    for block in body_blocks:
        ctx.builder.mark_label(block["label"])
        for stmt in block["stmts"]:
            if not _compile_statement(stmt, ctx):
                ctx.pop_break_target()
                return False
    ctx.pop_break_target()
    ctx.builder.mark_label(switch_end)
    return True


def _compile_statement(cursor, ctx):
    kind = cursor.kind

    if kind == clang.cindex.CursorKind.COMPOUND_STMT:
        for child in cursor.get_children():
            if not _compile_statement(child, ctx):
                return False
        return True

    if kind == clang.cindex.CursorKind.DECL_STMT:
        for decl in cursor.get_children():
            if decl.kind != clang.cindex.CursorKind.VAR_DECL or not _type_is_vm_supported(decl.type):
                return False
            stack_class, reg_index = ctx.reserve_symbol(decl)
            init_children = list(decl.get_children())
            if init_children:
                if len(init_children) != 1 or not _compile_expression(init_children[0], ctx, stack_class):
                    return False
            else:
                if stack_class == "fp":
                    ctx.builder.emit_f_push_imm64(0.0)
                else:
                    ctx.builder.emit_push_imm64(0)
            ctx.builder.emit_pop_reg(reg_index, stack_class)
        return True

    if kind == clang.cindex.CursorKind.RETURN_STMT:
        children = list(cursor.get_children())
        if len(children) != 1:
            return False
        if not _compile_expression(children[0], ctx, _type_class(ctx.function_cursor.result_type)):
            return False
        ctx.builder.emit_simple("HALT")
        return True

    if kind == clang.cindex.CursorKind.IF_STMT:
        children = list(cursor.get_children())
        if len(children) not in {2, 3}:
            return False
        else_label = ctx.builder.new_label()
        end_label = ctx.builder.new_label()
        if not _compile_expression(children[0], ctx, "int"):
            return False
        _normalize_bool(ctx.builder)
        ctx.builder.emit_jump("JZ", else_label)
        if not _compile_statement(children[1], ctx):
            return False
        if len(children) == 3:
            ctx.builder.emit_jump("JMP", end_label)
        ctx.builder.mark_label(else_label)
        if len(children) == 3 and not _compile_statement(children[2], ctx):
            return False
        if len(children) == 3:
            ctx.builder.mark_label(end_label)
        return True

    if kind == clang.cindex.CursorKind.WHILE_STMT:
        children = list(cursor.get_children())
        if len(children) != 2:
            return False
        loop_label = ctx.builder.new_label()
        end_label = ctx.builder.new_label()
        continue_label = ctx.builder.new_label()
        ctx.push_break_target(end_label)
        ctx.push_continue_target(continue_label)
        ctx.builder.mark_label(loop_label)
        if not _compile_expression(children[0], ctx, "int"):
            ctx.pop_continue_target()
            ctx.pop_break_target()
            return False
        _normalize_bool(ctx.builder)
        ctx.builder.emit_jump("JZ", end_label)
        if not _compile_statement(children[1], ctx):
            ctx.pop_continue_target()
            ctx.pop_break_target()
            return False
        ctx.builder.mark_label(continue_label)
        ctx.builder.emit_jump("JMP", loop_label)
        ctx.builder.mark_label(end_label)
        ctx.pop_continue_target()
        ctx.pop_break_target()
        return True

    if kind == clang.cindex.CursorKind.DO_STMT:
        children = list(cursor.get_children())
        if len(children) != 2:
            return False
        body_stmt, cond_expr = children
        loop_label = ctx.builder.new_label()
        continue_label = ctx.builder.new_label()
        end_label = ctx.builder.new_label()
        ctx.push_break_target(end_label)
        ctx.push_continue_target(continue_label)
        ctx.builder.mark_label(loop_label)
        if not _compile_statement(body_stmt, ctx):
            ctx.pop_continue_target()
            ctx.pop_break_target()
            return False
        ctx.builder.mark_label(continue_label)
        if not _compile_expression(cond_expr, ctx, "int"):
            ctx.pop_continue_target()
            ctx.pop_break_target()
            return False
        _normalize_bool(ctx.builder)
        ctx.builder.emit_jump("JZ", end_label)
        ctx.builder.emit_jump("JMP", loop_label)
        ctx.builder.mark_label(end_label)
        ctx.pop_continue_target()
        ctx.pop_break_target()
        return True

    if kind == clang.cindex.CursorKind.FOR_STMT:
        children = list(cursor.get_children())
        if len(children) != 4:
            return False
        if not _compile_statement(children[0], ctx):
            return False
        loop_label = ctx.builder.new_label()
        end_label = ctx.builder.new_label()
        continue_label = ctx.builder.new_label()
        ctx.push_break_target(end_label)
        ctx.push_continue_target(continue_label)
        ctx.builder.mark_label(loop_label)
        if not _compile_expression(children[1], ctx, "int"):
            ctx.pop_continue_target()
            ctx.pop_break_target()
            return False
        _normalize_bool(ctx.builder)
        ctx.builder.emit_jump("JZ", end_label)
        if not _compile_statement(children[3], ctx):
            ctx.pop_continue_target()
            ctx.pop_break_target()
            return False
        ctx.builder.mark_label(continue_label)
        if not _compile_statement(children[2], ctx):
            ctx.pop_continue_target()
            ctx.pop_break_target()
            return False
        ctx.builder.emit_jump("JMP", loop_label)
        ctx.builder.mark_label(end_label)
        ctx.pop_continue_target()
        ctx.pop_break_target()
        return True

    if kind == clang.cindex.CursorKind.SWITCH_STMT:
        return _compile_switch_statement(cursor, ctx)

    if kind == clang.cindex.CursorKind.UNARY_OPERATOR:
        op = _extract_unary_operator(cursor, ctx.source_text)
        if op in {"++", "--"}:
            child = _unwrap_expr(next(iter(cursor.get_children()), None))
            if child is None or child.kind != clang.cindex.CursorKind.DECL_REF_EXPR:
                return False
            symbol = ctx.lookup_symbol(child.spelling)
            if symbol is None or symbol[0] != "int":
                return False
            _, reg_index, _ = symbol
            ctx.builder.emit_push_reg(reg_index, "int")
            ctx.builder.emit_push_imm64(1)
            ctx.builder.emit_simple("ADD" if op == "++" else "SUB")
            ctx.builder.emit_pop_reg(reg_index, "int")
            return True
        return False

    if kind in {clang.cindex.CursorKind.BINARY_OPERATOR, clang.cindex.CursorKind.COMPOUND_ASSIGNMENT_OPERATOR}:
        op = _extract_binary_operator(cursor, ctx.source_text)
        if op in {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="}:
            children = list(cursor.get_children())
            return len(children) == 2 and _compile_store(children[0], children[1], ctx, op)
        return False

    if kind == clang.cindex.CursorKind.CALL_EXPR:
        return _compile_call_expression(cursor, ctx, None)

    if kind == clang.cindex.CursorKind.BREAK_STMT:
        target = ctx.current_break_target()
        if target is None:
            return False
        ctx.builder.emit_jump("JMP", target)
        return True

    if kind == clang.cindex.CursorKind.CONTINUE_STMT:
        target = ctx.current_continue_target()
        if target is None:
            return False
        ctx.builder.emit_jump("JMP", target)
        return True

    if kind == clang.cindex.CursorKind.NULL_STMT:
        return True

    return False


def _rewrite_function_name(signature_text, original_name, replacement_name):
    return re.sub(rf"\b{re.escape(original_name)}\b(?=\s*\()", replacement_name, signature_text, count=1)


def _build_native_bridge(native_info, name_map):
    cursor = native_info["cursor"]
    bridge_name = native_info["bridge_name"]
    int_index_name = generate_barcode_name(18)
    fp_index_name = generate_barcode_name(18)
    mapped_target = name_map.get(cursor.spelling, cursor.spelling)

    arg_exprs = []
    for param in _function_parameters(cursor):
        arg_exprs.append(
            _render_arg_unpack(param.type, int_index_name, fp_index_name).format(ints="int_args", fps="fp_args")
        )

    call_expr = f"{mapped_target}({', '.join(arg_exprs)})"
    result_type = cursor.result_type
    lines = [
        VM_SKIP_MARKER,
        f"static uint64_t {bridge_name}(const uint64_t* int_args, uint64_t int_count, const double* fp_args, uint64_t fp_count) {{",
        f"    (void)int_count;",
        f"    (void)fp_count;",
        f"    uint64_t {int_index_name} = 0;",
        f"    uint64_t {fp_index_name} = 0;",
    ]
    if _is_fp_type(result_type):
        result_name = generate_barcode_name(18)
        bits_name = generate_barcode_name(18)
        lines.extend(
            [
                f"    auto {result_name} = {call_expr};",
                f"    uint64_t {bits_name} = 0;",
                f"    std::memcpy(&{bits_name}, &{result_name}, sizeof({bits_name}));",
                f"    return {bits_name};",
            ]
        )
    elif _is_pointer_type(result_type):
        lines.append(f"    return reinterpret_cast<uint64_t>({call_expr});")
    else:
        lines.append(f"    return static_cast<uint64_t>({call_expr});")
    lines.append("}")
    return "\n".join(lines)


def _build_wrapper(function_cursor, source_text, name_map):
    if function_cursor.spelling == "main" or not _is_function_signature_supported(function_cursor):
        return None
    body_cursor = _get_function_body_cursor(function_cursor)
    if body_cursor is None:
        return None

    ctx = VmCompileContext(function_cursor, source_text, name_map)
    int_param_index = 4
    fp_param_index = 3
    for param in _function_parameters(function_cursor):
        if _type_class(param.type) == "fp":
            ctx.reserve_symbol(param, fp_param_index)
            fp_param_index += 1
        else:
            ctx.reserve_symbol(param, int_param_index)
            int_param_index += 1

    if not _compile_statement(body_cursor, ctx):
        return None

    byte_values = ctx.builder.finalize()
    if not byte_values or byte_values[-1] != BytecodeBuilder.OPCODES["HALT"]:
        return None

    line_start = source_text.rfind("\n", 0, function_cursor.extent.start.offset) + 1
    brace_index = body_cursor.extent.start.offset
    signature_text = source_text[line_start:brace_index].rstrip()
    if not signature_text or "\n#" in signature_text or "template" in signature_text:
        return None
    if re.search(r"\)\s*(?:const|noexcept|->|\[\[|requires)", signature_text):
        return None

    mapped_name = name_map.get(function_cursor.spelling, function_cursor.spelling)
    signature_text = _rewrite_function_name(signature_text, function_cursor.spelling, mapped_name)
    base_indent = re.match(r"\s*", source_text[line_start:brace_index]).group(0)

    code_name = generate_barcode_name(18)
    int_args_name = generate_barcode_name(18)
    fp_args_name = generate_barcode_name(18)
    native_table_name = generate_barcode_name(18)
    result_name = generate_barcode_name(18)

    int_arg_exprs = []
    fp_arg_exprs = []
    for param in _function_parameters(function_cursor):
        if _type_class(param.type) == "fp":
            fp_arg_exprs.append(f"static_cast<double>({param.spelling})")
        elif _is_pointer_type(param.type):
            int_arg_exprs.append(f"reinterpret_cast<unsigned long long>({param.spelling})")
        else:
            int_arg_exprs.append(f"static_cast<unsigned long long>({param.spelling})")

    helper_lines = [f"{base_indent}static const unsigned char {code_name}[] = {{{', '.join(str(v) for v in byte_values)}}};"]
    if ctx.native_functions:
        for native_info in ctx.native_functions:
            helper_lines.append(base_indent + _build_native_bridge(native_info, name_map).replace("\n", "\n" + base_indent))
        helper_lines.append(
            f"{base_indent}static void* {native_table_name}[] = {{{', '.join(f'reinterpret_cast<void*>(&{info['bridge_name']})' for info in ctx.native_functions)}}};"
        )

    wrapper_lines = [f"{base_indent}{VM_SKIP_MARKER}", signature_text, f"{base_indent}{{"]
    if int_arg_exprs:
        wrapper_lines.append(
            f"{base_indent}    uint64_t {int_args_name}[{len(int_arg_exprs)}] = {{{', '.join(int_arg_exprs)}}};"
        )
    else:
        wrapper_lines.append(f"{base_indent}    uint64_t* {int_args_name} = nullptr;")
    if fp_arg_exprs:
        wrapper_lines.append(
            f"{base_indent}    double {fp_args_name}[{len(fp_arg_exprs)}] = {{{', '.join(fp_arg_exprs)}}};"
        )
    else:
        wrapper_lines.append(f"{base_indent}    double* {fp_args_name} = nullptr;")
    wrapper_lines.append(
        f"{base_indent}    uint64_t {result_name} = {state.VM_NAMESPACE_NAME}::execute("
        f"{code_name}, sizeof({code_name}), {random.randint(1, 0xFFFFFFFF)}ULL, "
        f"{int_args_name}, {len(int_arg_exprs)}, {fp_args_name}, {len(fp_arg_exprs)}, "
        f"{native_table_name if ctx.native_functions else 'nullptr'}, {len(ctx.native_functions)});"
    )

    if _is_fp_type(function_cursor.result_type):
        fp_result_name = generate_barcode_name(18)
        wrapper_lines.extend(
            [
                f"{base_indent}    double {fp_result_name} = 0.0;",
                f"{base_indent}    std::memcpy(&{fp_result_name}, &{result_name}, sizeof({fp_result_name}));",
                f"{base_indent}    return static_cast<{function_cursor.result_type.spelling}>({fp_result_name});",
            ]
        )
    elif _is_pointer_type(function_cursor.result_type):
        wrapper_lines.append(
            f"{base_indent}    return reinterpret_cast<{function_cursor.result_type.spelling}>({result_name});"
        )
    else:
        wrapper_lines.append(
            f"{base_indent}    return static_cast<{function_cursor.result_type.spelling}>({result_name});"
        )
    wrapper_lines.append(f"{base_indent}}}")

    replacement = "\n".join(helper_lines + [""] + wrapper_lines)
    return line_start, body_cursor.extent.end.offset, replacement


def _walk_functions(cursor, target_realpath):
    if cursor.kind == clang.cindex.CursorKind.FUNCTION_DECL and is_local(cursor, target_realpath):
        yield cursor
    for child in cursor.get_children():
        yield from _walk_functions(child, target_realpath)


def collect_virtual_machine_replacements(tu_cursor, target_realpath, name_map, source_text, replacements):
    if not config.ENABLE_VIRTUAL_MACHINE_OBFUSCATION:
        vlog("vm", "disabled")
        return

    state.init_virtual_machine_names()
    converted = 0
    converted_names = []
    for function_cursor in _walk_functions(tu_cursor, target_realpath):
        wrapper = _build_wrapper(function_cursor, source_text, name_map)
        if wrapper is None:
            continue
        replacements.append(wrapper)
        converted += 1
        converted_names.append(function_cursor.spelling)

    state.VM_RUNTIME_REQUIRED = converted > 0
    if len(converted_names) <= 10:
        vlog("vm", f"converted_functions={converted}, names={converted_names}")
    else:
        shown = converted_names[:10]
        truncated = len(converted_names) - 10
        vlog("vm", f"converted_functions={converted}, names={shown}, truncated={truncated}")


def inject_virtual_machine_runtime(source_text):
    if not config.ENABLE_VIRTUAL_MACHINE_OBFUSCATION or not state.VM_RUNTIME_REQUIRED:
        return source_text

    state.init_virtual_machine_names()
    runtime_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cpputils", "vm.cpp")
    with open(runtime_path, "r", encoding="utf-8") as handle:
        runtime_source = handle.read()

    marker = "#include <iostream>"
    marker_index = runtime_source.find(marker)
    runtime_block = runtime_source if marker_index == -1 else runtime_source[:marker_index].rstrip()
    runtime_block = runtime_block.replace("namespace vm {", f"namespace {state.VM_NAMESPACE_NAME} {{", 1)
    runtime_block = _obfuscate_runtime_identifiers(runtime_block)
    runtime_block = strip_comments(runtime_block)
    injected = runtime_block + "\n\n" + source_text
    vlog("vm", f"injected runtime namespace={state.VM_NAMESPACE_NAME}, bytes {len(source_text)} -> {len(injected)}")
    return injected


def _obfuscate_runtime_identifiers(runtime_block):
    if not config.ENABLE_IDENTIFIER_OBFUSCATION:
        return runtime_block

    rename_map = {
        candidate: generate_barcode_name(18)
        for candidate in _collect_runtime_identifiers(runtime_block)
    }

    if not rename_map:
        return runtime_block

    return _replace_runtime_identifiers(runtime_block, rename_map)


def _collect_runtime_identifiers(source_text):
    candidates = set()
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
            if char == "\n":
                in_line_comment = False
                line_start = True
            index += 1
            continue

        if in_block_comment:
            if char == "*" and next_char == "/":
                in_block_comment = False
                index += 2
            else:
                index += 1
            continue

        if in_preprocessor:
            if char == "\n":
                escaped = index > 0 and source_text[index - 1] == "\\"
                if not escaped:
                    in_preprocessor = False
                    line_start = True
            index += 1
            continue

        if in_string:
            if char == '"' and source_text[index - 1] != "\\":
                in_string = False
            if char == "\n":
                line_start = True
            index += 1
            continue

        if in_char:
            if char == "'" and source_text[index - 1] != "\\":
                in_char = False
            if char == "\n":
                line_start = True
            index += 1
            continue

        if line_start and char in " \t":
            index += 1
            continue
        if line_start and char == "#":
            in_preprocessor = True
            index += 1
            continue

        line_start = char == "\n"

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
            index += 1
            continue

        if char == "'":
            in_char = True
            index += 1
            continue

        prev_char = source_text[index - 1] if index > 0 else ""
        if (char.isalpha() or char == "_") and not (prev_char.isalnum() or prev_char == "_"):
            start = index
            index += 1
            while index < len(source_text) and (source_text[index].isalnum() or source_text[index] == "_"):
                index += 1
            token = source_text[start:index]
            if (
                not _has_std_scope_prefix(source_text, start)
                and token not in RUNTIME_IDENTIFIER_EXCLUDES
                and token != state.VM_NAMESPACE_NAME
            ):
                candidates.add(token)
            continue

        index += 1

    return sorted(candidates)


def _replace_runtime_identifiers(source_text, rename_map):
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
            result.append(char)
            if char == "\n":
                in_line_comment = False
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
            if char == '"' and source_text[index - 1] != "\\":
                in_string = False
            index += 1
            continue

        if in_char:
            result.append(char)
            if char == "'" and source_text[index - 1] != "\\":
                in_char = False
            index += 1
            continue

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
            continue

        if char == "'":
            result.append(char)
            in_char = True
            index += 1
            continue

        prev_char = source_text[index - 1] if index > 0 else ""
        if (char.isalpha() or char == "_") and not (prev_char.isalnum() or prev_char == "_"):
            start = index
            index += 1
            while index < len(source_text) and (source_text[index].isalnum() or source_text[index] == "_"):
                index += 1
            token = source_text[start:index]
            if _has_std_scope_prefix(source_text, start):
                result.append(token)
            else:
                result.append(rename_map.get(token, token))
            continue

        result.append(char)
        index += 1

    return "".join(result)


def _has_std_scope_prefix(source_text, token_start):
    cursor = token_start - 1
    while cursor >= 0 and source_text[cursor].isspace():
        cursor -= 1
    if cursor < 1 or source_text[cursor] != ":" or source_text[cursor - 1] != ":":
        return False
    cursor -= 2
    while cursor >= 0 and source_text[cursor].isspace():
        cursor -= 1
    ident_end = cursor + 1
    while cursor >= 0 and (source_text[cursor].isalnum() or source_text[cursor] == "_"):
        cursor -= 1
    ident = source_text[cursor + 1:ident_end]
    return ident == "std"
