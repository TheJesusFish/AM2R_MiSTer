"""List live AM2R item instances and their coordinates."""

import gdb


def array_length(pointer):
    if not int(pointer):
        return 0
    header = gdb.lookup_type("stbds_array_header").pointer()
    return int((pointer.cast(header) - 1).dereference()["length"])


runner = gdb.parse_and_eval("g_runner").dereference()
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
    name = name_pointer.string() if int(name_pointer) else "<?>"
    if name.startswith("oItem"):
        print(
            "AM2R_ITEM index=%d object=%s id=%d x=%.6f y=%.6f active=%d visible=%d"
            % (
                index,
                name,
                int(instance["instanceId"]),
                float(instance["x"]),
                float(instance["y"]),
                1 if bool(instance["active"]) else 0,
                1 if bool(instance["visible"]) else 0,
            )
        )
