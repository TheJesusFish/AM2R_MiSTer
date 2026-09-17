"""Give an isolated hardware-QA process enough health for a lava-room test.

This changes only the live process.  It is never compiled into or shipped with
the runtime, and the test process is discarded when QA returns to menu.rbf.
"""

import gdb


RVALUE_REAL = 5


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
changed = []

table = globals_instance["selfVars"]
health_names = sorted(name for name in names.values() if "health" in name.lower())
print("PACING_QA_HEALTH_NAMES " + " ".join(health_names))
for index in range(int(table["capacity"])):
    entry = table["entries"][index]
    name = names.get(int(entry["key"]))
    if name and name.lower() in ("playerhealth", "samushealth", "maxhealth"):
        set_real(entry["value"], 9999.0)
        changed.append(name.lower())

if "maxhealth" not in changed or not ({"playerhealth", "samushealth"} & set(changed)):
    raise gdb.GdbError("expected maxhealth and a live Samus-health global")

print("PACING_QA_HEALTH %s=9999" % " ".join(sorted(changed)))
