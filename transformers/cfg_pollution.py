import config
import state
from util import generate_barcode_name
from util import vlog


def inject_cfg_pollution_helpers(source_text: str) -> str:
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
    helper_block = "\n".join(
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
    )

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
    out = "".join(lines[:insert_line_index]) + helper_block + "".join(lines[insert_line_index:])
    vlog("cfg", f"injected helpers edge={state.CFG_EDGE_HELPER_NAME} clone_sel={state.CFG_CLONE_SELECT_HELPER_NAME}, bytes {len(source_text)} -> {len(out)}")
    return out
