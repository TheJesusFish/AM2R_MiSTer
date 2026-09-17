"""Print live character/camera field addresses for a read-only QA sampler."""

import gdb


def array_length(pointer):
    if not int(pointer):
        return 0
    header_type = gdb.lookup_type("stbds_array_header").pointer()
    return int((pointer.cast(header_type) - 1).dereference()["length"])


runner_pointer = gdb.parse_and_eval("g_runner")
runner = runner_pointer.dereference()
data_win = runner["dataWin"].dereference()
print("MOTION_FRAME address=0x%x value=%d" %
      (int(runner["frameCount"].address), int(runner["frameCount"])))

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
    if name not in ("oCharacter", "oCamera"):
        continue
    if not bool(instance["active"]) or bool(instance["destroyed"]):
        continue
    address = int(pointer)
    x_address = int(instance["x"].address)
    y_address = int(instance["y"].address)
    print(
        "MOTION_OBJECT name=%s index=%d instance=0x%x x=0x%x y=0x%x "
        "x_offset=%d y_offset=%d value=(%.9g,%.9g)"
        % (
            name,
            index,
            address,
            x_address,
            y_address,
            x_address - address,
            y_address - address,
            float(instance["x"]),
            float(instance["y"]),
        )
    )
