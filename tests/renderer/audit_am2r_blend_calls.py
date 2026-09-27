#!/usr/bin/env python3
"""Read-only WAD14 blend arguments; does not extract or alter game content.

FUNC/VARI chains and CODE instruction boundaries are authoritative. This is a
deliberately small literal/direct-variable recognizer, not a decompiler or a
control-flow proof. Unknown expressions remain unknown. Output contains only
structural names, argument values, offsets and counts, never game bytecode.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from audit_am2r_function_surface import chunks, c_string


def word(blob, address):
    return struct.unpack_from("<I", blob, address)[0]


def references(blob, table, chunk):
    body, length = table[chunk]
    if length % 12:
        raise ValueError("Only flat WAD14 reference tables are supported")
    result = {}
    for offset in range(body, body + length, 12):
        name, count, address = struct.unpack_from("<III", blob, offset)
        name = c_string(blob, name)
        for index in range(count):
            if address in result:
                raise ValueError("Duplicate or cyclic reference chain")
            result[address] = name
            if index + 1 < count:
                delta = word(blob, address + 4) & 0x07FFFFFF
                if delta == 0:
                    raise ValueError("Reference chain stopped early")
                address += delta
    return result


def code_entries(blob, table):
    body, _ = table[b"CODE"]
    count = word(blob, body)
    for pointer in struct.unpack_from(f"<{count}I", blob, body + 4):
        if not pointer:
            continue
        name, size = struct.unpack_from("<II", blob, pointer)
        start, end = pointer + 8, pointer + 8 + size
        if end > len(blob):
            raise ValueError("Code range outside input")
        instructions = []
        ip = start
        while ip < end:
            instruction = word(blob, ip)
            kind, typ = instruction >> 24, (instruction >> 16) & 15
            length = 4
            if kind == 0xC0:  # WAD14 push
                length = 4 if typ == 15 else 12 if typ in (0, 3) else 8
            elif kind == 0x41:  # WAD14 pop
                length = 4 if typ == 15 else 8
            elif kind == 0xDA:  # WAD14 call
                length = 8
            elif kind == 0xFF:  # break; normally absent from this WAD version
                length = 8 if typ == 2 else 4
            instructions.append((ip, instruction))
            ip += length
        if ip != end:
            raise ValueError("Code instruction range does not end exactly")
        yield c_string(blob, name), instructions


def value_before(blob, instructions, before, variables):
    index = before - 1
    if index >= 0 and instructions[index][1] >> 24 == 0x03:
        index -= 1  # scalar conversion; do not evaluate other arithmetic
    if index < 0:
        return {"unknown": True}, before
    address, instruction = instructions[index]
    if instruction >> 24 != 0xC0:
        return {"unknown": True}, before
    typ = (instruction >> 16) & 15
    if typ == 15:
        return {"literal": struct.unpack("<h", struct.pack("<H", instruction & 65535))[0]}, index
    if typ in (0, 2, 3):
        fmt = {0: "<d", 2: "<i", 3: "<q"}[typ]
        return {"literal": struct.unpack_from(fmt, blob, address + 4)[0]}, index
    if typ == 5 and address in variables:
        return {"variable": variables[address]}, index
    return {"unknown": True}, before


def inventory(path):
    blob = path.read_bytes()
    table = chunks(blob)
    gen8, _ = table[b"GEN8"]
    if blob[gen8 + 1] != 14:
        raise ValueError("This literal audit supports WAD14 only")
    functions = references(blob, table, b"FUNC")
    variables = references(blob, table, b"VARI")
    wanted = {"draw_set_blend_mode", "draw_set_blend_mode_ext", "draw_clear_alpha"}
    calls, assignments = [], []
    for caller, instructions in code_entries(blob, table):
        for index, (address, instruction) in enumerate(instructions):
            name = functions.get(address)
            if name in wanted:
                if instruction >> 24 != 0xDA:
                    raise ValueError("Function reference is not a WAD14 call")
                cursor, arguments = index, []
                for _ in range(instruction & 65535):
                    value, cursor = value_before(blob, instructions, cursor, variables)
                    arguments.append(value)
                calls.append(dict(function=name, caller=caller,
                                  address=f"0x{address:x}", arguments=arguments))
            if variables.get(address) == "blendmode" and instruction >> 24 == 0x41:
                value, _ = value_before(blob, instructions, index, variables)
                assignments.append(dict(caller=caller, address=f"0x{address:x}", value=value))
    expected = Counter(name for name in functions.values() if name in wanted)
    actual = Counter(call["function"] for call in calls)
    if actual != expected:
        raise ValueError("Not all FUNC references matched a valid CODE instruction")
    named = Counter(json.dumps(call["arguments"][0], sort_keys=True)
                    for call in calls if call["function"] == "draw_set_blend_mode")
    return dict(sha256=hashlib.sha256(blob).hexdigest(),
                gen8_version_fields=list(struct.unpack_from("<4I", blob, gen8 + 44)),
                static_call_counts=dict(actual),
                named_arguments=dict(named), blendmode_assignments=assignments, calls=calls,
                caveat="Static literal/direct-variable recognition, not execution frequency, reachability, indirect-write exclusion or native pixel evidence")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_win", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = inventory(args.data_win)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("Input SHA256:", result["sha256"])
        print("GEN8 version fields:", result["gen8_version_fields"])
        print("Static calls:", result["static_call_counts"])
        print("Named arguments:", result["named_arguments"])
        print("Direct blendmode assignments:", result["blendmode_assignments"])
        for call in result["calls"]:
            if call["function"] != "draw_set_blend_mode" or call["arguments"][0].get("literal") not in (0, 1):
                print(json.dumps(call))
        print(result["caveat"])


if __name__ == "__main__":
    main()
