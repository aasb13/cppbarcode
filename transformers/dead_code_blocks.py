import random
import re

import config
from util import generate_barcode_name, has_vm_skip_marker
from util import vlog


def inject_dead_code_blocks(source_text: str) -> str:
    """Injects unreachable local dead-code blocks into function bodies."""
    if not config.ENABLE_DEAD_CODE_INJECTION:
        vlog("deadcode", "disabled")
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
        guard_name = generate_barcode_name(18)
        noise_name = generate_barcode_name(18)
        fake_branch_name = generate_barcode_name(18)
        sink_name = generate_barcode_name(18)
        seed = random.randint(0x10, 0xFFFF)
        mask = random.randint(0x10, 0xFF)
        dead_block = "\n".join(
            [
                "",
                f"{inner_indent}volatile int {guard_name} = 0;",
                f"{inner_indent}volatile unsigned long long {noise_name} = static_cast<unsigned long long>({seed}) ^ static_cast<unsigned long long>(reinterpret_cast<unsigned long long>(&{guard_name}));",
                f"{inner_indent}int {fake_branch_name} = static_cast<int>({noise_name} & {mask});",
                f"{inner_indent}if ((sizeof(long long) == 0) && ({fake_branch_name} == {mask})) {{",
                f"{inner_indent}    {guard_name} ^= {fake_branch_name};",
                f"{inner_indent}}}",
                f"{inner_indent}if ({guard_name}) {{",
                f"{inner_indent}    int {sink_name} = {seed};",
                f"{inner_indent}    while ({sink_name} > 1) {{",
                f"{inner_indent}        {sink_name} = ({sink_name} ^ {seed}) + 1;",
                f"{inner_indent}        if ({sink_name} == {seed}) {{",
                f"{inner_indent}            break;",
                f"{inner_indent}        }}",
                f"{inner_indent}    }}",
                f"{inner_indent}}}",
            ]
        )

        result.append(source_text[last_index : brace_index + 1])
        result.append(dead_block)
        last_index = brace_index + 1
        scan_index = brace_index + 1
        injected += 1

    result.append(source_text[last_index:])
    out = "".join(result)
    vlog("deadcode", f"injected_blocks={injected}, bytes {len(source_text)} -> {len(out)}")
    return out
