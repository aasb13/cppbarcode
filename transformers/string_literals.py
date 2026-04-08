import ast
import base64
import random

import config
import state
from util import generate_barcode_name
from util import vlog


def encode_string_literal(value_str: str) -> dict | None:
    if not value_str.startswith('"') or not value_str.endswith('"'):
        return None

    try:
        decoded = ast.literal_eval(value_str)
    except (SyntaxError, ValueError):
        return None

    if not isinstance(decoded, str):
        return None

    raw_bytes = decoded.encode("utf-8")
    key_a = random.randint(1, 255)
    key_b = random.randint(1, 255)
    xor_stage = bytes(byte ^ key_a for byte in raw_bytes)
    base64_stage = base64.b64encode(xor_stage)
    layered = bytes(byte ^ key_b for byte in base64_stage)
    encoded_literal = '"' + "".join(f"\\x{byte:02x}" for byte in layered) + '"'
    return {
        "encoded_literal": encoded_literal,
        "key_a": key_a,
        "key_b": key_b,
        "output_size": len(raw_bytes),
    }


def build_string_literal_replacement(value_str: str) -> str | None:
    if not config.ENABLE_STRING_LITERAL_ENCRYPTION:
        return None

    encoded = encode_string_literal(value_str)
    if encoded is None:
        return None

    state.init_string_literal_names()
    lambda_name = generate_barcode_name(18)
    vlog("strings", f"encoded_literal out_size={encoded['output_size']} key_a={encoded['key_a']} key_b={encoded['key_b']}")
    return (
        f"([]() -> const char* {{ "
        f"static constexpr auto {lambda_name} = {state.STRING_DECODE_HELPER_NAME}<{encoded['output_size']}>"
        f"({encoded['encoded_literal']}, {encoded['key_a']}, {encoded['key_b']}); "
        f"return {lambda_name}.data(); "
        f"}}())"
    )


def inject_string_literal_helpers(source_text: str) -> str:
    """Injects constexpr and fake decoders for string literal encryption."""
    if not config.ENABLE_STRING_LITERAL_ENCRYPTION:
        vlog("strings", "disabled")
        return source_text

    # This helper only makes sense if at least one literal was rewritten.
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
    vlog(
        "strings",
        f"injected decoder={state.STRING_DECODE_HELPER_NAME} fakes={state.STRING_FAKE_DECODER_NAMES}, bytes {len(source_text)} -> {len(out)}",
    )
    return out
