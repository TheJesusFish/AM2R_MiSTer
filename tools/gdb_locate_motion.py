"""Print live character/camera field addresses for a read-only QA sampler."""

import struct

import gdb


def array_length(pointer):
    if not int(pointer):
        return 0
    header_type = gdb.lookup_type("stbds_array_header").pointer()
    return int((pointer.cast(header_type) - 1).dereference()["length"])


def locate_runner():
    """Use the symbol when valid, otherwise scan the small executable BSS."""
    inferior = gdb.selected_inferior()
    runner_type = gdb.lookup_type("Runner").pointer()
    try:
        direct = gdb.parse_and_eval("g_runner")
        if 0x001D0000 <= int(direct) < 0xB0000000:
            runner = direct.dereference()
            if (0 <= int(runner["currentRoomIndex"]) < 1000 and
                    0 < int(runner["frameCount"]) < 100000000):
                return direct, runner
    except (gdb.error, gdb.MemoryError):
        pass

    for address in range(0x001CF000, 0x00220000, 4):
        try:
            pointer_value = struct.unpack(
                "<I", bytes(inferior.read_memory(address, 4))
            )[0]
            if (pointer_value < 0x001D0000 or pointer_value >= 0xB0000000 or
                    pointer_value & 3):
                continue
            pointer = gdb.Value(pointer_value).cast(runner_type)
            runner = pointer.dereference()
            data_win = runner["dataWin"].dereference()
            if not (0 <= int(runner["currentRoomIndex"]) < 1000):
                continue
            if not (0 < int(runner["frameCount"]) < 100000000):
                continue
            if not (100 <= int(data_win["objt"]["count"]) < 10000):
                continue
            return pointer, runner
        except (gdb.error, gdb.MemoryError):
            continue
    raise gdb.GdbError("could not locate the live AM2R Runner")


runner_pointer, runner = locate_runner()
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
