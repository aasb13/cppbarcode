import random
import re

import config
from util import generate_barcode_name, has_vm_skip_marker
from util import vlog


def inject_opaque_predicates(source_text: str) -> str:
    """Injects dead branches into function bodies without changing behavior."""
    if not config.ENABLE_OPAQUE_PREDICATE_INJECTION:
        vlog("opaque", "disabled")
        return source_text

    result: list[str] = []
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
        base_indent = re.match(r"\s*", source_text[line_start:brace_index]).group(0)
        inner_indent = base_indent + "    "
        dead_name = generate_barcode_name(18)
        noise_name = generate_barcode_name(18)
        shadow_state_name = generate_barcode_name(18)
        payload_name = generate_barcode_name(18)
        payload_value = random.randint(0x10, 0xFFFF)
        mask_value = random.randint(0x10, 0xFF)
        opaque_block = "\n".join(
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
                f"{inner_indent}}}",
            ]
        )

        result.append(source_text[last_index : brace_index + 1])
        result.append(opaque_block)
        last_index = brace_index + 1
        scan_index = brace_index + 1
        injected += 1

    result.append(source_text[last_index:])
    out = "".join(result)
    vlog("opaque", f"injected_blocks={injected}, bytes {len(source_text)} -> {len(out)}")
    return out
