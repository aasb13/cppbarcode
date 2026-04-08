import random

import config
from transformers.structural import _extract_flatten_return_type
from util import (
    generate_barcode_name,
    indent_block,
    iter_function_definitions,
    looks_like_declaration,
    vlog,
    split_top_level_statements,
)


def is_supported_for_goto_obfuscation(body_text):
    unsupported_markers = ("goto ", "goto\t", "try", "catch", " co_", "case ", "default:")
    return not any(marker in body_text for marker in unsupported_markers)


def build_goto_obfuscated_body(body_text, base_indent, return_type=None):
    statements = split_top_level_statements(body_text)
    if len(statements) < 2:
        return None

    first_executable = None
    for index, statement in enumerate(statements):
        if not looks_like_declaration(statement):
            first_executable = index
            break

    if first_executable is None:
        return None

    declarations = statements[:first_executable]
    executable = statements[first_executable:]
    if len(executable) < 2:
        return None
    if any(looks_like_declaration(statement) for statement in executable):
        return None
    if not is_supported_for_goto_obfuscation(body_text):
        return None

    indent = base_indent + "    "
    entry_label = generate_barcode_name(18)
    exit_label = generate_barcode_name(18)
    real_labels = [generate_barcode_name(18) for _ in executable]
    fake_labels = [generate_barcode_name(18) for _ in executable]
    emitted_blocks = []

    for declaration in declarations:
        emitted_blocks.extend(indent_block(declaration, indent))

    emitted_blocks.append(f"{indent}goto {entry_label};")

    block_order = []
    for index in range(len(executable)):
        block_order.append(("real", index))
        block_order.append(("fake", index))
    random.shuffle(block_order)
    entry_insert_index = random.randrange(len(block_order) + 1)
    block_order.insert(entry_insert_index, ("entry", None))

    for kind, index in block_order:
        if kind == "entry":
            first_fake = fake_labels[0]
            emitted_blocks.append(f"{indent}{entry_label}:")
            emitted_blocks.append(f"{indent}{{")
            route_name = generate_barcode_name(18)
            emitted_blocks.append(
                f"{indent}    int {route_name} = (({random.randint(0x10, 0xFFFF)} ^ {random.randint(0x10, 0xFFFF)}) & 0);"
            )
            emitted_blocks.append(f"{indent}    if ({route_name} != 0) goto {first_fake};")
            emitted_blocks.append(f"{indent}    goto {real_labels[0]};")
            emitted_blocks.append(f"{indent}}}")
            continue

        if kind == "fake":
            emitted_blocks.append(f"{indent}{fake_labels[index]}:")
            emitted_blocks.append(f"{indent}{{")
            guard_name = generate_barcode_name(18)
            emitted_blocks.append(f"{indent}    volatile int {guard_name} = {random.randint(0x10, 0xFF)};")
            emitted_blocks.append(f"{indent}    (void){guard_name};")
            emitted_blocks.append(f"{indent}    goto {real_labels[index]};")
            emitted_blocks.append(f"{indent}}}")
            continue

        statement = executable[index]
        emitted_blocks.append(f"{indent}{real_labels[index]}:")
        emitted_blocks.append(f"{indent}{{")
        emitted_blocks.extend(indent_block(statement, f"{indent}    "))
        terminal = statement.lstrip().startswith(("return", "throw"))
        if terminal:
            emitted_blocks.append(f"{indent}}}")
            continue
        if index + 1 < len(executable):
            next_target = fake_labels[index + 1] if random.random() > 0.5 else real_labels[index + 1]
            emitted_blocks.append(f"{indent}    goto {next_target};")
        else:
            emitted_blocks.append(f"{indent}    goto {exit_label};")
        emitted_blocks.append(f"{indent}}}")

    emitted_blocks.append(f"{indent}{exit_label}:")
    emitted_blocks.append(f"{indent}{{")
    if return_type is None or return_type == "void":
        emitted_blocks.append(f"{indent}    ;")
    else:
        emitted_blocks.append(f"{indent}    return {return_type}{{}};")
    emitted_blocks.append(f"{indent}}}")

    return "{\n" + "\n".join(emitted_blocks) + f"\n{base_indent}}}"


def apply_goto_flow_obfuscation(source_text):
    if not config.ENABLE_GOTO_FLOW_OBFUSCATION:
        vlog("goto", "disabled")
        return source_text

    replacements = []
    transformed = 0
    for function_info in iter_function_definitions(source_text):
        if function_info.get("skip_structural"):
            continue
        return_type = _extract_flatten_return_type(function_info["prefix"], function_info["name"])
        if return_type is None and function_info["prefix"].strip() != "void":
            continue
        rewritten_body = build_goto_obfuscated_body(
            function_info["body_text"],
            function_info["base_indent"],
            return_type=return_type,
        )
        if rewritten_body is None:
            continue
        replacements.append((function_info["brace_index"], function_info["end_index"], rewritten_body))
        transformed += 1

    if not replacements:
        vlog("goto", "no eligible functions; no-op")
        return source_text

    content = source_text
    for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
        content = content[:start] + replacement + content[end:]
    vlog("goto", f"obfuscated_functions={transformed}, bytes {len(source_text)} -> {len(content)}")
    return content
