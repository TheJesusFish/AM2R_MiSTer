"""Arm the live room's save station after debugger-only character staging.

Directly moving the persistent character does not always refresh GameMaker's
collision-line cache before ``oSaveStation`` runs.  Set only the station's
derived ``enabled`` and ``cansave`` flags; its normal Step event still performs
the save and all game-state updates.
"""

import struct

import gdb


def array_length(pointer):
    if not int(pointer):
        return 0
    header_type = gdb.lookup_type("stbds_array_header").pointer()
    return int((pointer.cast(header_type) - 1).dereference()["length"])


inferior = gdb.selected_inferior()
runner_type = gdb.lookup_type("Runner").pointer()
runner_pointer = 0
for address in range(0x001CF000, 0x00220000, 4):
    try:
        pointer_value = struct.unpack(
            "<I", bytes(inferior.read_memory(address, 4))
        )[0]
        if pointer_value < 0x001D0000 or pointer_value >= 0xB0000000 or pointer_value & 3:
            continue
        candidate = gdb.Value(pointer_value).cast(runner_type).dereference()
        data_win = candidate["dataWin"].dereference()
        if not (0 <= int(candidate["currentRoomIndex"]) < 1000):
            continue
        if not (100 <= int(data_win["objt"]["count"]) < 10000):
            continue
        runner_pointer = pointer_value
        break
    except (gdb.error, gdb.MemoryError):
        continue

if not runner_pointer:
    raise gdb.GdbError("could not locate the live AM2R Runner")

runner = gdb.Value(runner_pointer).cast(runner_type).dereference()
data_win = runner["dataWin"].dereference()
variable_ids = {"enabled": set(), "cansave": set()}
for index in range(int(data_win["vari"]["variableCount"])):
    variable = data_win["vari"]["variables"][index]
    name_pointer = variable["name"]
    if not int(name_pointer):
        continue
    name = name_pointer.string()
    if name in variable_ids:
        variable_ids[name].add(int(variable["varID"]))

station_pointer = 0
for index in range(array_length(runner["instances"])):
    pointer = runner["instances"][index]
    if not int(pointer):
        continue
    instance = pointer.dereference()
    object_index = int(instance["objectIndex"])
    if object_index < 0 or object_index >= int(data_win["objt"]["count"]):
        continue
    name_pointer = data_win["objt"]["objects"][object_index]["name"]
    name = name_pointer.string() if int(name_pointer) else ""
    if name == "oSaveStation" and bool(instance["active"]) and not bool(instance["destroyed"]):
        station_pointer = int(pointer)
        break

if not station_pointer:
    raise gdb.GdbError("active oSaveStation was not found")

station = gdb.Value(station_pointer).cast(gdb.lookup_type("Instance").pointer()).dereference()
changed = []
entries = station["selfVars"]["entries"]
for index in range(int(station["selfVars"]["capacity"])):
    entry = entries[index]
    for name, ids in variable_ids.items():
        if int(entry["key"]) not in ids:
            continue
        entry_address = int(entries) + index * int(entry.type.sizeof)
        gdb.execute(
            "set var ((IntRValueEntry*)0x%x)->value.real = 1.0" % entry_address,
            to_string=True,
        )
        gdb.execute(
            "set var ((IntRValueEntry*)0x%x)->value.type = 5" % entry_address,
            to_string=True,
        )
        changed.append(name)

if set(changed) != set(variable_ids):
    raise gdb.GdbError("save station is missing expected derived variables")

print(
    "SAVE_STATION_QA_ARMED room=%d station=%d variables=%s"
    % (
        int(runner["currentRoomIndex"]),
        int(station["instanceId"]),
        ",".join(sorted(changed)),
    )
)
