"""Recreate the supplied slot-1 Alpha Metroid encounter in live QA state.

Only the isolated running process is mutated.  The defeated flag for Alpha 4
is cleared, missiles are replenished, and the authored room is reloaded so its
normal proximity trigger creates the enemy.
"""

import gdb


RVALUE_REAL = 5
METROID_INDEX = 4


def hash_map_length(pointer):
    header_type = gdb.lookup_type("stbds_array_header").pointer()
    return int(((pointer - 1).cast(header_type) - 1).dereference()["length"] - 1)


def variable_names(vm):
    result = {}
    name_map = vm["varNameMap"]
    for index in range(hash_map_length(name_map)):
        entry = name_map[index]
        if not int(entry["key"]):
            continue
        try:
            result[int(entry["value"])] = entry["key"].string()
        except (gdb.MemoryError, UnicodeError):
            pass
    return result


def set_real(slot, value):
    address = int(slot.address)
    gdb.execute("set ((RValue*)0x%x)->real=%.9g" % (address, value))
    gdb.execute("set ((RValue*)0x%x)->type=%d" % (address, RVALUE_REAL))
    gdb.execute("set ((RValue*)0x%x)->ownsReference=0" % address)
    gdb.execute("set ((RValue*)0x%x)->gmlStackType=0" % address)


runner = gdb.parse_and_eval("g_runner").dereference()
vm = runner["vmContext"].dereference()
globals_instance = vm["globalScopeInstance"].dereference()
names = variable_names(vm)
metdead_slot = None
spawn_values = {
    # Enter rm_a1b05 inside the authored Alpha proximity trigger. Without
    # replacing these item-cutscene carry-over coordinates, a direct QA room
    # reload can immediately schedule an out-of-bounds room transition.
    "targetx": 500.0,
    "targety": 96.0,
    "offsetx": 0.0,
    "offsety": 0.0,
}

table = globals_instance["selfVars"]
for index in range(int(table["capacity"])):
    entry = table["entries"][index]
    name = names.get(int(entry["key"]))
    if name in ("missiles", "maxmissiles"):
        set_real(entry["value"], 30.0)
        print("ALPHA_FIGHT_GLOBAL name=%s value=30" % name)
    elif name == "monstersalive":
        set_real(entry["value"], 0.0)
        print("ALPHA_FIGHT_GLOBAL name=monstersalive value=0")
    elif name in spawn_values:
        set_real(entry["value"], spawn_values[name])
        print("ALPHA_FIGHT_GLOBAL name=%s value=%.0f" % (name, spawn_values[name]))
    elif name == "metdead":
        metdead_slot = entry["value"]

if metdead_slot is None or int(metdead_slot["type"]) != 6 or not int(metdead_slot["array"]):
    raise gdb.GdbError("global.metdead is not a live GML array")

array = metdead_slot["array"].dereference()
if int(array["type"]) != 0 or int(array["legacy"]["rowCount"]) < 1:
    raise gdb.GdbError("global.metdead does not have the expected legacy layout")
row = array["legacy"]["rows"][0]
if int(row["length"]) <= METROID_INDEX:
    raise gdb.GdbError("global.metdead is shorter than Alpha index 4")
set_real(row["data"][METROID_INDEX], 0.0)
print("ALPHA_FIGHT_METDEAD index=%d value=0" % METROID_INDEX)

gdb.execute("set g_runner->pendingRoom=81")
print("ALPHA_FIGHT_RELOAD pendingRoom=81")
