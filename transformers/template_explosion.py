import random

import config
from util import generate_barcode_name, iter_function_definitions, vlog


def _find_insertion_line_index(lines):
    insert_line_index = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            insert_line_index = index + 1
            continue
        break
    return insert_line_index


def _build_template_family():
    base_name = generate_barcode_name(18)
    tag_name = generate_barcode_name(18)
    value_name = generate_barcode_name(18)
    lines = [
        "namespace {",
        f"template <int {tag_name}> struct {base_name} {{",
        f"    static constexpr int {value_name} = {base_name}<{tag_name} - 1>::{value_name} + {base_name}<{tag_name} - 2>::{value_name} + 1;",
        "};",
        f"template <> struct {base_name}<0> {{ static constexpr int {value_name} = 1; }};",
        f"template <> struct {base_name}<1> {{ static constexpr int {value_name} = 1; }};",
        "}",
        "",
    ]
    return base_name, value_name, "\n".join(lines)


def apply_template_explosion(source_text):
    if not config.ENABLE_TEMPLATE_EXPLOSION:
        vlog("tpl_boom", "disabled")
        return source_text

    eligible = []
    for function_info in iter_function_definitions(source_text):
        if function_info.get("skip_structural"):
            continue
        eligible.append(function_info)

    if not eligible:
        vlog("tpl_boom", "no eligible functions; no-op")
        return source_text

    template_name, value_name, helper_block = _build_template_family()
    replacements = []
    chosen_count = min(len(eligible), max(1, random.randint(1, 3)))
    chosen = random.sample(eligible, chosen_count)
    depths = []
    for function_info in chosen:
        depth = random.randint(9, 12)
        depths.append(depth)
        brace_index = function_info["brace_index"]
        base_indent = function_info["base_indent"]
        inner_indent = base_indent + "    "
        sink_name = generate_barcode_name(18)
        expr_name = generate_barcode_name(18)
        block = "\n".join(
            [
                "",
                f"{inner_indent}if ((sizeof(int) == 0) && ({template_name}<{depth}>::{value_name} != 0)) {{",
                f"{inner_indent}    volatile int {sink_name} = {template_name}<{depth}>::{value_name};",
                f"{inner_indent}    int {expr_name} = static_cast<int>(sizeof({template_name}<{depth}>));",
                f"{inner_indent}    {sink_name} ^= {expr_name};",
                f"{inner_indent}    (void){sink_name};",
                f"{inner_indent}}}",
            ]
        )
        replacements.append((brace_index + 1, brace_index + 1, block))

    content = source_text
    for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
        content = content[:start] + replacement + content[end:]

    lines = content.splitlines(keepends=True)
    insert_line_index = _find_insertion_line_index(lines)
    out = "".join(lines[:insert_line_index]) + helper_block + "".join(lines[insert_line_index:])
    vlog(
        "tpl_boom",
        f"injected_functions={len(chosen)}, depths={depths}, bytes {len(source_text)} -> {len(out)}",
    )
    return out
