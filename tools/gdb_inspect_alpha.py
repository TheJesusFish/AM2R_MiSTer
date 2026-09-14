"""Report live Alpha Metroid combat variables without changing game state."""

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
found = 0
for index in range(array_length(runner["instances"])):
    pointer = runner["instances"][index]
    if not int(pointer):
        continue
    instance = pointer.dereference()
    object_index = int(instance["objectIndex"])
    if object_index < 0 or object_index >= int(data_win["objt"]["count"]):
        continue
    name_pointer = data_win["objt"]["objects"][object_index]["name"]
    if not int(name_pointer) or name_pointer.string() != "oMAlpha":
        continue
    values = {}
    table = instance["selfVars"]
    for entry_index in range(int(table["capacity"])):
        entry = table["entries"][entry_index]
        name = names.get(int(entry["key"]))
        if name not in ("myhealth", "starthealth", "flashing", "flashtime", "state", "canbehit", "myid"):
            continue
        value = entry["value"]
        if int(value["type"]) == RVALUE_REAL:
            values[name] = float(value["real"])
    print(
        "ALPHA_STATE id=%d x=%.3f y=%.3f active=%d visible=%d %s"
        % (
            int(instance["instanceId"]),
            float(instance["x"]),
            float(instance["y"]),
            int(instance["active"]),
            int(instance["visible"]),
            " ".join("%s=%.3f" % item for item in sorted(values.items())),
        )
    )
    found += 1

print("ALPHA_COUNT value=%d" % found)
