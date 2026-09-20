"""Give a live AM2R QA session enough protection to exercise the lava room.

Debugger-only: this changes RAM in the disposable hardware session.  It does
not modify the runtime, save game, or checkpoint files.
"""

import struct

import gdb


inferior = gdb.selected_inferior()


def locate_runner_pointer():
    runner_type = gdb.lookup_type("Runner").pointer()
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
            return address, pointer_value
        except (gdb.error, gdb.MemoryError):
            continue
    raise gdb.GdbError("could not locate the live AM2R Runner")


runner_slot, runner_pointer = locate_runner_pointer()
runner = gdb.Value(runner_pointer).cast(gdb.lookup_type("Runner").pointer()).dereference()
data_win = runner["dataWin"].dereference()
global_instance = runner["vmContext"].dereference()["globalScopeInstance"].dereference()


def variable_ids(name):
    result = set()
    for index in range(int(data_win["vari"]["variableCount"])):
        variable = data_win["vari"]["variables"][index]
        pointer = variable["name"]
        if int(pointer) and pointer.string() == name and int(variable["varID"]) >= 0:
            result.add(int(variable["varID"]))
    return result


def find_entry(name):
    ids = variable_ids(name)
    entries = global_instance["selfVars"]["entries"]
    for index in range(int(global_instance["selfVars"]["capacity"])):
        if int(entries[index]["key"]) in ids:
            return int(entries) + index * int(entries[index].type.sizeof)
    raise gdb.GdbError("global.%s has no existing value slot" % name)


def set_real(name, value):
    address = find_entry(name)
    gdb.execute("set var ((IntRValueEntry*)0x%x)->value.real = %.17g" % (address, value), to_string=True)
    gdb.execute("set var ((IntRValueEntry*)0x%x)->value.type = 5" % address, to_string=True)
    gdb.execute("set var ((IntRValueEntry*)0x%x)->value.ownsReference = 0" % address, to_string=True)
    gdb.execute("set var ((IntRValueEntry*)0x%x)->value.gmlStackType = 0" % address, to_string=True)
    return address


def set_int(name, value):
    address = find_entry(name)
    gdb.execute("set var ((IntRValueEntry*)0x%x)->value.int32 = %d" % (address, value), to_string=True)
    gdb.execute("set var ((IntRValueEntry*)0x%x)->value.type = 2" % address, to_string=True)
    gdb.execute("set var ((IntRValueEntry*)0x%x)->value.ownsReference = 0" % address, to_string=True)
    gdb.execute("set var ((IntRValueEntry*)0x%x)->value.gmlStackType = 2" % address, to_string=True)
    return address


health_address = set_real("samushealth", 9999.0)
max_health_address = set_real("maxhealth", 9999.0)
suit_address = set_int("currentsuit", 2)
print(
    "LAVA_QA_GLOBALS slot=0x%x samushealth=9999@0x%x maxhealth=9999@0x%x currentsuit=2@0x%x"
    % (runner_slot, health_address, max_health_address, suit_address)
)
