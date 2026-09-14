"""Disable oWaterFXV2 only in the disposable hardware-test process."""

import gdb


def array_length(pointer):
    if not int(pointer):
        return 0
    header_type = gdb.lookup_type("stbds_array_header").pointer()
    return int((pointer.cast(header_type) - 1).dereference()["length"])


runner = gdb.parse_and_eval("g_runner").dereference()
data_win = runner["dataWin"].dereference()
disabled = 0
for index in range(array_length(runner["instances"])):
    pointer = runner["instances"][index]
    if not int(pointer):
        continue
    instance = pointer.dereference()
    object_index = int(instance["objectIndex"])
    if object_index < 0 or object_index >= int(data_win["objt"]["count"]):
        continue
    name_pointer = data_win["objt"]["objects"][object_index]["name"]
    if int(name_pointer) and name_pointer.string() == "oWaterFXV2":
        gdb.execute("set *(bool*)0x%x = 0" % int(instance["active"].address))
        disabled += 1
        print(
            "WATER_FX_DISABLED id=%d x=%.3f y=%.3f"
            % (int(instance["instanceId"]), float(instance["x"]), float(instance["y"]))
        )

print("WATER_FX_DISABLED_COUNT value=%d" % disabled)
