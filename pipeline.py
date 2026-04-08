import os
import tempfile

import clang.cindex

import config
import state
from transformers import (
    AstRewriteTransformer,
    CFGPollutionTransformer,
    ControlBodyBracingTransformer,
    ControlFlowFlatteningTransformer,
    DataFlowTransformer,
    DeadCodeBlockTransformer,
    DeadCodeHelperTransformer,
    DeadCodeRemovalTransformer,
    DefineObfuscationTransformer,
    FunctionCloningTransformer,
    FunctionPointerIndirectionTransformer,
    IncludeWrappingTransformer,
    MemoryAccessTransformer,
    OpaquePredicateTransformer,
    RuntimeHelperTransformer,
    LoopIdiomTransformer,
    StatementReorderingTransformer,
    STLWrapperTransformer,
    StringLiteralEncryptionTransformer,
    StylometricNoiseTransformer,
    TMPAdditionTransformer,
    TypeLevelObfuscationTransformer,
    VirtualMachineTransformer,
    WhitespaceDegradationTransformer,
)
from transformers.ast_rewrite import build_string_literal_replacement, collect_ast_replacements
from transformers.brace_expansion import expand_inline_control_bodies
from transformers.define_obfuscation import apply_define_obfuscation
from transformers.dead_code_removal import remove_dead_code
from transformers.formatting import apply_stylometric_noise
from transformers.helper_injectors import (
    inject_cfg_pollution_helpers,
    inject_data_flow_helpers,
    inject_dead_code_helpers,
    inject_function_pointer_helpers,
    inject_memory_access_helpers,
    inject_runtime_obfuscation_helpers,
    inject_stl_wrappers,
    inject_string_literal_helpers,
    inject_tmp_addition_helpers,
    inject_type_level_helpers,
)
from transformers.include_wrapping import wrap_includes_with_preprocessor_logic
from transformers.opaque_dead_code import inject_dead_code_blocks, inject_opaque_predicates
from transformers.runtime import runtime_wrap_constant
from transformers.structural import (
    apply_control_flow_flattening,
    apply_function_cloning,
    apply_loop_idiom_transformation,
    apply_statement_reordering,
)
from transformers.vm_integration import (
    collect_virtual_machine_replacements,
    inject_virtual_machine_runtime,
)
from transformers.vm_expression_wrappers import apply_vm_expression_wrappers
from transformers.whitespace_degradation import degrade_whitespace_formatting
from util import (
    build_name_map,
    build_protected_range_index,
    get_constant_mutation,
    is_in_protected_range,
    normalize_path,
    parse_integer_literal,
    strip_comments,
)

# Path configuration for Clang library (Adjust based on your OS)
if os.path.exists("/usr/lib/libclang.so"):
    clang.cindex.Config.set_library_path("/usr/lib")


INCLUDE_WRAPPING_TRANSFORMER = IncludeWrappingTransformer(wrap_includes_with_preprocessor_logic)
DEFINE_OBFUSCATION_TRANSFORMER = DefineObfuscationTransformer(apply_define_obfuscation)
FUNCTION_CLONING_TRANSFORMER = FunctionCloningTransformer(apply_function_cloning)
STATEMENT_REORDERING_TRANSFORMER = StatementReorderingTransformer(apply_statement_reordering)
LOOP_IDIOM_TRANSFORMER = LoopIdiomTransformer(apply_loop_idiom_transformation)
CONTROL_FLOW_FLATTENING_TRANSFORMER = ControlFlowFlatteningTransformer(apply_control_flow_flattening)
CFG_POLLUTION_TRANSFORMER = CFGPollutionTransformer(inject_cfg_pollution_helpers)
OPAQUE_PREDICATE_TRANSFORMER = OpaquePredicateTransformer(inject_opaque_predicates)
DEAD_CODE_BLOCK_TRANSFORMER = DeadCodeBlockTransformer(inject_dead_code_blocks)
DEAD_CODE_REMOVAL_TRANSFORMER = DeadCodeRemovalTransformer(remove_dead_code)
AST_REWRITE_TRANSFORMER = AstRewriteTransformer(collect_ast_replacements)
RUNTIME_HELPER_TRANSFORMER = RuntimeHelperTransformer(inject_runtime_obfuscation_helpers)
CONTROL_BODY_BRACING_TRANSFORMER = ControlBodyBracingTransformer(expand_inline_control_bodies)
MEMORY_ACCESS_TRANSFORMER = MemoryAccessTransformer(inject_memory_access_helpers)
STL_WRAPPER_TRANSFORMER = STLWrapperTransformer(inject_stl_wrappers)
DEAD_CODE_HELPER_TRANSFORMER = DeadCodeHelperTransformer(inject_dead_code_helpers)
TMP_ADDITION_TRANSFORMER = TMPAdditionTransformer(inject_tmp_addition_helpers)
FUNCTION_POINTER_TRANSFORMER = FunctionPointerIndirectionTransformer(inject_function_pointer_helpers)
DATA_FLOW_TRANSFORMER = DataFlowTransformer(inject_data_flow_helpers)
STRING_LITERAL_TRANSFORMER = StringLiteralEncryptionTransformer(inject_string_literal_helpers)
TYPE_LEVEL_TRANSFORMER = TypeLevelObfuscationTransformer(inject_type_level_helpers)
STYLOMETRIC_NOISE_TRANSFORMER = StylometricNoiseTransformer(apply_stylometric_noise)
WHITESPACE_DEGRADATION_TRANSFORMER = WhitespaceDegradationTransformer(degrade_whitespace_formatting)
VIRTUAL_MACHINE_TRANSFORMER = VirtualMachineTransformer(
    collect_virtual_machine_replacements,
    inject_virtual_machine_runtime,
)


def obfuscate_file(target_file):
    state.VM_RUNTIME_REQUIRED = False
    state.VM_EXPRESSION_WRAPPER_NAMES = set()
    with open(target_file, "r", encoding="utf-8") as f:
        original_content = f.read()

    working_content = strip_comments(original_content)
    working_content = DEAD_CODE_REMOVAL_TRANSFORMER.apply(working_content)
    working_content = CONTROL_BODY_BRACING_TRANSFORMER.apply(working_content)

    def write_parse_target(source_text, existing_temp_path=None):
        parse_target_path = target_file
        temp_path_local = existing_temp_path
        if source_text != original_content:
            if temp_path_local is None:
                temp_file = tempfile.NamedTemporaryFile(
                    "w",
                    suffix=os.path.splitext(target_file)[1],
                    delete=False,
                    encoding="utf-8",
                )
                temp_path_local = temp_file.name
                temp_file.close()
            with open(temp_path_local, "w", encoding="utf-8") as temp_file:
                temp_file.write(source_text)
            parse_target_path = temp_path_local
        return parse_target_path, temp_path_local

    temp_path = None
    parse_target, temp_path = write_parse_target(working_content, temp_path)

    index = clang.cindex.Index.create()
    tu = index.parse(parse_target, args=["-std=c++17"])
    target_realpath = normalize_path(parse_target)
    wrapped_content = apply_vm_expression_wrappers(tu.cursor, target_realpath, working_content)
    if wrapped_content != working_content:
        working_content = wrapped_content
        parse_target, temp_path = write_parse_target(working_content, temp_path)
        tu = index.parse(parse_target, args=["-std=c++17"])
        target_realpath = normalize_path(parse_target)

    name_map = {}
    if config.ENABLE_IDENTIFIER_OBFUSCATION:
        print(f"--- Harvesting local symbols from {target_file} ---")
        build_name_map(tu.cursor, target_realpath, name_map)

    if config.ENABLE_IDENTIFIER_OBFUSCATION and not name_map:
        print("No local symbols found. Check file path or content!")
        return

    tokens = list(tu.get_tokens(extent=tu.cursor.extent))
    replacements = []
    VIRTUAL_MACHINE_TRANSFORMER.collect(
        tu.cursor, target_realpath, name_map, working_content, replacements
    )
    vm_active = state.VM_RUNTIME_REQUIRED
    if (
        config.ENABLE_CONTAINER_ACCESSOR_REWRITES
        or config.ENABLE_STL_WRAPPER_REWRITES
        or config.ENABLE_TMP_ADDITION_OBFUSCATION
        or config.ENABLE_FUNCTION_POINTER_INDIRECTION
        or config.ENABLE_DATA_FLOW_OBFUSCATION
        or config.ENABLE_MEMORY_ACCESS_OBFUSCATION
    ):
        original_tmp_flag = config.ENABLE_TMP_ADDITION_OBFUSCATION
        original_data_flow_flag = config.ENABLE_DATA_FLOW_OBFUSCATION
        if vm_active or config.ENABLE_TYPE_LEVEL_OBFUSCATION:
            config.ENABLE_TMP_ADDITION_OBFUSCATION = False
        if vm_active:
            config.ENABLE_DATA_FLOW_OBFUSCATION = False
        try:
            AST_REWRITE_TRANSFORMER.collect(
                tu.cursor, target_realpath, name_map, working_content, replacements
            )
        finally:
            config.ENABLE_TMP_ADDITION_OBFUSCATION = original_tmp_flag
            config.ENABLE_DATA_FLOW_OBFUSCATION = original_data_flow_flag
    protected_ranges, protected_range_starts = build_protected_range_index(
        [(start, end) for start, end, _ in replacements]
    )

    print("--- Performing global token mutation ---")
    for token in tokens:
        if not token.location.file or normalize_path(token.location.file.name) != target_realpath:
            continue
        token_start = token.extent.start.offset
        token_end = token.extent.end.offset
        if is_in_protected_range(
            token_start, token_end, protected_ranges, protected_range_starts
        ):
            continue

        if config.ENABLE_IDENTIFIER_OBFUSCATION and token.spelling in name_map:
            replacements.append((token_start, token_end, name_map[token.spelling]))

        elif (
            config.ENABLE_CONSTANT_MUTATION
            and token.kind == clang.cindex.TokenKind.LITERAL
            and parse_integer_literal(token.spelling) is not None
        ):
            replacements.append(
                (
                    token_start,
                    token_end,
                    get_constant_mutation(
                        token.spelling, runtime_wrapper=runtime_wrap_constant
                    ),
                )
            )
        elif (
            config.ENABLE_STRING_LITERAL_ENCRYPTION
            and token.kind == clang.cindex.TokenKind.LITERAL
            and token.spelling.startswith('"')
        ):
            replacement = build_string_literal_replacement(token.spelling)
            if replacement is not None:
                replacements.append((token_start, token_end, replacement))

        elif config.ENABLE_BOOLEAN_OBFUSCATION and token.spelling == "true":
            replacements.append((token_start, token_end, "(0x7 > 0x1)"))
        elif config.ENABLE_BOOLEAN_OBFUSCATION and token.spelling == "false":
            replacements.append((token_start, token_end, "(0x7 < 0x1)"))

    unique_repls = sorted(list(set(replacements)), key=lambda x: x[0], reverse=True)

    content = list(working_content)
    for start, end, new_val in unique_repls:
        content[start:end] = list(new_val)

    final_content = "".join(content)
    final_content = LOOP_IDIOM_TRANSFORMER.apply(final_content)
    final_content = STATEMENT_REORDERING_TRANSFORMER.apply(final_content)
    final_content = FUNCTION_CLONING_TRANSFORMER.apply(final_content)
    final_content = CONTROL_FLOW_FLATTENING_TRANSFORMER.apply(final_content)
    final_content = OPAQUE_PREDICATE_TRANSFORMER.apply(final_content)
    final_content = DEAD_CODE_BLOCK_TRANSFORMER.apply(final_content)
    if config.ENABLE_INCLUDE_WRAPPING:
        final_content = INCLUDE_WRAPPING_TRANSFORMER.apply(final_content)
    final_content = RUNTIME_HELPER_TRANSFORMER.apply(final_content)
    final_content = CFG_POLLUTION_TRANSFORMER.apply(final_content)
    final_content = MEMORY_ACCESS_TRANSFORMER.apply(final_content)
    final_content = STL_WRAPPER_TRANSFORMER.apply(final_content)
    final_content = DEAD_CODE_HELPER_TRANSFORMER.apply(final_content)
    if not vm_active:
        final_content = TMP_ADDITION_TRANSFORMER.apply(final_content)
    final_content = FUNCTION_POINTER_TRANSFORMER.apply(final_content)
    if not vm_active:
        final_content = DATA_FLOW_TRANSFORMER.apply(final_content)
    final_content = STRING_LITERAL_TRANSFORMER.apply(final_content)
    final_content = TYPE_LEVEL_TRANSFORMER.apply(final_content)
    final_content = VIRTUAL_MACHINE_TRANSFORMER.inject(final_content)
    formatting_safe = not state.VM_RUNTIME_REQUIRED
    if config.ENABLE_STYLOMETRIC_NOISE and formatting_safe:
        final_content = STYLOMETRIC_NOISE_TRANSFORMER.apply(final_content)
    if formatting_safe:
        final_content = DEFINE_OBFUSCATION_TRANSFORMER.apply(final_content)
    if config.ENABLE_WHITESPACE_DEGRADATION:
        final_content = WHITESPACE_DEGRADATION_TRANSFORMER.apply(final_content)
    final_content = strip_comments(final_content)

    output_name = config.OUTPUT_PREFIX + target_file
    with open(output_name, "w", encoding="utf-8") as f:
        f.write(final_content)

    if temp_path is not None:
        os.unlink(temp_path)

    print(f"Success! Obfuscated code saved to: {output_name}")
    print(f"Symbols renamed: {len(name_map)}")
