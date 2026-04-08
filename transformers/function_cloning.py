import random
import re

import config
import state
from util import (
    extract_declared_local_name,
    extract_parameter_name,
    generate_barcode_name,
    iter_function_definitions,
    looks_like_declaration,
    replace_identifier_text,
    split_parameter_list,
    split_top_level_statements,
)
from util import vlog


def build_cloned_body_variant(body_text: str, variant_index: int) -> str:
    cloned = body_text
    local_renames: dict[str, str] = {}
    for statement in split_top_level_statements(body_text):
        if not looks_like_declaration(statement):
            continue
        local_name = extract_declared_local_name(statement)
        if local_name and local_name not in local_renames:
            local_renames[local_name] = generate_barcode_name(16)

    for original_name, obfuscated_name in local_renames.items():
        cloned = replace_identifier_text(cloned, original_name, obfuscated_name)

    if variant_index % 2 == 1:
        cloned = re.sub(r"\breturn\s+([^;]+);", lambda match: f"return (({match.group(1).strip()}));", cloned)

    statements = split_top_level_statements(cloned)
    first_executable = None
    for index, statement in enumerate(statements):
        if not looks_like_declaration(statement):
            first_executable = index
            break
    if first_executable is not None:
        decls = statements[:first_executable]
        execs = statements[first_executable:]
        if len(decls) > 1 and all(execs):
            random.shuffle(decls)
            cloned = "\n".join(decls + execs)

    return cloned


def build_clone_dispatcher(
    function_name: str,
    signature_text: str,
    params_text: str,
    clone_names: list[str],
    base_indent: str,
) -> str | None:
    # The dispatcher depends on CFG clone selector helper; names must exist even if CFG pollution is disabled.
    state.init_cfg_pollution_names()

    param_parts = split_parameter_list(params_text)
    param_names = [name for name in (extract_parameter_name(part) for part in param_parts) if name]
    if len(param_names) != len([part for part in param_parts if part not in {"void", "..."}]):
        return None

    inner_indent = base_indent + "    "
    args_expr = ", ".join(param_names)
    selector_name = generate_barcode_name(18)
    salt_value = random.randint(0x100, 0xFFFF)

    lines = [
        f"{signature_text} {{",
        f"{inner_indent}int {selector_name} = {state.CFG_CLONE_SELECT_HELPER_NAME}({len(clone_names)}, {salt_value}ULL);",
    ]
    for index, clone_name in enumerate(clone_names[:-1]):
        call_expr = f"{clone_name}({args_expr})"
        lines.append(f"{inner_indent}if ({selector_name} == {index}) {{")
        lines.append(f"{inner_indent}    return {call_expr};")
        lines.append(f"{inner_indent}}}")
    lines.append(f"{inner_indent}return {clone_names[-1]}({args_expr});")
    lines.append(f"{base_indent}}}")
    return "\n".join(lines)


def apply_function_cloning(source_text: str) -> str:
    """Clones eligible free functions and routes calls through an opaque dispatcher."""
    if not config.ENABLE_FUNCTION_CLONING:
        vlog("cloning", "disabled")
        return source_text

    replacements: list[tuple[int, int, str]] = []
    cloned_functions = 0
    emitted_clones = 0
    for function_info in iter_function_definitions(source_text):
        if function_info.get("skip_structural"):
            continue
        function_name = function_info["name"]
        if function_name == "main":
            continue
        if function_info["prefix"].endswith("operator"):
            continue

        brace_index = function_info["brace_index"]
        line_start = source_text.rfind("\n", 0, brace_index) + 1
        signature_text = source_text[line_start:brace_index].rstrip()
        if not signature_text or "\n#" in signature_text or function_name not in signature_text:
            continue
        if re.search(r"\)\s*(?:const|noexcept|->|\[\[|requires)", signature_text):
            continue
        if re.search(rf"\b{re.escape(function_name)}\s*\(", function_info["body_text"]):
            continue

        params_text = function_info["params_text"][:-1]
        param_parts = split_parameter_list(params_text)
        if any(part == "..." for part in param_parts):
            continue

        clone_count = 2
        clone_names = [generate_barcode_name(18) for _ in range(clone_count)]
        clone_variants: list[str] = []
        for variant_index, clone_name in enumerate(clone_names):
            variant_body = build_cloned_body_variant(function_info["body_text"], variant_index)
            clone_signature = re.sub(
                rf"\b{re.escape(function_name)}\b(?=\s*\()",
                clone_name,
                signature_text,
                count=1,
            )
            clone_variants.append(
                f"__attribute__((unused, noinline, noipa)) {clone_signature}\n{function_info['base_indent']}{{{variant_body}\n{function_info['base_indent']}}}"
            )
            emitted_clones += 1

        dispatcher = build_clone_dispatcher(
            function_name,
            signature_text,
            params_text,
            clone_names,
            function_info["base_indent"],
        )
        if dispatcher is None:
            continue

        replacement = "\n\n".join(clone_variants + [dispatcher])
        replacements.append((line_start, function_info["end_index"], replacement))
        cloned_functions += 1

    if not replacements:
        vlog("cloning", "no eligible functions; no-op")
        return source_text

    content = source_text
    for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
        content = content[:start] + replacement + content[end:]
    vlog(
        "cloning",
        f"cloned_functions={cloned_functions}, emitted_clones={emitted_clones}, bytes {len(source_text)} -> {len(content)}",
    )
    return content
