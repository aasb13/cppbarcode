import random
import re

import config
from util import CHERRY_SKIP_MARKER, VM_SKIP_MARKER, iter_function_definitions, vlog


def _brace_depth_at(source_text, end_offset):
    depth = 0
    index = 0
    in_string = False
    in_char = False
    in_line_comment = False
    in_block_comment = False
    while index < end_offset:
        char = source_text[index]
        next_char = source_text[index + 1] if index + 1 < end_offset else ""
        if in_line_comment:
            if char == "\n":
                in_line_comment = False
            index += 1
            continue
        if in_block_comment:
            if char == "*" and next_char == "/":
                in_block_comment = False
                index += 2
            else:
                index += 1
            continue
        if in_string:
            if char == '"' and source_text[index - 1] != "\\":
                in_string = False
            index += 1
            continue
        if in_char:
            if char == "'" and source_text[index - 1] != "\\":
                in_char = False
            index += 1
            continue
        if char == "/" and next_char == "/":
            in_line_comment = True
            index += 2
            continue
        if char == "/" and next_char == "*":
            in_block_comment = True
            index += 2
            continue
        if char == '"':
            in_string = True
            index += 1
            continue
        if char == "'":
            in_char = True
            index += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        index += 1
    return depth


def _segment_start_with_markers(source_text, line_start):
    marker_start = line_start
    while marker_start > 0:
        prev_line_end = marker_start - 1
        prev_line_start = source_text.rfind("\n", 0, prev_line_end) + 1
        prev_line = source_text[prev_line_start:prev_line_end].strip()
        if prev_line in {VM_SKIP_MARKER, CHERRY_SKIP_MARKER}:
            marker_start = prev_line_start
            continue
        break
    return marker_start


def _signature_line_start(source_text, function_name, brace_index):
    name_index = source_text.rfind(function_name, 0, brace_index)
    if name_index == -1:
        return source_text.rfind("\n", 0, brace_index) + 1
    return source_text.rfind("\n", 0, name_index) + 1


def _build_forward_declaration(signature_text):
    stripped = signature_text.strip()
    if not stripped or "\n#" in stripped:
        return None
    if re.search(r"\)\s*(?:const|noexcept|->|\[\[|requires)", stripped):
        return None
    return stripped + ";"


def apply_function_reordering(source_text):
    if not config.ENABLE_FUNCTION_REORDERING:
        vlog("fn_reorder", "disabled")
        return source_text

    function_records = []
    for function_info in iter_function_definitions(source_text):
        if function_info.get("skip_structural"):
            continue
        brace_index = function_info["brace_index"]
        line_start = _signature_line_start(source_text, function_info["name"], brace_index)
        if _brace_depth_at(source_text, line_start) != 0:
            continue
        segment_start = _segment_start_with_markers(source_text, line_start)
        signature_text = f"{function_info['prefix']}({function_info['params_text']}"
        if not signature_text or function_info["name"] not in signature_text:
            continue
        forward_decl = _build_forward_declaration(signature_text)
        if forward_decl is None:
            continue
        function_records.append(
            {
                "name": function_info["name"],
                "start": segment_start,
                "end": function_info["end_index"],
                "definition": source_text[segment_start:function_info["end_index"]].rstrip(),
                "prototype": forward_decl,
            }
        )

    if len(function_records) < 2:
        vlog("fn_reorder", "no eligible functions; no-op")
        return source_text

    first_start = min(record["start"] for record in function_records)
    last_end = max(record["end"] for record in function_records)
    function_by_start = {record["start"]: record for record in function_records}

    ordered_defs = [record for record in function_records if record["name"] != "main"]
    main_defs = [record for record in function_records if record["name"] == "main"]
    random.shuffle(ordered_defs)
    shuffled_records = ordered_defs + main_defs

    prototype_order = function_records[:]
    random.shuffle(prototype_order)
    prototypes = []
    seen_prototypes = set()
    for record in prototype_order:
        if record["prototype"] in seen_prototypes:
            continue
        seen_prototypes.add(record["prototype"])
        prototypes.append(record["prototype"])

    prefix = source_text[:first_start]
    suffix = source_text[last_end:]
    gap_cursor = first_start
    gap_chunks = []
    for start in sorted(function_by_start):
        if gap_cursor < start:
            gap_chunks.append(source_text[gap_cursor:start])
        gap_cursor = function_by_start[start]["end"]
    if gap_cursor < last_end:
        gap_chunks.append(source_text[gap_cursor:last_end])
    gap_text = "".join(chunk for chunk in gap_chunks if chunk.strip())

    body_parts = ["\n".join(prototypes), ""]
    for index, record in enumerate(shuffled_records):
        body_parts.append(record["definition"])
        if index + 1 < len(shuffled_records):
            body_parts.append("")
    if gap_text:
        body_parts.extend(["", gap_text.strip("\n")])

    reordered_block = "\n\n".join(part for part in body_parts if part is not None)
    out = prefix + reordered_block + suffix
    vlog(
        "fn_reorder",
        f"reordered_functions={len(function_records)}, bytes {len(source_text)} -> {len(out)}",
    )
    return out
