"""Prepare AM2R item 163 for a deterministic missile-pickup regression.

This intentionally mutates only the live QA process.  It restores the early
game inventory values present in the user's slot-3 checkpoint and clears the
single missile-tank collection flag before the room is reloaded.
"""

import gdb


RVALUE_REAL = 5
ITEM_INDEX = 163


def hash_map_length(pointer):
    # stb_ds hash maps expose a default entry at [-1], one slot beyond the
    # ordinary dynamic-array header.
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
wanted = {
    "maxmissiles": 30.0,
    "missiles": 30.0,
    "mtanks": 0.0,
    # A same-room reload after an item cutscene restores oCharacter from these
    # globals. Keep the QA spawn outside the pickup collision mask so the
    # capture can begin before the ordinary collision event fires.
    "targetx": 3060.0,
    "targety": 608.0,
    "offsetx": 0.0,
    "offsety": 0.0,
}
item_slot = None

table = globals_instance["selfVars"]
for index in range(int(table["capacity"])):
    entry = table["entries"][index]
    name = names.get(int(entry["key"]))
    if name in wanted:
        set_real(entry["value"], wanted[name])
        print("MISSILE_PICKUP_GLOBAL name=%s value=%.0f" % (name, wanted[name]))
    elif name == "item":
        item_slot = entry["value"]

if item_slot is None or int(item_slot["type"]) != 6 or not int(item_slot["array"]):
    raise gdb.GdbError("global.item is not a live GML array")

array = item_slot["array"].dereference()
if int(array["type"]) != 0 or int(array["legacy"]["rowCount"]) < 1:
    raise gdb.GdbError("global.item does not have the expected legacy layout")
row = array["legacy"]["rows"][0]
if int(row["length"]) <= ITEM_INDEX:
    raise gdb.GdbError("global.item is shorter than item 163")
set_real(row["data"][ITEM_INDEX], 0.0)
print("MISSILE_PICKUP_ITEM index=%d value=0" % ITEM_INDEX)

# Reload the authored room so oItem's Other event observes the cleared flag.
gdb.execute("set g_runner->pendingRoom=83")
print("MISSILE_PICKUP_RELOAD pendingRoom=83")
