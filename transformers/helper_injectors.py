import random

import config
import state
from util import generate_barcode_name, vlog


def _find_insertion_line_index(lines):
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
    return insert_line_index


def inject_runtime_obfuscation_helpers(source_text):
    """Injects helpers that keep constant wrappers visible after optimization."""
    if not config.ENABLE_RUNTIME_CONSTANT_OBFUSCATION:
        vlog("runtime", "disabled")
        return source_text

    if state.RUNTIME_OBF_STATE_NAME is None or state.RUNTIME_OBF_HELPER_NAME is None:
        vlog("runtime", "no runtime helper names were initialized; skip injection")
        return source_text

    value_name = generate_barcode_name(18)
    mask_name = generate_barcode_name(18)
    shadow_name = generate_barcode_name(18)
    mix_name = generate_barcode_name(18)
    helper_variants = [
        "\n".join(
            [
                "namespace {",
                f"volatile unsigned long long {state.RUNTIME_OBF_STATE_NAME} = 0;",
                f"template <typename T> __attribute__((noinline, noipa)) T {state.RUNTIME_OBF_HELPER_NAME}(T {value_name}) {{",
                f"    unsigned long long {mask_name} = {state.RUNTIME_OBF_STATE_NAME};",
                f'    __asm__ __volatile__("" : "+r"({value_name}) : "r"({mask_name}) : "memory");',
                f"    return {value_name};",
                "}",
                "}",
                "",
            ]
        ),
        "\n".join(
            [
                "namespace {",
                f"volatile unsigned long long {state.RUNTIME_OBF_STATE_NAME} = 0;",
                f"template <typename T> __attribute__((noinline, noipa)) T {state.RUNTIME_OBF_HELPER_NAME}(T {value_name}) {{",
                f"    unsigned long long {mask_name} = {state.RUNTIME_OBF_STATE_NAME};",
                f"    T {shadow_name} = {value_name};",
                f'    __asm__ __volatile__("" : "+r"({shadow_name}) : "r"({mask_name}) : "memory");',
                f"    return static_cast<T>({shadow_name});",
                "}",
                "}",
                "",
            ]
        ),
        "\n".join(
            [
                "namespace {",
                f"volatile unsigned long long {state.RUNTIME_OBF_STATE_NAME} = 0;",
                f"template <typename T> __attribute__((noinline, noipa)) T {state.RUNTIME_OBF_HELPER_NAME}(T {value_name}) {{",
                f"    unsigned long long {mask_name} = {state.RUNTIME_OBF_STATE_NAME};",
                f"    unsigned long long {mix_name} = ({mask_name} << 1) ^ ({mask_name} >> 1);",
                f"    T {shadow_name} = static_cast<T>({value_name});",
                f'    __asm__ __volatile__("" : "+r"({shadow_name}) : "r"({mix_name}) : "memory");',
                f"    return static_cast<T>(({shadow_name} ^ static_cast<T>(0)) + static_cast<T>(0));",
                "}",
                "}",
                "",
            ]
        ),
    ]
    helper_block = random.choice(helper_variants)
    variant_index = 0 if helper_block == helper_variants[0] else 1

    lines = source_text.splitlines(keepends=True)
    insert_line_index = _find_insertion_line_index(lines)
    if insert_line_index == 0:
        out = helper_block + source_text
        vlog("runtime", f"injected variant={variant_index} at BOF, bytes {len(source_text)} -> {len(out)}")
        return out
    out = "".join(lines[:insert_line_index]) + helper_block + "".join(lines[insert_line_index:])
    vlog("runtime", f"injected variant={variant_index} after prologue, bytes {len(source_text)} -> {len(out)}")
    return out


def inject_cfg_pollution_helpers(source_text):
    """Injects opaque selectors used by polluted CFG edges and clone dispatchers."""
    if not (config.ENABLE_CFG_POLLUTION or config.ENABLE_FUNCTION_CLONING):
        vlog("cfg", "disabled (cfg_pollution and function_cloning are both off)")
        return source_text

    state.init_cfg_pollution_names()
    shadow_name = generate_barcode_name(18)
    real_name = generate_barcode_name(18)
    fake_name = generate_barcode_name(18)
    salt_name = generate_barcode_name(18)
    upper_name = generate_barcode_name(18)
    state_name = generate_barcode_name(18)
    mix_name = generate_barcode_name(18)
    helper_variants = [
        "\n".join(
            [
                "namespace {",
                f"volatile unsigned long long {state_name} = 0;",
                f"__attribute__((noinline, noipa)) int {state.CFG_EDGE_HELPER_NAME}(int {real_name}, int {fake_name}, unsigned long long {salt_name}) {{",
                f"    unsigned long long {shadow_name} = {state_name} ^ {salt_name};",
                f'    __asm__ __volatile__("" : "+r"({shadow_name}) : : "memory");',
                f"    return ((({shadow_name} * 0ULL) + 1ULL) == 1ULL) ? {real_name} : {fake_name};",
                "}",
                f"__attribute__((noinline, noipa)) int {state.CFG_CLONE_SELECT_HELPER_NAME}(int {upper_name}, unsigned long long {salt_name}) {{",
                f"    unsigned long long {shadow_name} = {state_name} + {salt_name};",
                f'    __asm__ __volatile__("" : "+r"({shadow_name}) : : "memory");',
                f"    return static_cast<int>(({shadow_name} * 0ULL) % static_cast<unsigned long long>({upper_name} > 0 ? {upper_name} : 1));",
                "}",
                "}",
                "",
            ]
        ),
        "\n".join(
            [
                "namespace {",
                f"volatile unsigned long long {state_name} = 0;",
                f"__attribute__((noinline, noipa)) int {state.CFG_EDGE_HELPER_NAME}(int {real_name}, int {fake_name}, unsigned long long {salt_name}) {{",
                f"    unsigned long long {shadow_name} = ({state_name} + {salt_name}) ^ ({salt_name} >> 1);",
                f'    __asm__ __volatile__("" : "+r"({shadow_name}) : : "memory");',
                f"    int {mix_name} = static_cast<int>((({shadow_name} & 1ULL) ^ ({shadow_name} & 1ULL)));",
                f"    return ({mix_name} == 0) ? {real_name} : {fake_name};",
                "}",
                f"__attribute__((noinline, noipa)) int {state.CFG_CLONE_SELECT_HELPER_NAME}(int {upper_name}, unsigned long long {salt_name}) {{",
                f"    unsigned long long {shadow_name} = ({state_name} ^ {salt_name}) + ({salt_name} & 7ULL);",
                f'    __asm__ __volatile__("" : "+r"({shadow_name}) : : "memory");',
                f"    unsigned long long {mix_name} = ({shadow_name} ^ {shadow_name}) + {shadow_name};",
                f"    return static_cast<int>(({mix_name} * 0ULL) % static_cast<unsigned long long>({upper_name} > 0 ? {upper_name} : 1));",
                "}",
                "}",
                "",
            ]
        ),
        "\n".join(
            [
                "namespace {",
                f"volatile unsigned long long {state_name} = 0;",
                f"__attribute__((noinline, noipa)) int {state.CFG_EDGE_HELPER_NAME}(int {real_name}, int {fake_name}, unsigned long long {salt_name}) {{",
                f"    unsigned long long {shadow_name} = {state_name} + ({salt_name} ^ 0x5A5AULL);",
                f'    __asm__ __volatile__("" : "+r"({shadow_name}) : : "memory");',
                f"    return static_cast<int>(((({shadow_name} ^ {shadow_name}) + 1ULL) != 0ULL) ? {real_name} : {fake_name});",
                "}",
                f"__attribute__((noinline, noipa)) int {state.CFG_CLONE_SELECT_HELPER_NAME}(int {upper_name}, unsigned long long {salt_name}) {{",
                f"    unsigned long long {shadow_name} = ({state_name} | 1ULL) ^ {salt_name};",
                f'    __asm__ __volatile__("" : "+r"({shadow_name}) : : "memory");',
                f"    return static_cast<int>(({shadow_name} & 0ULL) % static_cast<unsigned long long>({upper_name} > 0 ? {upper_name} : 1));",
                "}",
                "}",
                "",
            ]
        ),
    ]
    helper_block = random.choice(helper_variants)

    lines = source_text.splitlines(keepends=True)
    insert_line_index = _find_insertion_line_index(lines)
    out = "".join(lines[:insert_line_index]) + helper_block + "".join(lines[insert_line_index:])
    vlog(
        "cfg",
        f"injected helpers edge={state.CFG_EDGE_HELPER_NAME} clone_sel={state.CFG_CLONE_SELECT_HELPER_NAME}, bytes {len(source_text)} -> {len(out)}",
    )
    return out


def inject_memory_access_helpers(source_text):
    """Injects index/pointer wrappers for array and member access rewrites."""
    if not config.ENABLE_MEMORY_ACCESS_OBFUSCATION:
        vlog("mem", "disabled")
        return source_text
    if (
        state.MEMORY_INDEX_HELPER_NAME is None
        or state.MEMORY_PTR_ADVANCE_HELPER_NAME is None
        or state.MEMORY_MEMBER_HELPER_NAME is None
    ):
        vlog("mem", "no AST rewrites created memory helpers; skip injection")
        return source_text

    index_name = generate_barcode_name(18)
    shadow_name = generate_barcode_name(18)
    ptr_name = generate_barcode_name(18)
    mix_name = generate_barcode_name(18)
    helper_variants = [
        "\n".join(
            [
                "#include <type_traits>",
                "namespace {",
                f"template <typename T, typename = std::enable_if_t<std::is_integral_v<T>>> __attribute__((noinline, noipa)) T {state.MEMORY_INDEX_HELPER_NAME}(T {index_name}) {{",
                f"    T {shadow_name} = {index_name};",
                f'    __asm__ __volatile__("" : "+r"({shadow_name}) : : "memory");',
                f"    return static_cast<T>(({shadow_name} ^ static_cast<T>(0)) + (static_cast<T>(0) & {shadow_name}));",
                "}",
                f"template <typename T, typename I> __attribute__((noinline, noipa)) auto {state.MEMORY_PTR_ADVANCE_HELPER_NAME}(T* {ptr_name}, I {index_name}) -> T* {{",
                f"    auto {shadow_name} = {state.MEMORY_INDEX_HELPER_NAME}(static_cast<I>({index_name}));",
                f'    __asm__ __volatile__("" : "+r"({ptr_name}) : "g"({shadow_name}) : "memory");',
                f"    return {ptr_name} + ({shadow_name} - static_cast<I>(0) + static_cast<I>(0));",
                "}",
                f"template <typename T> __attribute__((noinline, noipa)) T* {state.MEMORY_MEMBER_HELPER_NAME}(T* {ptr_name}) {{",
                f'    __asm__ __volatile__("" : "+r"({ptr_name}) : : "memory");',
                f"    return {ptr_name};",
                "}",
                "}",
                "",
            ]
        ),
        "\n".join(
            [
                "#include <type_traits>",
                "namespace {",
                f"template <typename T, typename = std::enable_if_t<std::is_integral_v<T>>> __attribute__((noinline, noipa)) T {state.MEMORY_INDEX_HELPER_NAME}(T {index_name}) {{",
                f"    T {shadow_name} = static_cast<T>({index_name} + static_cast<T>(0));",
                f'    __asm__ __volatile__("" : "+r"({shadow_name}) : : "memory");',
                f"    T {mix_name} = static_cast<T>(({shadow_name} | static_cast<T>(0)) - static_cast<T>(0));",
                f"    return {mix_name};",
                "}",
                f"template <typename T, typename I> __attribute__((noinline, noipa)) auto {state.MEMORY_PTR_ADVANCE_HELPER_NAME}(T* {ptr_name}, I {index_name}) -> T* {{",
                f"    I {shadow_name} = {state.MEMORY_INDEX_HELPER_NAME}(static_cast<I>({index_name}));",
                f"    auto {mix_name} = ({shadow_name} ^ static_cast<I>(0));",
                f'    __asm__ __volatile__("" : "+r"({ptr_name}) : "g"({mix_name}) : "memory");',
                f"    return &{ptr_name}[{mix_name}];",
                "}",
                f"template <typename T> __attribute__((noinline, noipa)) T* {state.MEMORY_MEMBER_HELPER_NAME}(T* {ptr_name}) {{",
                f"    auto {shadow_name} = reinterpret_cast<unsigned long long>({ptr_name});",
                f'    __asm__ __volatile__("" : "+r"({shadow_name}) : : "memory");',
                f"    return reinterpret_cast<T*>({shadow_name});",
                "}",
                "}",
                "",
            ]
        ),
        "\n".join(
            [
                "#include <type_traits>",
                "namespace {",
                f"template <typename T, typename = std::enable_if_t<std::is_integral_v<T>>> __attribute__((noinline, noipa)) T {state.MEMORY_INDEX_HELPER_NAME}(T {index_name}) {{",
                f"    T {shadow_name} = {index_name};",
                f"    T {mix_name} = static_cast<T>(({shadow_name} & ~static_cast<T>(0)) | ({shadow_name} & static_cast<T>(0)));",
                f'    __asm__ __volatile__("" : "+r"({mix_name}) : : "memory");',
                f"    return static_cast<T>({mix_name} + static_cast<T>(0));",
                "}",
                f"template <typename T, typename I> __attribute__((noinline, noipa)) auto {state.MEMORY_PTR_ADVANCE_HELPER_NAME}(T* {ptr_name}, I {index_name}) -> T* {{",
                f"    I {shadow_name} = {state.MEMORY_INDEX_HELPER_NAME}(static_cast<I>({index_name}));",
                f'    __asm__ __volatile__("" : "+r"({shadow_name}) : "r"({ptr_name}) : "memory");',
                f"    return static_cast<T*>({ptr_name} + {shadow_name});",
                "}",
                f"template <typename T> __attribute__((noinline, noipa)) T* {state.MEMORY_MEMBER_HELPER_NAME}(T* {ptr_name}) {{",
                f'    __asm__ __volatile__("" : : "g"({ptr_name}) : "memory");',
                f"    return static_cast<T*>({ptr_name});",
                "}",
                "}",
                "",
            ]
        ),
    ]
    helper_block = random.choice(helper_variants)

    lines = source_text.splitlines(keepends=True)
    insert_line_index = _find_insertion_line_index(lines)
    out = "".join(lines[:insert_line_index]) + helper_block + "".join(lines[insert_line_index:])
    vlog(
        "mem",
        f"injected helpers index={state.MEMORY_INDEX_HELPER_NAME} adv={state.MEMORY_PTR_ADVANCE_HELPER_NAME} member={state.MEMORY_MEMBER_HELPER_NAME}, bytes {len(source_text)} -> {len(out)}",
    )
    return out


def inject_stl_wrappers(source_text):
    """Injects wrapper helpers for selected STL operations."""
    if not config.ENABLE_STL_WRAPPER_REWRITES:
        vlog("stl", "disabled")
        return source_text

    if state.STL_SORT_HELPER_NAME is None:
        vlog("stl", "no AST rewrites requested STL wrapper; skip injection")
        return source_text

    container_name = generate_barcode_name(18)
    first_name = generate_barcode_name(18)
    last_name = generate_barcode_name(18)
    size_name = generate_barcode_name(18)
    helper_variants = [
        "\n".join(
            [
                "namespace {",
                f"template <typename Container> __attribute__((noinline, noipa)) void {state.STL_SORT_HELPER_NAME}(Container& {container_name}) {{",
                f"    auto {first_name} = {container_name}.begin();",
                f"    auto {last_name} = {container_name}.end();",
                f'    __asm__ __volatile__("" : : "g"({first_name}), "g"({last_name}) : "memory");',
                f"    std::sort({first_name}, {last_name});",
                "}",
                "}",
                "",
            ]
        ),
        "\n".join(
            [
                "namespace {",
                f"template <typename Container> __attribute__((noinline, noipa)) void {state.STL_SORT_HELPER_NAME}(Container& {container_name}) {{",
                f"    auto {first_name} = {container_name}.begin();",
                f"    auto {last_name} = {container_name}.end();",
                f"    auto {size_name} = {container_name}.size();",
                f'    __asm__ __volatile__("" : : "g"({first_name}), "g"({last_name}), "g"({size_name}) : "memory");',
                f"    if ({size_name} > 1) {{",
                f"        std::sort({first_name}, {last_name});",
                "    }",
                "}",
                "}",
                "",
            ]
        ),
    ]
    helper_block = random.choice(helper_variants)

    lines = source_text.splitlines(keepends=True)
    insert_line_index = _find_insertion_line_index(lines)
    out = "".join(lines[:insert_line_index]) + helper_block + "".join(lines[insert_line_index:])
    vlog("stl", f"injected sort wrapper={state.STL_SORT_HELPER_NAME}, bytes {len(source_text)} -> {len(out)}")
    return out


def inject_dead_code_helpers(source_text):
    """Injects unused file-scope helper functions."""
    if not config.ENABLE_DEAD_CODE_INJECTION:
        vlog("dead_helpers", "disabled")
        return source_text

    state.init_dead_code_helper_names()
    helper_a, helper_b = state.DEAD_CODE_HELPER_NAMES
    seed_a = random.randint(0x100, 0xFFFF)
    seed_b = random.randint(0x100, 0xFFFF)
    value_a = generate_barcode_name(18)
    shadow_a = generate_barcode_name(18)
    loop_a = generate_barcode_name(18)
    value_b = generate_barcode_name(18)
    shadow_b = generate_barcode_name(18)
    mix_name = generate_barcode_name(18)
    helper_variants = [
        "\n".join(
            [
                "namespace {",
                f"__attribute__((noinline, noipa)) int {helper_a}(int {value_a}) {{",
                f"    int {shadow_a} = {value_a} ^ {seed_a};",
                f"    for (int {loop_a} = 0; {loop_a} < 3; ++{loop_a}) {{",
                f"        {shadow_a} = ({shadow_a} + {loop_a}) ^ {loop_a};",
                "    }",
                f"    return {shadow_a};",
                "}",
                f"__attribute__((noinline, noipa)) long long {helper_b}(long long {value_b}) {{",
                f"    long long {shadow_b} = {value_b} + {seed_b};",
                f"    if (({shadow_b} & 1LL) == 0) {{",
                f"        {shadow_b} ^= {value_b};",
                "    } else {",
                f"        {shadow_b} -= {value_b};",
                "    }",
                f"    return {shadow_b};",
                "}",
                "}",
                "",
            ]
        ),
        "\n".join(
            [
                "namespace {",
                f"__attribute__((noinline, noipa)) int {helper_a}(int {value_a}) {{",
                f"    int {shadow_a} = {value_a} + {seed_a};",
                f"    int {mix_name} = {shadow_a};",
                f"    for (int {loop_a} = 0; {loop_a} < 2; ++{loop_a}) {{",
                f"        {mix_name} = ({mix_name} ^ {loop_a}) - {loop_a};",
                "    }",
                f"    return {mix_name};",
                "}",
                f"__attribute__((noinline, noipa)) long long {helper_b}(long long {value_b}) {{",
                f"    long long {shadow_b} = ({value_b} ^ {seed_b}) + {seed_b};",
                f"    long long {mix_name} = ({shadow_b} & 1LL) ? ({shadow_b} - {value_b}) : ({shadow_b} + {value_b});",
                f"    return {mix_name};",
                "}",
                "}",
                "",
            ]
        ),
    ]
    helper_block = random.choice(helper_variants)

    lines = source_text.splitlines(keepends=True)
    insert_line_index = _find_insertion_line_index(lines)
    out = "".join(lines[:insert_line_index]) + helper_block + "".join(lines[insert_line_index:])
    vlog("dead_helpers", f"injected helpers={state.DEAD_CODE_HELPER_NAMES}, bytes {len(source_text)} -> {len(out)}")
    return out


def inject_tmp_addition_helpers(source_text):
    """Injects template-driven bitwise addition helpers for integral rewrites."""
    if not config.ENABLE_TMP_ADDITION_OBFUSCATION:
        vlog("tmp_add", "disabled")
        return source_text

    if state.TMP_ADD_STRUCT_NAME is None or state.TMP_ADD_HELPER_NAME is None:
        vlog("tmp_add", "no helper names initialized; skip injection")
        return source_text

    lhs_name = generate_barcode_name(18)
    rhs_name = generate_barcode_name(18)
    enable_name = generate_barcode_name(18)
    type_name = generate_barcode_name(18)
    word_name = generate_barcode_name(18)
    left_type_name = generate_barcode_name(18)
    right_type_name = generate_barcode_name(18)
    stage_struct_name = generate_barcode_name(18)
    bit_struct_name = generate_barcode_name(18)
    sequence_struct_name = generate_barcode_name(18)
    bit_name = generate_barcode_name(18)
    bits_name = generate_barcode_name(18)
    carry_name = generate_barcode_name(18)
    result_name = generate_barcode_name(18)
    sum_name = generate_barcode_name(18)
    next_name = generate_barcode_name(18)
    one_name = generate_barcode_name(18)
    seq_name = generate_barcode_name(18)
    unsigned_lhs_name = generate_barcode_name(18)
    unsigned_rhs_name = generate_barcode_name(18)
    variant = state.TMP_ADD_VARIANT or {
        "engine_mode": "recursive",
        "carry_mode": "majority",
        "entry_mode": "direct",
    }

    if variant["carry_mode"] == "xor_mix":
        next_expr = (
            f"static_cast<{word_name}>((({lhs_name} & {rhs_name}) | "
            f"(({lhs_name} ^ {rhs_name}) & {carry_name})) & {one_name})"
        )
    else:
        next_expr = (
            f"static_cast<{word_name}>((({lhs_name} & {rhs_name}) | "
            f"({lhs_name} & {carry_name}) | ({rhs_name} & {carry_name})) & {one_name})"
        )

    if variant["entry_mode"] == "swap":
        call_return = f"{state.TMP_ADD_STRUCT_NAME}<{word_name}>::eval({unsigned_rhs_name}, {unsigned_lhs_name})"
    elif variant["entry_mode"] == "zero_left":
        call_return = (
            f"static_cast<{word_name}>({unsigned_lhs_name}) + "
            f"{state.TMP_ADD_STRUCT_NAME}<{word_name}>::eval(static_cast<{word_name}>(0), {unsigned_rhs_name})"
        )
    elif variant["entry_mode"] == "zero_right":
        call_return = (
            f"{state.TMP_ADD_STRUCT_NAME}<{word_name}>::eval({unsigned_lhs_name}, static_cast<{word_name}>(0)) + "
            f"static_cast<{word_name}>({unsigned_rhs_name})"
        )
    else:
        call_return = f"{state.TMP_ADD_STRUCT_NAME}<{word_name}>::eval({unsigned_lhs_name}, {unsigned_rhs_name})"

    helper_signature = (
        f"template <typename {left_type_name}, typename {right_type_name}, "
        f"typename {type_name} = std::decay_t<decltype(std::declval<{left_type_name}>() + std::declval<{right_type_name}>())>, "
        f"typename {enable_name} = std::enable_if_t<std::is_integral_v<{type_name}>>> "
        f"__attribute__((noinline, noipa)) {type_name} {state.TMP_ADD_HELPER_NAME}"
        f"({left_type_name} {lhs_name}, {right_type_name} {rhs_name})"
    )

    recursive_variant = "\n".join(
        [
            "#include <type_traits>",
            "#include <utility>",
            "namespace {",
            f"template <typename {word_name}, std::size_t {bit_name}, std::size_t {bits_name}> struct {stage_struct_name} {{",
            f"    static {word_name} eval({word_name} {unsigned_lhs_name}, {word_name} {unsigned_rhs_name}, {word_name} {carry_name}) {{",
            f"        constexpr {word_name} {one_name} = static_cast<{word_name}>(1);",
            f"        const {word_name} {lhs_name} = static_cast<{word_name}>(({unsigned_lhs_name} >> {bit_name}) & {one_name});",
            f"        const {word_name} {rhs_name} = static_cast<{word_name}>(({unsigned_rhs_name} >> {bit_name}) & {one_name});",
            f"        const {word_name} {sum_name} = static_cast<{word_name}>(({lhs_name} ^ {rhs_name} ^ {carry_name}) & {one_name});",
            f"        const {word_name} {next_name} = {next_expr};",
            f"        return static_cast<{word_name}>(({sum_name} << {bit_name}) | "
            f"{stage_struct_name}<{word_name}, {bit_name} + 1, {bits_name}>::eval({unsigned_lhs_name}, {unsigned_rhs_name}, {next_name}));",
            "    }",
            "};",
            f"template <typename {word_name}, std::size_t {bits_name}> struct {stage_struct_name}<{word_name}, {bits_name}, {bits_name}> {{",
            f"    static {word_name} eval({word_name}, {word_name}, {word_name}) {{",
            f"        return static_cast<{word_name}>(0);",
            "    }",
            "};",
            f"template <typename {word_name}> struct {state.TMP_ADD_STRUCT_NAME} {{",
            f"    static {word_name} eval({word_name} {unsigned_lhs_name}, {word_name} {unsigned_rhs_name}) {{",
            f"        return {stage_struct_name}<{word_name}, 0, sizeof({word_name}) * 8>::eval(",
            f"            {unsigned_lhs_name}, {unsigned_rhs_name}, static_cast<{word_name}>(0));",
            "    }",
            "};",
            helper_signature + " {",
            f"    using {word_name} = std::make_unsigned_t<{type_name}>;",
            f"    const {word_name} {unsigned_lhs_name} = static_cast<{word_name}>(static_cast<{type_name}>({lhs_name}));",
            f"    const {word_name} {unsigned_rhs_name} = static_cast<{word_name}>(static_cast<{type_name}>({rhs_name}));",
            f"    return static_cast<{type_name}>({call_return});",
            "}",
            "}",
            "",
        ]
    )

    index_sequence_variant = "\n".join(
        [
            "#include <type_traits>",
            "#include <utility>",
            "namespace {",
            f"template <typename {word_name}, std::size_t {bit_name}> struct {bit_struct_name} {{",
            f"    static {word_name} eval({word_name} {unsigned_lhs_name}, {word_name} {unsigned_rhs_name}, {word_name}& {carry_name}) {{",
            f"        constexpr {word_name} {one_name} = static_cast<{word_name}>(1);",
            f"        const {word_name} {lhs_name} = static_cast<{word_name}>(({unsigned_lhs_name} >> {bit_name}) & {one_name});",
            f"        const {word_name} {rhs_name} = static_cast<{word_name}>(({unsigned_rhs_name} >> {bit_name}) & {one_name});",
            f"        const {word_name} {sum_name} = static_cast<{word_name}>(({lhs_name} ^ {rhs_name} ^ {carry_name}) & {one_name});",
            f"        const {word_name} {next_name} = {next_expr};",
            f"        {carry_name} = {next_name};",
            f"        return static_cast<{word_name}>({sum_name} << {bit_name});",
            "    }",
            "};",
            f"template <typename {word_name}, typename {seq_name}> struct {sequence_struct_name};",
            f"template <typename {word_name}, std::size_t... {bit_name}> struct {sequence_struct_name}<{word_name}, std::index_sequence<{bit_name}...>> {{",
            f"    static {word_name} eval({word_name} {unsigned_lhs_name}, {word_name} {unsigned_rhs_name}) {{",
            f"        {word_name} {carry_name} = static_cast<{word_name}>(0);",
            f"        {word_name} {result_name} = static_cast<{word_name}>(0);",
            f"        (({result_name} = static_cast<{word_name}>({result_name} | "
            f"{bit_struct_name}<{word_name}, {bit_name}>::eval({unsigned_lhs_name}, {unsigned_rhs_name}, {carry_name}))), ...);",
            f"        return {result_name};",
            "    }",
            "};",
            helper_signature + " {",
            f"    using {word_name} = std::make_unsigned_t<{type_name}>;",
            f"    using {seq_name} = std::make_index_sequence<sizeof({word_name}) * 8>;",
            f"    const {word_name} {unsigned_lhs_name} = static_cast<{word_name}>(static_cast<{type_name}>({lhs_name}));",
            f"    const {word_name} {unsigned_rhs_name} = static_cast<{word_name}>(static_cast<{type_name}>({rhs_name}));",
            f"    return static_cast<{type_name}>({sequence_struct_name}<{word_name}, {seq_name}>::eval({unsigned_lhs_name}, {unsigned_rhs_name}));",
            "}",
            "}",
            "",
        ]
    )

    helper_variants = [
        recursive_variant,
        index_sequence_variant,
        "\n".join(
            [
                "#include <type_traits>",
                "#include <utility>",
                "namespace {",
                f"template <typename {word_name}, std::size_t {bit_name}, std::size_t {bits_name}> struct {stage_struct_name} {{",
                f"    static {word_name} eval({word_name} {unsigned_lhs_name}, {word_name} {unsigned_rhs_name}, {word_name} {carry_name}) {{",
                f"        constexpr {word_name} {one_name} = static_cast<{word_name}>(1);",
                f"        const {word_name} {lhs_name} = static_cast<{word_name}>(({unsigned_lhs_name} >> {bit_name}) & {one_name});",
                f"        const {word_name} {rhs_name} = static_cast<{word_name}>(({unsigned_rhs_name} >> {bit_name}) & {one_name});",
                f"        const {word_name} {sum_name} = static_cast<{word_name}>(({lhs_name} ^ {rhs_name} ^ {carry_name}) & {one_name});",
                f"        const {word_name} {next_name} = {next_expr};",
                f"        return static_cast<{word_name}>(({sum_name} << {bit_name}) ^ "
                f"{stage_struct_name}<{word_name}, {bit_name} + 1, {bits_name}>::eval({unsigned_lhs_name}, {unsigned_rhs_name}, {next_name}));",
                "    }",
                "};",
                f"template <typename {word_name}, std::size_t {bits_name}> struct {stage_struct_name}<{word_name}, {bits_name}, {bits_name}> {{",
                f"    static {word_name} eval({word_name}, {word_name}, {word_name}) {{",
                f"        return static_cast<{word_name}>(0);",
                "    }",
                "};",
                f"template <typename {word_name}> struct {state.TMP_ADD_STRUCT_NAME} {{",
                f"    static {word_name} eval({word_name} {unsigned_lhs_name}, {word_name} {unsigned_rhs_name}) {{",
                f"        return {stage_struct_name}<{word_name}, 0, sizeof({word_name}) * 8>::eval(",
                f"            {unsigned_lhs_name}, {unsigned_rhs_name}, static_cast<{word_name}>(0));",
                "    }",
                "};",
                helper_signature + " {",
                f"    using {word_name} = std::make_unsigned_t<{type_name}>;",
                f"    const {word_name} {unsigned_lhs_name} = static_cast<{word_name}>(static_cast<{type_name}>({lhs_name}));",
                f"    const {word_name} {unsigned_rhs_name} = static_cast<{word_name}>(static_cast<{type_name}>({rhs_name}));",
                f"    return static_cast<{type_name}>({call_return});",
                "}",
                "}",
                "",
            ]
        ),
    ]

    if variant["engine_mode"] == "index_sequence":
        helper_block = helper_variants[1]
    elif variant["carry_mode"] == "xor_mix":
        helper_block = helper_variants[2]
    else:
        helper_block = helper_variants[0]

    lines = source_text.splitlines(keepends=True)
    insert_line_index = _find_insertion_line_index(lines)
    out = "".join(lines[:insert_line_index]) + helper_block + "".join(lines[insert_line_index:])
    vlog(
        "tmp_add",
        f"injected struct={state.TMP_ADD_STRUCT_NAME} helper={state.TMP_ADD_HELPER_NAME} variant={state.TMP_ADD_VARIANT}, bytes {len(source_text)} -> {len(out)}",
    )
    return out


def inject_function_pointer_helpers(source_text):
    """Injects helper templates for staged function-pointer dispatch."""
    if not config.ENABLE_FUNCTION_POINTER_INDIRECTION:
        vlog("fnptr", "disabled")
        return source_text

    if state.FUNCTION_PTR_STAGE_HELPER_NAME is None or state.FUNCTION_PTR_INVOKE_HELPER_NAME is None:
        vlog("fnptr", "no helper names initialized; skip injection")
        return source_text

    fn_name = generate_barcode_name(18)
    staged_name = generate_barcode_name(18)
    left_type_name = generate_barcode_name(18)
    arg_pack_name = generate_barcode_name(18)
    result_name = generate_barcode_name(18)
    enable_name = generate_barcode_name(18)
    helper_variants = [
        "\n".join(
            [
                "#include <type_traits>",
                "namespace {",
                f"template <typename {left_type_name}> __attribute__((noinline, noipa)) {left_type_name} {state.FUNCTION_PTR_STAGE_HELPER_NAME}({left_type_name} {fn_name}) {{",
                f"    auto {staged_name} = {fn_name};",
                f'    __asm__ __volatile__("" : "+r"({staged_name}) : : "memory");',
                f"    return {staged_name};",
                "}",
                f"template <typename {left_type_name}, typename... {arg_pack_name}, typename {enable_name} = std::enable_if_t<!std::is_void_v<std::invoke_result_t<{left_type_name}, {arg_pack_name}...>>>>",
                f"__attribute__((noinline, noipa)) auto {state.FUNCTION_PTR_INVOKE_HELPER_NAME}({left_type_name} {fn_name}, {arg_pack_name}... {result_name}) -> std::invoke_result_t<{left_type_name}, {arg_pack_name}...> {{",
                f"    auto {staged_name} = {state.FUNCTION_PTR_STAGE_HELPER_NAME}({fn_name});",
                f"    return {staged_name}({result_name}...);",
                "}",
                f"template <typename {left_type_name}, typename... {arg_pack_name}, typename {enable_name} = std::enable_if_t<std::is_void_v<std::invoke_result_t<{left_type_name}, {arg_pack_name}...>>>>",
                f"__attribute__((noinline, noipa)) void {state.FUNCTION_PTR_INVOKE_HELPER_NAME}({left_type_name} {fn_name}, {arg_pack_name}... {result_name}) {{",
                f"    auto {staged_name} = {state.FUNCTION_PTR_STAGE_HELPER_NAME}({fn_name});",
                f"    {staged_name}({result_name}...);",
                "}",
                "}",
                "",
            ]
        ),
        "\n".join(
            [
                "#include <type_traits>",
                "namespace {",
                f"template <typename {left_type_name}> __attribute__((noinline, noipa)) {left_type_name} {state.FUNCTION_PTR_STAGE_HELPER_NAME}({left_type_name} {fn_name}) {{",
                f'    __asm__ __volatile__("" : "+r"({fn_name}) : : "memory");',
                f"    return {fn_name};",
                "}",
                f"template <typename {left_type_name}, typename... {arg_pack_name}, typename {enable_name} = std::invoke_result_t<{left_type_name}, {arg_pack_name}...>>",
                f"__attribute__((noinline, noipa)) std::enable_if_t<!std::is_void_v<{enable_name}>, {enable_name}> {state.FUNCTION_PTR_INVOKE_HELPER_NAME}({left_type_name} {fn_name}, {arg_pack_name}... {result_name}) {{",
                f"    return {state.FUNCTION_PTR_STAGE_HELPER_NAME}({state.FUNCTION_PTR_STAGE_HELPER_NAME}({fn_name}))({result_name}...);",
                "}",
                f"template <typename {left_type_name}, typename... {arg_pack_name}, typename {enable_name} = std::invoke_result_t<{left_type_name}, {arg_pack_name}...>>",
                f"__attribute__((noinline, noipa)) std::enable_if_t<std::is_void_v<{enable_name}>, void> {state.FUNCTION_PTR_INVOKE_HELPER_NAME}({left_type_name} {fn_name}, {arg_pack_name}... {result_name}) {{",
                f"    {state.FUNCTION_PTR_STAGE_HELPER_NAME}({state.FUNCTION_PTR_STAGE_HELPER_NAME}({fn_name}))({result_name}...);",
                "}",
                "}",
                "",
            ]
        ),
    ]
    helper_block = random.choice(helper_variants)

    lines = source_text.splitlines(keepends=True)
    insert_line_index = _find_insertion_line_index(lines)
    out = "".join(lines[:insert_line_index]) + helper_block + "".join(lines[insert_line_index:])
    vlog(
        "fnptr",
        f"injected stage={state.FUNCTION_PTR_STAGE_HELPER_NAME} invoke={state.FUNCTION_PTR_INVOKE_HELPER_NAME}, bytes {len(source_text)} -> {len(out)}",
    )
    return out


def inject_data_flow_helpers(source_text):
    """Injects helper templates for variable splitting and merging."""
    if not config.ENABLE_DATA_FLOW_OBFUSCATION:
        vlog("dataflow", "disabled")
        return source_text

    if (
        state.DATA_FLOW_STRUCT_NAME is None
        or state.DATA_FLOW_PACK_HELPER_NAME is None
        or state.DATA_FLOW_UNPACK_HELPER_NAME is None
        or state.DATA_FLOW_MERGE_HELPER_NAME is None
    ):
        vlog("dataflow", "no helper names initialized; skip injection")
        return source_text

    variant = state.DATA_FLOW_VARIANT or {
        "share_mode": "sum",
        "mask_a": 0x5A,
        "mask_b": 0x33,
        "merge_mode": "direct",
        "unpack_mode": "plain",
    }
    lhs_name = generate_barcode_name(18)
    rhs_name = generate_barcode_name(18)
    value_name = generate_barcode_name(18)
    part_a_name = generate_barcode_name(18)
    part_b_name = generate_barcode_name(18)
    noise_name = generate_barcode_name(18)
    op_name = generate_barcode_name(18)
    enable_name = generate_barcode_name(18)

    mask_a_expr = f"static_cast<T>({variant['mask_a']})"
    mask_b_expr = f"static_cast<T>({variant['mask_b']})"
    if variant["share_mode"] == "delta":
        pack_body = [
            f"        T {part_a_name} = {value_name} + {mask_a_expr};",
            f"        T {part_b_name} = {mask_a_expr};",
            f"        T {noise_name} = ({value_name} ^ {mask_b_expr}) + {mask_a_expr};",
            f"        return {{{part_a_name}, {part_b_name}, {noise_name}}};",
        ]
        unpack_expr = f"{value_name}.a - {value_name}.b"
        merge_plus = f"{state.DATA_FLOW_STRUCT_NAME}<T>{{{lhs_name}.a + {rhs_name}.a, {lhs_name}.b + {rhs_name}.b, ({lhs_name}.noise ^ {rhs_name}.noise)}}"
        merge_minus = f"{state.DATA_FLOW_STRUCT_NAME}<T>{{{lhs_name}.a - {rhs_name}.a, {lhs_name}.b - {rhs_name}.b, ({lhs_name}.noise + {rhs_name}.noise)}}"
    else:
        pack_body = [
            f"        T {part_a_name} = {value_name} - {mask_a_expr};",
            f"        T {part_b_name} = {mask_a_expr};",
            f"        T {noise_name} = ({value_name} ^ {mask_b_expr}) - {mask_a_expr};",
            f"        return {{{part_a_name}, {part_b_name}, {noise_name}}};",
        ]
        unpack_expr = f"{value_name}.a + {value_name}.b"
        merge_plus = f"{state.DATA_FLOW_STRUCT_NAME}<T>{{{lhs_name}.a + {rhs_name}.a, {lhs_name}.b + {rhs_name}.b, ({lhs_name}.noise ^ {rhs_name}.noise)}}"
        merge_minus = f"{state.DATA_FLOW_STRUCT_NAME}<T>{{{lhs_name}.a - {rhs_name}.a, {lhs_name}.b - {rhs_name}.b, ({lhs_name}.noise - {rhs_name}.noise)}}"

    if variant["unpack_mode"] == "mask_fold":
        unpack_lines = [
            f"    T {noise_name} = static_cast<T>({value_name}.noise ^ {mask_b_expr});",
            f"    return static_cast<T>(({unpack_expr}) + ({noise_name} * static_cast<T>(0)));",
        ]
    elif variant["unpack_mode"] == "volatile_noise":
        unpack_lines = [
            f"    auto {noise_name} = {value_name}.noise;",
            f'    __asm__ __volatile__("" : "+r"({noise_name}) : : "memory");',
            f"    return {unpack_expr};",
        ]
    else:
        unpack_lines = [f"    return {unpack_expr};"]

    helper_variants = [
        "\n".join(
            [
                "#include <type_traits>",
                "namespace {",
                f"template <typename T, typename {enable_name} = std::enable_if_t<std::is_integral_v<T>>>",
                f"struct {state.DATA_FLOW_STRUCT_NAME} {{",
                "    T a;",
                "    T b;",
                "    T noise;",
                "};",
                f"template <typename T, typename {enable_name} = std::enable_if_t<std::is_integral_v<T>>>",
                f"__attribute__((noinline, noipa)) {state.DATA_FLOW_STRUCT_NAME}<T> {state.DATA_FLOW_PACK_HELPER_NAME}(T {value_name}) {{",
                *pack_body,
                "}",
                f"template <typename T, typename {enable_name} = std::enable_if_t<std::is_integral_v<T>>>",
                f"__attribute__((noinline, noipa)) T {state.DATA_FLOW_UNPACK_HELPER_NAME}({state.DATA_FLOW_STRUCT_NAME}<T> {value_name}) {{",
                *unpack_lines,
                "}",
                f"template <typename T, typename {enable_name} = std::enable_if_t<std::is_integral_v<T>>>",
                f"__attribute__((noinline, noipa)) {state.DATA_FLOW_STRUCT_NAME}<T> {state.DATA_FLOW_MERGE_HELPER_NAME}({state.DATA_FLOW_STRUCT_NAME}<T> {lhs_name}, {state.DATA_FLOW_STRUCT_NAME}<T> {rhs_name}, char {op_name}) {{",
                f"    return ({op_name} == '+') ? {merge_plus} : {merge_minus};",
                "}",
                "}",
                "",
            ]
        ),
        "\n".join(
            [
                "#include <type_traits>",
                "namespace {",
                f"template <typename T, typename {enable_name} = std::enable_if_t<std::is_integral_v<T>>>",
                f"struct {state.DATA_FLOW_STRUCT_NAME} {{",
                "    T a;",
                "    T b;",
                "    T noise;",
                "};",
                f"template <typename T, typename {enable_name} = std::enable_if_t<std::is_integral_v<T>>>",
                f"__attribute__((noinline, noipa)) {state.DATA_FLOW_STRUCT_NAME}<T> {state.DATA_FLOW_PACK_HELPER_NAME}(T {value_name}) {{",
                *pack_body,
                "}",
                f"template <typename T, typename {enable_name} = std::enable_if_t<std::is_integral_v<T>>>",
                f"__attribute__((noinline, noipa)) T {state.DATA_FLOW_UNPACK_HELPER_NAME}({state.DATA_FLOW_STRUCT_NAME}<T> {value_name}) {{",
                *unpack_lines,
                "}",
                f"template <typename T, typename {enable_name} = std::enable_if_t<std::is_integral_v<T>>>",
                f"__attribute__((noinline, noipa)) {state.DATA_FLOW_STRUCT_NAME}<T> {state.DATA_FLOW_MERGE_HELPER_NAME}({state.DATA_FLOW_STRUCT_NAME}<T> {lhs_name}, {state.DATA_FLOW_STRUCT_NAME}<T> {rhs_name}, char {op_name}) {{",
                f"    auto {noise_name} = ({lhs_name}.noise ^ {rhs_name}.noise);",
                f'    __asm__ __volatile__("" : "+r"({noise_name}) : : "memory");',
                f"    return ({op_name} == '+') ? {merge_plus} : {merge_minus};",
                "}",
                "}",
                "",
            ]
        ),
    ]
    helper_block = random.choice(helper_variants)

    lines = source_text.splitlines(keepends=True)
    insert_line_index = _find_insertion_line_index(lines)
    out = "".join(lines[:insert_line_index]) + helper_block + "".join(lines[insert_line_index:])
    vlog(
        "dataflow",
        f"injected struct={state.DATA_FLOW_STRUCT_NAME} pack={state.DATA_FLOW_PACK_HELPER_NAME} unpack={state.DATA_FLOW_UNPACK_HELPER_NAME} merge={state.DATA_FLOW_MERGE_HELPER_NAME} variant={state.DATA_FLOW_VARIANT}, bytes {len(source_text)} -> {len(out)}",
    )
    return out


def inject_string_literal_helpers(source_text):
    """Injects constexpr and fake decoders for string literal encryption."""
    if not config.ENABLE_STRING_LITERAL_ENCRYPTION:
        vlog("strings", "disabled")
        return source_text

    if state.STRING_DECODE_HELPER_NAME is None:
        vlog("strings", "no literals rewritten (decoder name not initialized); skip helper injection")
        return source_text

    state.init_string_literal_names()
    fake_a, fake_b = state.STRING_FAKE_DECODER_NAMES
    input_name = generate_barcode_name(18)
    key_a_name = generate_barcode_name(18)
    key_b_name = generate_barcode_name(18)
    stage_name = generate_barcode_name(18)
    out_name = generate_barcode_name(18)
    idx_name = generate_barcode_name(18)
    quartet_name = generate_barcode_name(18)
    helper_block = "\n".join(
        [
            "#include <array>",
            "namespace {",
            "__attribute__((noinline, noipa)) int " + fake_a + "(const char* value) {",
            "    int total = 0;",
            "    while (*value) {",
            "        total = (total * 33) ^ static_cast<unsigned char>(*value++);",
            "    }",
            "    return total;",
            "}",
            "__attribute__((noinline, noipa)) long long " + fake_b + "(const char* value) {",
            "    long long total = 0;",
            "    while (*value) {",
            "        total += static_cast<unsigned char>(*value++) * 17LL;",
            "    }",
            "    return total;",
            "}",
            "constexpr unsigned char " + state.STRING_DECODE_HELPER_NAME + "_b64(char value) {",
            "    return (value >= 'A' && value <= 'Z') ? static_cast<unsigned char>(value - 'A') :",
            "           (value >= 'a' && value <= 'z') ? static_cast<unsigned char>(value - 'a' + 26) :",
            "           (value >= '0' && value <= '9') ? static_cast<unsigned char>(value - '0' + 52) :",
            "           (value == '+') ? 62 :",
            "           (value == '/') ? 63 : 0;",
            "}",
            f"template <std::size_t OutSize, std::size_t N> constexpr std::array<char, OutSize + 1> {state.STRING_DECODE_HELPER_NAME}(const char (&{input_name})[N], unsigned char {key_a_name}, unsigned char {key_b_name}) {{",
            f"    std::array<char, N - 1> {stage_name}{{}};",
            f"    for (std::size_t {idx_name} = 0; {idx_name} < N - 1; ++{idx_name}) {{",
            f"        {stage_name}[{idx_name}] = static_cast<char>(static_cast<unsigned char>({input_name}[{idx_name}]) ^ {key_b_name});",
            "    }",
            f"    std::array<char, OutSize + 1> {out_name}{{}};",
            "    std::size_t out_index = 0;",
            f"    for (std::size_t {idx_name} = 0; {idx_name} + 3 < N - 1 && out_index < OutSize; {idx_name} += 4) {{",
            f"        unsigned int {quartet_name} = (static_cast<unsigned int>({state.STRING_DECODE_HELPER_NAME}_b64({stage_name}[{idx_name}])) << 18)",
            f"            | (static_cast<unsigned int>({state.STRING_DECODE_HELPER_NAME}_b64({stage_name}[{idx_name} + 1])) << 12)",
            f"            | (static_cast<unsigned int>({state.STRING_DECODE_HELPER_NAME}_b64({stage_name}[{idx_name} + 2])) << 6)",
            f"            | static_cast<unsigned int>({state.STRING_DECODE_HELPER_NAME}_b64({stage_name}[{idx_name} + 3]));",
            "        if (out_index < OutSize) { out_index += 1; }",
            f"        {out_name}[out_index - 1] = static_cast<char>((({quartet_name} >> 16) & 0xFFu) ^ {key_a_name});",
            f"        if ({stage_name}[{idx_name} + 2] != '=' && out_index < OutSize) {{",
            f"            {out_name}[out_index++] = static_cast<char>((({quartet_name} >> 8) & 0xFFu) ^ {key_a_name});",
            "        }",
            f"        if ({stage_name}[{idx_name} + 3] != '=' && out_index < OutSize) {{",
            f"            {out_name}[out_index++] = static_cast<char>(({quartet_name} & 0xFFu) ^ {key_a_name});",
            "        }",
            "    }",
            f"    {out_name}[OutSize] = '\\0';",
            f"    if ((sizeof(long long) == 0) && ({fake_a}(reinterpret_cast<const char*>({out_name}.data())) == {fake_b}(reinterpret_cast<const char*>({out_name}.data())))) {{",
            f"        {out_name}[0] ^= 0;",
            "    }",
            f"    return {out_name};",
            "}",
            "}",
            "",
        ]
    )

    lines = source_text.splitlines(keepends=True)
    insert_line_index = _find_insertion_line_index(lines)
    out = "".join(lines[:insert_line_index]) + helper_block + "".join(lines[insert_line_index:])
    vlog(
        "strings",
        f"injected decoder={state.STRING_DECODE_HELPER_NAME} fakes={state.STRING_FAKE_DECODER_NAMES}, bytes {len(source_text)} -> {len(out)}",
    )
    return out


def inject_type_level_helpers(source_text):
    """Injects primitive wrapper types with overloaded operators."""
    if not config.ENABLE_TYPE_LEVEL_OBFUSCATION:
        vlog("typelevel", "disabled")
        return source_text
    if state.OPAQUE_WRAPPER_NAME is None:
        vlog("typelevel", "no wrapper name initialized; skip injection")
        return source_text

    state.init_type_level_names()
    variant = state.OPAQUE_WRAPPER_VARIANT or {
        "key_a": 0x55,
        "key_b": 0x33,
        "encode_mode": "xor_add",
        "compare_mode": "direct",
    }
    value_name = generate_barcode_name(18)
    storage_name = generate_barcode_name(18)
    rhs_name = generate_barcode_name(18)
    key_a_name = generate_barcode_name(18)
    key_b_name = generate_barcode_name(18)
    compare_type = (
        "std::decay_t<decltype(static_cast<T>(lhs.value()) + static_cast<T>(rhs))>"
        if variant["compare_mode"] == "common_type"
        else "T"
    )
    compare_lhs = (
        f"static_cast<{compare_type}>(lhs.value())" if variant["compare_mode"] == "common_type" else "lhs.value()"
    )
    compare_rhs = f"static_cast<{compare_type}>(rhs)"
    if variant["encode_mode"] == "add_xor":
        encode_expr = f"static_cast<T>(({value_name} + {key_b_name}) ^ {key_a_name})"
        decode_expr = f"static_cast<T>(({value_name} ^ {key_a_name}) - {key_b_name})"
    elif variant["encode_mode"] == "xor_sub":
        encode_expr = f"static_cast<T>(({value_name} ^ {key_a_name}) - {key_b_name})"
        decode_expr = f"static_cast<T>(({value_name} + {key_b_name}) ^ {key_a_name})"
    else:
        encode_expr = f"static_cast<T>(({value_name} ^ {key_a_name}) + {key_b_name})"
        decode_expr = f"static_cast<T>(({value_name} - {key_b_name}) ^ {key_a_name})"

    helper_variants = [
        "\n".join(
            [
                "#include <type_traits>",
                "namespace {",
                f"template <typename T, typename = std::enable_if_t<std::is_integral_v<T> && !std::is_same_v<T, bool>>> struct {state.OPAQUE_WRAPPER_NAME} {{",
                f"    T {storage_name};",
                f"    static constexpr T {key_a_name} = static_cast<T>({variant['key_a']});",
                f"    static constexpr T {key_b_name} = static_cast<T>({variant['key_b']});",
                f"    static constexpr T encode(T {value_name}) {{ return {encode_expr}; }}",
                f"    static constexpr T decode(T {value_name}) {{ return {decode_expr}; }}",
                f"    constexpr {state.OPAQUE_WRAPPER_NAME}() : {storage_name}(encode(0)) {{}}",
                f"    constexpr {state.OPAQUE_WRAPPER_NAME}(T {value_name}) : {storage_name}(encode({value_name})) {{}}",
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
                f"template <typename T, typename U> bool operator==(const {state.OPAQUE_WRAPPER_NAME}<T>& lhs, const U& rhs) {{ return {compare_lhs} == {compare_rhs}; }}",
                f"template <typename T, typename U> bool operator!=(const {state.OPAQUE_WRAPPER_NAME}<T>& lhs, const U& rhs) {{ return {compare_lhs} != {compare_rhs}; }}",
                f"template <typename T, typename U> bool operator<(const {state.OPAQUE_WRAPPER_NAME}<T>& lhs, const U& rhs) {{ return {compare_lhs} < {compare_rhs}; }}",
                f"template <typename T, typename U> bool operator<=(const {state.OPAQUE_WRAPPER_NAME}<T>& lhs, const U& rhs) {{ return {compare_lhs} <= {compare_rhs}; }}",
                f"template <typename T, typename U> bool operator>(const {state.OPAQUE_WRAPPER_NAME}<T>& lhs, const U& rhs) {{ return {compare_lhs} > {compare_rhs}; }}",
                f"template <typename T, typename U> bool operator>=(const {state.OPAQUE_WRAPPER_NAME}<T>& lhs, const U& rhs) {{ return {compare_lhs} >= {compare_rhs}; }}",
                "}",
                "",
            ]
        ),
        "\n".join(
            [
                "#include <type_traits>",
                "namespace {",
                f"template <typename T, typename = std::enable_if_t<std::is_integral_v<T> && !std::is_same_v<T, bool>>> struct {state.OPAQUE_WRAPPER_NAME} {{",
                f"    T {storage_name};",
                f"    static constexpr T {key_a_name} = static_cast<T>({variant['key_a']});",
                f"    static constexpr T {key_b_name} = static_cast<T>({variant['key_b']});",
                f"    static constexpr T encode(T {value_name}) {{ return {encode_expr}; }}",
                f"    static constexpr T decode(T {value_name}) {{ return {decode_expr}; }}",
                f"    constexpr {state.OPAQUE_WRAPPER_NAME}() : {storage_name}(encode(0)) {{}}",
                f"    constexpr {state.OPAQUE_WRAPPER_NAME}(T {value_name}) : {storage_name}(encode({value_name})) {{}}",
                f"    constexpr T value() const {{ return decode({storage_name}); }}",
                "    constexpr operator T() const { return value(); }",
                f"    {state.OPAQUE_WRAPPER_NAME}& operator=(T {value_name}) {{ {storage_name} = encode({value_name}); return *this; }}",
                f"    {state.OPAQUE_WRAPPER_NAME}& operator++() {{ {storage_name} = encode(static_cast<T>(value() + 1)); return *this; }}",
                f"    {state.OPAQUE_WRAPPER_NAME} operator++(int) {{ auto copy = *this; {storage_name} = encode(static_cast<T>(value() + 1)); return copy; }}",
                f"    {state.OPAQUE_WRAPPER_NAME}& operator+=(T {rhs_name}) {{ {storage_name} = encode(static_cast<T>(value() + {rhs_name})); return *this; }}",
                f"    {state.OPAQUE_WRAPPER_NAME}& operator-=(T {rhs_name}) {{ {storage_name} = encode(static_cast<T>(value() - {rhs_name})); return *this; }}",
                "};",
                f"template <typename T, typename U> auto operator+(const {state.OPAQUE_WRAPPER_NAME}<T>& lhs, const U& rhs) -> {state.OPAQUE_WRAPPER_NAME}<std::common_type_t<T, U>> {{ using R = std::common_type_t<T, U>; return {state.OPAQUE_WRAPPER_NAME}<R>(static_cast<R>(lhs.value()) + static_cast<R>(rhs)); }}",
                f"template <typename T, typename U> auto operator-(const {state.OPAQUE_WRAPPER_NAME}<T>& lhs, const U& rhs) -> {state.OPAQUE_WRAPPER_NAME}<std::common_type_t<T, U>> {{ using R = std::common_type_t<T, U>; return {state.OPAQUE_WRAPPER_NAME}<R>(static_cast<R>(lhs.value()) - static_cast<R>(rhs)); }}",
                f"template <typename T, typename U> auto operator*(const {state.OPAQUE_WRAPPER_NAME}<T>& lhs, const U& rhs) -> {state.OPAQUE_WRAPPER_NAME}<std::common_type_t<T, U>> {{ using R = std::common_type_t<T, U>; return {state.OPAQUE_WRAPPER_NAME}<R>(static_cast<R>(lhs.value()) * static_cast<R>(rhs)); }}",
                f"template <typename T, typename U> auto operator/(const {state.OPAQUE_WRAPPER_NAME}<T>& lhs, const U& rhs) -> {state.OPAQUE_WRAPPER_NAME}<std::common_type_t<T, U>> {{ using R = std::common_type_t<T, U>; return {state.OPAQUE_WRAPPER_NAME}<R>(static_cast<R>(lhs.value()) / static_cast<R>(rhs)); }}",
                f"template <typename T, typename U> bool operator==(const {state.OPAQUE_WRAPPER_NAME}<T>& lhs, const U& rhs) {{ return {compare_lhs} == {compare_rhs}; }}",
                f"template <typename T, typename U> bool operator!=(const {state.OPAQUE_WRAPPER_NAME}<T>& lhs, const U& rhs) {{ return {compare_lhs} != {compare_rhs}; }}",
                f"template <typename T, typename U> bool operator<(const {state.OPAQUE_WRAPPER_NAME}<T>& lhs, const U& rhs) {{ return {compare_lhs} < {compare_rhs}; }}",
                f"template <typename T, typename U> bool operator<=(const {state.OPAQUE_WRAPPER_NAME}<T>& lhs, const U& rhs) {{ return {compare_lhs} <= {compare_rhs}; }}",
                f"template <typename T, typename U> bool operator>(const {state.OPAQUE_WRAPPER_NAME}<T>& lhs, const U& rhs) {{ return {compare_lhs} > {compare_rhs}; }}",
                f"template <typename T, typename U> bool operator>=(const {state.OPAQUE_WRAPPER_NAME}<T>& lhs, const U& rhs) {{ return {compare_lhs} >= {compare_rhs}; }}",
                "}",
                "",
            ]
        ),
    ]
    helper_block = random.choice(helper_variants)

    lines = source_text.splitlines(keepends=True)
    insert_line_index = _find_insertion_line_index(lines)
    out = "".join(lines[:insert_line_index]) + helper_block + "".join(lines[insert_line_index:])
    vlog(
        "typelevel",
        f"injected wrapper={state.OPAQUE_WRAPPER_NAME} variant={state.OPAQUE_WRAPPER_VARIANT}, bytes {len(source_text)} -> {len(out)}",
    )
    return out
