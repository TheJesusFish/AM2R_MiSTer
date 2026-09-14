"""Reload the current underwater QA room at the checkpoint coordinates.

This intentionally mutates only the disposable hardware-test process.  It is
used to make AM2R recreate its water-effect surface so the initialization frame
can be inspected independently from the surface stored in the checkpoint.
"""

import gdb


RVALUE_REAL = 5
WANTED = {
    "targetx": 554.0,
    "targety": 1648.0,
    "offsetx": 0.0,
    "offsety": 0.0,
}


def hash_map_length(pointer):
    header = gdb.lookup_type("stbds_array_header").pointer()
    return int(((pointer - 1).cast(header) - 1).dereference()["length"] - 1)


runner = gdb.parse_and_eval("g_runner").dereference()
vm = runner["vmContext"].dereference()
names = {}
name_map = vm["varNameMap"]
for index in range(hash_map_length(name_map)):
    entry = name_map[index]
    if int(entry["key"]):
        try:
            names[int(entry["value"])] = entry["key"].string()
        except (gdb.MemoryError, UnicodeError):
            pass

globals_instance = vm["globalScopeInstance"].dereference()
for index in range(int(globals_instance["selfVars"]["capacity"])):
    entry = globals_instance["selfVars"]["entries"][index]
    name = names.get(int(entry["key"]))
    if name not in WANTED:
        continue
    address = int(entry["value"].address)
    gdb.execute("set ((RValue*)0x%x)->real=%.9g" % (address, WANTED[name]))
    gdb.execute("set ((RValue*)0x%x)->type=%d" % (address, RVALUE_REAL))
    gdb.execute("set ((RValue*)0x%x)->ownsReference=0" % address)
    gdb.execute("set ((RValue*)0x%x)->gmlStackType=0" % address)
    print("UNDERWATER_RELOAD_GLOBAL name=%s value=%.0f" % (name, WANTED[name]))

gdb.execute("set g_runner->pendingRoom=83")
print("UNDERWATER_RELOAD pendingRoom=83 target=554,1648")
