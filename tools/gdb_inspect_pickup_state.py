"""Report AM2R missile inventory and live pickup state without mutation."""

import gdb


RVALUE_REAL = 5
GLOBAL_NAMES = {"missiles", "maxmissiles", "mtanks"}


def array_length(pointer):
    if not int(pointer):
        return 0
    header = gdb.lookup_type("stbds_array_header").pointer()
    return int((pointer.cast(header) - 1).dereference()["length"])


def hash_map_length(pointer):
    header = gdb.lookup_type("stbds_array_header").pointer()
    return int(((pointer - 1).cast(header) - 1).dereference()["length"] - 1)


runner = gdb.parse_and_eval("g_runner").dereference()
vm = runner["vmContext"].dereference()
data_win = runner["dataWin"].dereference()

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
    if name not in GLOBAL_NAMES:
        continue
    value = entry["value"]
    if int(value["type"]) == RVALUE_REAL:
        print("PICKUP_GLOBAL name=%s value=%.6f" % (name, float(value["real"])))
    else:
        print("PICKUP_GLOBAL name=%s type=%d" % (name, int(value["type"])))

for index in range(array_length(runner["instances"])):
    pointer = runner["instances"][index]
    if not int(pointer):
        continue
    instance = pointer.dereference()
    object_index = int(instance["objectIndex"])
    if object_index < 0 or object_index >= int(data_win["objt"]["count"]):
        continue
    name_pointer = data_win["objt"]["objects"][object_index]["name"]
    name = name_pointer.string() if int(name_pointer) else "<?>"
    if not name.startswith("oItem"):
        continue
    print(
        "PICKUP_INSTANCE object=%s id=%d x=%.3f y=%.3f active=%d "
        "visible=%d destroyed=%d"
        % (
            name,
            int(instance["instanceId"]),
            float(instance["x"]),
            float(instance["y"]),
            1 if bool(instance["active"]) else 0,
            1 if bool(instance["visible"]) else 0,
            1 if bool(instance["destroyed"]) else 0,
        )
    )
