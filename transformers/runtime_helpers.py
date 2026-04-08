import random

import config
import state
from util import generate_barcode_name
from util import vlog


def get_runtime_constant_mutation(value_str: str) -> str:
    """Wrap constant mutations in a runtime-dependent helper to resist optimizer folding."""
    state.init_runtime_obfuscation_names()
    return f"{state.RUNTIME_OBF_HELPER_NAME}({value_str})"


def inject_runtime_obfuscation_helpers(source_text: str) -> str:
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
    ]
    helper_block = random.choice(helper_variants)
    variant_index = 0 if helper_block == helper_variants[0] else 1

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

    if insert_line_index == 0:
        out = helper_block + source_text
        vlog("runtime", f"injected variant={variant_index} at BOF, bytes {len(source_text)} -> {len(out)}")
        return out

    out = "".join(lines[:insert_line_index]) + helper_block + "".join(lines[insert_line_index:])
    vlog("runtime", f"injected variant={variant_index} after prologue, bytes {len(source_text)} -> {len(out)}")
    return out
