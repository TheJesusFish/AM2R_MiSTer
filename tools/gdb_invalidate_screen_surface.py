"""Invalidate AM2R's live oControl.screen_surface for one QA reproduction.

This bounded mutation leaves the aliased surface itself untouched.  It only
sets the live field to -1 so the game's existing `if (!surface_exists(...))`
path allocates a dedicated snapshot surface on the next item acquisition.
"""

import gdb


RVALUE_REAL = 5


def array_length(pointer):
    if not int(pointer):
        return 0
    header_type = gdb.lookup_type("stbds_array_header").pointer()
    return int((pointer.cast(header_type) - 1).dereference()["length"])


def hash_map_length(pointer):
    header_type = gdb.lookup_type("stbds_array_header").pointer()
    return int(((pointer - 1).cast(header_type) - 1).dereference()["length"] - 1)


runner = gdb.parse_and_eval("g_runner").dereference()
vm = runner["vmContext"].dereference()
names = {}
name_map = vm["varNameMap"]
for index in range(hash_map_length(name_map)):
    entry = name_map[index]
    if not int(entry["key"]):
        continue
    try:
        names[int(entry["value"])] = entry["key"].string()
    except (gdb.MemoryError, UnicodeError):
        pass

data_win = runner["dataWin"].dereference()
for index in range(array_length(runner["instances"])):
    pointer = runner["instances"][index]
    if not int(pointer):
        continue
    instance = pointer.dereference()
    object_index = int(instance["objectIndex"])
    if object_index < 0 or object_index >= int(data_win["objt"]["count"]):
        continue
    name_pointer = data_win["objt"]["objects"][object_index]["name"]
    if not int(name_pointer) or name_pointer.string() != "oControl":
        continue
    table = instance["selfVars"]
    for entry_index in range(int(table["capacity"])):
        entry = table["entries"][entry_index]
        if names.get(int(entry["key"])) != "screen_surface":
            continue
        value = entry["value"]
        old_type = int(value["type"])
        old_value = float(value["real"]) if old_type == RVALUE_REAL else 0.0
        address = int(value.address)
        gdb.execute("set ((RValue*)0x%x)->real=-1" % address)
        gdb.execute("set ((RValue*)0x%x)->type=%d" % (address, RVALUE_REAL))
        gdb.execute("set ((RValue*)0x%x)->ownsReference=0" % address)
        gdb.execute("set ((RValue*)0x%x)->gmlStackType=0" % address)
        print(
            "SCREEN_SURFACE_INVALIDATED instance=%d old_type=%d old_value=%.0f new_value=-1"
            % (int(instance["instanceId"]), old_type, old_value)
        )
        raise SystemExit

raise gdb.GdbError("live oControl.screen_surface not found")
