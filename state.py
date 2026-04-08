import random

from util import generate_barcode_name


RUNTIME_OBF_STATE_NAME = None
RUNTIME_OBF_HELPER_NAME = None
CFG_EDGE_HELPER_NAME = None
CFG_CLONE_SELECT_HELPER_NAME = None
STL_SORT_HELPER_NAME = None
DEAD_CODE_HELPER_NAMES = []
TMP_ADD_STRUCT_NAME = None
TMP_ADD_HELPER_NAME = None
TMP_ADD_VARIANT = None
FUNCTION_PTR_STAGE_HELPER_NAME = None
FUNCTION_PTR_INVOKE_HELPER_NAME = None
MEMORY_INDEX_HELPER_NAME = None
MEMORY_PTR_ADVANCE_HELPER_NAME = None
MEMORY_MEMBER_HELPER_NAME = None
DATA_FLOW_STRUCT_NAME = None
DATA_FLOW_PACK_HELPER_NAME = None
DATA_FLOW_UNPACK_HELPER_NAME = None
DATA_FLOW_MERGE_HELPER_NAME = None
DATA_FLOW_VARIANT = None
STRING_DECODE_HELPER_NAME = None
STRING_FAKE_DECODER_NAMES = []
OPAQUE_WRAPPER_NAME = None
OPAQUE_WRAPPER_VARIANT = None
VM_NAMESPACE_NAME = None
VM_RUNTIME_REQUIRED = False
VM_EXPRESSION_WRAPPER_NAMES = set()


def init_runtime_obfuscation_names():
    global RUNTIME_OBF_STATE_NAME, RUNTIME_OBF_HELPER_NAME
    if RUNTIME_OBF_STATE_NAME is None:
        RUNTIME_OBF_STATE_NAME = generate_barcode_name(18)
    if RUNTIME_OBF_HELPER_NAME is None:
        RUNTIME_OBF_HELPER_NAME = generate_barcode_name(18)


def init_cfg_pollution_names():
    global CFG_EDGE_HELPER_NAME, CFG_CLONE_SELECT_HELPER_NAME, RUNTIME_OBF_STATE_NAME
    if CFG_EDGE_HELPER_NAME is None:
        CFG_EDGE_HELPER_NAME = generate_barcode_name(18)
    if CFG_CLONE_SELECT_HELPER_NAME is None:
        CFG_CLONE_SELECT_HELPER_NAME = generate_barcode_name(18)
    if RUNTIME_OBF_STATE_NAME is None:
        RUNTIME_OBF_STATE_NAME = generate_barcode_name(18)


def init_stl_helper_names():
    global STL_SORT_HELPER_NAME
    if STL_SORT_HELPER_NAME is None:
        STL_SORT_HELPER_NAME = generate_barcode_name(18)


def init_function_pointer_helper_names():
    global FUNCTION_PTR_STAGE_HELPER_NAME, FUNCTION_PTR_INVOKE_HELPER_NAME
    if FUNCTION_PTR_STAGE_HELPER_NAME is None:
        FUNCTION_PTR_STAGE_HELPER_NAME = generate_barcode_name(18)
    if FUNCTION_PTR_INVOKE_HELPER_NAME is None:
        FUNCTION_PTR_INVOKE_HELPER_NAME = generate_barcode_name(18)


def init_memory_access_names():
    global MEMORY_INDEX_HELPER_NAME, MEMORY_PTR_ADVANCE_HELPER_NAME, MEMORY_MEMBER_HELPER_NAME
    if MEMORY_INDEX_HELPER_NAME is None:
        MEMORY_INDEX_HELPER_NAME = generate_barcode_name(18)
    if MEMORY_PTR_ADVANCE_HELPER_NAME is None:
        MEMORY_PTR_ADVANCE_HELPER_NAME = generate_barcode_name(18)
    if MEMORY_MEMBER_HELPER_NAME is None:
        MEMORY_MEMBER_HELPER_NAME = generate_barcode_name(18)


def init_data_flow_names():
    global DATA_FLOW_STRUCT_NAME, DATA_FLOW_PACK_HELPER_NAME
    global DATA_FLOW_UNPACK_HELPER_NAME, DATA_FLOW_MERGE_HELPER_NAME, DATA_FLOW_VARIANT
    if DATA_FLOW_STRUCT_NAME is None:
        DATA_FLOW_STRUCT_NAME = generate_barcode_name(18)
    if DATA_FLOW_PACK_HELPER_NAME is None:
        DATA_FLOW_PACK_HELPER_NAME = generate_barcode_name(18)
    if DATA_FLOW_UNPACK_HELPER_NAME is None:
        DATA_FLOW_UNPACK_HELPER_NAME = generate_barcode_name(18)
    if DATA_FLOW_MERGE_HELPER_NAME is None:
        DATA_FLOW_MERGE_HELPER_NAME = generate_barcode_name(18)
    if DATA_FLOW_VARIANT is None:
        DATA_FLOW_VARIANT = {
            "share_mode": random.choice(("sum", "delta")),
            "mask_a": random.randint(0x10, 0xFFFF),
            "mask_b": random.randint(0x10, 0xFFFF),
            "merge_mode": random.choice(("direct", "restaged")),
        }


def init_string_literal_names():
    global STRING_DECODE_HELPER_NAME, STRING_FAKE_DECODER_NAMES
    if STRING_DECODE_HELPER_NAME is None:
        STRING_DECODE_HELPER_NAME = generate_barcode_name(18)
    if not STRING_FAKE_DECODER_NAMES:
        STRING_FAKE_DECODER_NAMES = [generate_barcode_name(18), generate_barcode_name(18)]


def init_type_level_names():
    global OPAQUE_WRAPPER_NAME, OPAQUE_WRAPPER_VARIANT
    if OPAQUE_WRAPPER_NAME is None:
        OPAQUE_WRAPPER_NAME = generate_barcode_name(18)
    if OPAQUE_WRAPPER_VARIANT is None:
        OPAQUE_WRAPPER_VARIANT = {
            "key_a": random.randint(0x10, 0xFFFF),
            "key_b": random.randint(0x10, 0xFFFF),
        }


def init_dead_code_helper_names():
    global DEAD_CODE_HELPER_NAMES
    if not DEAD_CODE_HELPER_NAMES:
        DEAD_CODE_HELPER_NAMES = [generate_barcode_name(18), generate_barcode_name(18)]


def init_tmp_addition_names():
    global TMP_ADD_STRUCT_NAME, TMP_ADD_HELPER_NAME, TMP_ADD_VARIANT
    if TMP_ADD_STRUCT_NAME is None:
        TMP_ADD_STRUCT_NAME = generate_barcode_name(18)
    if TMP_ADD_HELPER_NAME is None:
        TMP_ADD_HELPER_NAME = generate_barcode_name(18)
    if TMP_ADD_VARIANT is None:
        TMP_ADD_VARIANT = {
            "depth": random.randint(4, 8),
            "split_mode": random.choice(("half", "biased_left", "biased_right")),
            "base_mode": random.choice(("plain", "commuted", "neg_sub", "double_neg")),
            "call_mode": random.choice(("direct", "swap", "lift_left", "lift_right")),
            "helper_mode": random.choice(("value_sfinae", "type_sfinae")),
        }


def init_virtual_machine_names():
    global VM_NAMESPACE_NAME
    if VM_NAMESPACE_NAME is None:
        VM_NAMESPACE_NAME = generate_barcode_name(18)
