"""Locate g_runner in a restored process when the checkpointed ELF is absent.

DMTCP restores the executable's old data/BSS image but /proc/PID/exe names the
restart loader.  The exact historical ELF may no longer exist.  Runner's layout
has remained stable across the relevant builds, so scan the small original
data/BSS/initial-brk range for pointers whose pointee satisfies independent
Runner/DataWin invariants.
"""

import struct
import gdb


inferior = gdb.selected_inferior()
runner_type = gdb.lookup_type("Runner").pointer()

for address in range(0x001CF000, 0x001D8000, 4):
    try:
        raw = bytes(inferior.read_memory(address, 4))
    except gdb.MemoryError:
        continue
    pointer_value = struct.unpack("<I", raw)[0]
    if pointer_value < 0x001D0000 or pointer_value >= 0xB0000000 or pointer_value & 3:
        continue
    try:
        runner = gdb.Value(pointer_value).cast(runner_type).dereference()
        data_win_pointer = int(runner["dataWin"])
        vm_pointer = int(runner["vmContext"])
        renderer_pointer = int(runner["renderer"])
        grid_pointer = int(runner["spatialGrid"])
        current_room = int(runner["currentRoomIndex"])
        pending_room = int(runner["pendingRoom"])
        frame = int(runner["frameCount"])
        next_instance = int(runner["nextInstanceId"])
        data_win = runner["dataWin"].dereference()
        object_count = int(data_win["objt"]["count"])
        room_count = int(data_win["room"]["count"])
        sprite_count = int(data_win["sprt"]["count"])
    except (gdb.error, gdb.MemoryError):
        continue
    if not (0x001D0000 <= data_win_pointer < 0xB0000000):
        continue
    if not (0x001D0000 <= vm_pointer < 0xB0000000):
        continue
    if not (0x001D0000 <= renderer_pointer < 0xB0000000):
        continue
    if not (0x001D0000 <= grid_pointer < 0xB0000000):
        continue
    if not (0 <= current_room < 1000 and -1 <= pending_room < 1000):
        continue
    if not (0 < frame < 100000000 and 100000 <= next_instance < 10000000):
        continue
    if not (100 <= object_count < 10000 and 1 <= room_count < 10000
            and 100 <= sprite_count < 10000):
        continue
    print(
        "RUNNER_POINTER global_address=0x%x runner=0x%x dataWin=0x%x vm=0x%x "
        "renderer=0x%x grid=0x%x room=%d pending=%d frame=%d nextInstance=%d "
        "objects=%d rooms=%d sprites=%d" % (
            address, pointer_value, data_win_pointer, vm_pointer,
            renderer_pointer, grid_pointer, current_room, pending_room, frame,
            next_instance, object_count, room_count, sprite_count,
        )
    )
