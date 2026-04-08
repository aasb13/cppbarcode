import random
import re

import config
from transformers.structural import _extract_flatten_return_type
from util import find_matching_brace, generate_barcode_name, has_vm_skip_marker, is_performance_sensitive_function, vlog


def _build_dead_variable_noise(indent, seed, mask):
    int_a_name = generate_barcode_name(18)
    int_b_name = generate_barcode_name(18)
    int_c_name = generate_barcode_name(18)
    int_mix_name = generate_barcode_name(18)
    int_shadow_name = generate_barcode_name(18)
    double_a_name = generate_barcode_name(18)
    double_b_name = generate_barcode_name(18)
    double_mix_name = generate_barcode_name(18)
    double_shadow_name = generate_barcode_name(18)
    sink_name = generate_barcode_name(18)
    tweak_a = random.randint(3, 17)
    tweak_b = random.randint(5, 23)
    divisor = random.randint(2, 9)
    offset = random.randint(1, 11)
    return [
        f"{indent}int {int_a_name} = {seed};",
        f"{indent}int {int_b_name} = ({int_a_name} ^ {mask}) + {tweak_a};",
        f"{indent}int {int_c_name} = ({int_b_name} + {int_a_name}) - ({mask} + {tweak_a});",
        f"{indent}int {int_mix_name} = ({int_c_name} ^ {int_b_name}) + ({int_a_name} & {mask});",
        f"{indent}int {int_shadow_name} = ({int_mix_name} + {int_c_name}) - {int_mix_name};",
        f"{indent}double {double_a_name} = static_cast<double>({int_shadow_name}) / {divisor}.0;",
        f"{indent}double {double_b_name} = {double_a_name} + static_cast<double>({mask}) / {offset}.0;",
        f"{indent}double {double_mix_name} = ({double_b_name} * 1.0) - ({double_a_name} - {double_a_name});",
        f"{indent}double {double_shadow_name} = ({double_mix_name} + {double_a_name}) - {double_a_name};",
        f"{indent}volatile double {sink_name} = {double_shadow_name} + static_cast<double>({int_shadow_name} & 1);",
        f"{indent}(void){sink_name};",
    ]


def _build_unreachable_transfer_lines(indent, guard_name, return_type=None):
    entry_label = generate_barcode_name(18)
    exit_label = generate_barcode_name(18)
    lines = [
        f"{indent}if ({guard_name}) {{",
        f"{indent}    goto {entry_label};",
    ]
    if return_type == "void":
        lines.append(f"{indent}    return;")
    elif return_type is not None:
        lines.append(f"{indent}    return {return_type}{{}};")
    lines.extend(
        [
            f"{indent}{entry_label}:",
            f"{indent}    goto {exit_label};",
            f"{indent}{exit_label}:",
            f"{indent}    throw {random.randint(0x10, 0xFFFF)};",
            f"{indent}}}",
        ]
    )
    return lines


def inject_opaque_predicates(source_text):
    """Injects dead branches into function bodies without changing behavior."""
    if not config.ENABLE_OPAQUE_PREDICATE_INJECTION:
        vlog("opaque", "disabled")
        return source_text

    result = []
    last_index = 0
    scan_index = 0
    injected = 0

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

        line_start = source_text.rfind("\n", 0, brace_index) + 1
        if has_vm_skip_marker(source_text, line_start):
            scan_index = brace_index + 1
            continue
        function_end = find_matching_brace(source_text, brace_index)
        if function_end is not None:
            body_text = source_text[brace_index + 1 : function_end - 1]
            if is_performance_sensitive_function(stripped_header, body_text):
                scan_index = brace_index + 1
                continue
        return_type = _extract_flatten_return_type(prefix, name_match.group(1))
        if return_type is None and prefix.strip() == "void":
            return_type = "void"
        base_indent = re.match(r"\s*", source_text[line_start:brace_index]).group(0)
        inner_indent = base_indent + "    "
        dead_name = generate_barcode_name(18)
        noise_name = generate_barcode_name(18)
        shadow_state_name = generate_barcode_name(18)
        payload_name = generate_barcode_name(18)
        payload_value = random.randint(0x10, 0xFFFF)
        mask_value = random.randint(0x10, 0xFF)
        opaque_variants = [
            [
                "",
                f"{inner_indent}volatile int {dead_name} = 0;",
                f"{inner_indent}volatile unsigned long long {noise_name} = static_cast<unsigned long long>({payload_value}) ^ static_cast<unsigned long long>(reinterpret_cast<unsigned long long>(&{dead_name}));",
                f"{inner_indent}int {shadow_state_name} = static_cast<int>({noise_name} & {mask_value});",
                f"{inner_indent}if ((sizeof(int) == 0) && (({shadow_state_name} & {mask_value}) == {mask_value})) {{",
                f"{inner_indent}    {dead_name} ^= {shadow_state_name};",
                f"{inner_indent}}}",
                f"{inner_indent}if ((({dead_name} ^ {dead_name}) + {dead_name}) != 0) {{",
                f"{inner_indent}    int {payload_name} = {payload_value};",
                f"{inner_indent}    {payload_name} ^= {payload_value};",
                * _build_unreachable_transfer_lines(f"{inner_indent}    ", dead_name, return_type),
                f"{inner_indent}}}",
            ],
            [
                "",
                f"{inner_indent}volatile int {dead_name} = 0;",
                f"{inner_indent}unsigned long long {noise_name} = (static_cast<unsigned long long>({payload_value}) + static_cast<unsigned long long>(reinterpret_cast<unsigned long long>(&{dead_name}))) ^ {mask_value}ULL;",
                f"{inner_indent}int {shadow_state_name} = static_cast<int>(({noise_name} ^ {noise_name}) & {mask_value});",
                f"{inner_indent}if (((sizeof(long long) ^ sizeof(long long)) != 0) && ({shadow_state_name} == {mask_value})) {{",
                f"{inner_indent}    {dead_name} += {shadow_state_name};",
                f"{inner_indent}}}",
                f"{inner_indent}if (({dead_name} & ({mask_value} ^ {mask_value})) != 0) {{",
                f"{inner_indent}    int {payload_name} = {payload_value};",
                f"{inner_indent}    {payload_name} -= {payload_value};",
                * _build_unreachable_transfer_lines(f"{inner_indent}    ", dead_name, return_type),
                f"{inner_indent}}}",
            ],
        ]
        opaque_block = "\n".join(random.choice(opaque_variants))

        result.append(source_text[last_index : brace_index + 1])
        result.append(opaque_block)
        last_index = brace_index + 1
        scan_index = brace_index + 1
        injected += 1

    result.append(source_text[last_index:])
    out = "".join(result)
    vlog("opaque", f"injected_blocks={injected}, bytes {len(source_text)} -> {len(out)}")
    return out


def inject_dead_code_blocks(source_text):
    """Injects unreachable local dead-code blocks into function bodies."""
    if not config.ENABLE_DEAD_CODE_INJECTION:
        vlog("deadcode", "disabled")
        return source_text

    result = []
    last_index = 0
    scan_index = 0
    injected = 0

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

        line_start = source_text.rfind("\n", 0, brace_index) + 1
        if has_vm_skip_marker(source_text, line_start):
            scan_index = brace_index + 1
            continue
        function_end = find_matching_brace(source_text, brace_index)
        if function_end is not None:
            body_text = source_text[brace_index + 1 : function_end - 1]
            if is_performance_sensitive_function(stripped_header, body_text):
                scan_index = brace_index + 1
                continue
        return_type = _extract_flatten_return_type(prefix, name_match.group(1))
        if return_type is None and prefix.strip() == "void":
            return_type = "void"
        base_indent = re.match(r"\s*", source_text[line_start:brace_index]).group(0)
        inner_indent = base_indent + "    "
        guard_name = generate_barcode_name(18)
        noise_name = generate_barcode_name(18)
        fake_branch_name = generate_barcode_name(18)
        sink_name = generate_barcode_name(18)
        seed = random.randint(0x10, 0xFFFF)
        mask = random.randint(0x10, 0xFF)
        aggressive_noise = _build_dead_variable_noise(f"{inner_indent}    ", seed, mask)
        dead_variants = [
            [
                "",
                f"{inner_indent}volatile int {guard_name} = 0;",
                f"{inner_indent}volatile unsigned long long {noise_name} = static_cast<unsigned long long>({seed}) ^ static_cast<unsigned long long>(reinterpret_cast<unsigned long long>(&{guard_name}));",
                f"{inner_indent}int {fake_branch_name} = static_cast<int>({noise_name} & {mask});",
                f"{inner_indent}if ((sizeof(long long) == 0) && ({fake_branch_name} == {mask})) {{",
                f"{inner_indent}    {guard_name} ^= {fake_branch_name};",
                f"{inner_indent}}}",
                *aggressive_noise,
                f"{inner_indent}if ({guard_name}) {{",
                f"{inner_indent}    int {sink_name} = {seed};",
                f"{inner_indent}    while ({sink_name} > 1) {{",
                f"{inner_indent}        {sink_name} = ({sink_name} ^ {seed}) + 1;",
                f"{inner_indent}        if ({sink_name} == {seed}) {{",
                f"{inner_indent}            break;",
                f"{inner_indent}        }}",
                f"{inner_indent}    }}",
                * _build_unreachable_transfer_lines(f"{inner_indent}    ", guard_name, return_type),
                f"{inner_indent}}}",
            ],
            [
                "",
                f"{inner_indent}volatile int {guard_name} = 0;",
                f"{inner_indent}unsigned long long {noise_name} = static_cast<unsigned long long>({seed}) + static_cast<unsigned long long>(reinterpret_cast<unsigned long long>(&{guard_name}));",
                f"{inner_indent}int {fake_branch_name} = static_cast<int>(({noise_name} ^ {noise_name}) | ({mask} & 0));",
                *aggressive_noise,
                f"{inner_indent}if ({guard_name} != 0) {{",
                f"{inner_indent}    int {sink_name} = {seed};",
                f"{inner_indent}    do {{",
                f"{inner_indent}        {sink_name} ^= {mask};",
                f"{inner_indent}        {sink_name} -= {mask};",
                f"{inner_indent}    }} while ({sink_name} < 0);",
                * _build_unreachable_transfer_lines(f"{inner_indent}    ", guard_name, return_type),
                f"{inner_indent}}}",
            ],
        ]
        dead_block = "\n".join(random.choice(dead_variants))

        result.append(source_text[last_index : brace_index + 1])
        result.append(dead_block)
        last_index = brace_index + 1
        scan_index = brace_index + 1
        injected += 1

    result.append(source_text[last_index:])
    out = "".join(result)
    vlog("deadcode", f"injected_blocks={injected}, bytes {len(source_text)} -> {len(out)}")
    return out
