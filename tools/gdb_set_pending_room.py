"""Queue a room transition in a live stripped Butterscotch process.

Hardware-QA only.  The target room is supplied through GDB's ``$qa_room``
convenience variable.  The script finds the Runner pointer by validating live
heap candidates, so it does not depend on an optimized build retaining the
file-local ``g_runner`` symbol.
"""

import struct

import gdb


inferior = gdb.selected_inferior()
runner_type = gdb.lookup_type("Runner").pointer()
runner_pointer = 0

for address in range(0x001CF000, 0x00220000, 4):
    try:
        pointer_value = struct.unpack(
            "<I", bytes(inferior.read_memory(address, 4))
        )[0]
        if pointer_value < 0x001D0000 or pointer_value >= 0xB0000000 or pointer_value & 3:
            continue
        candidate = gdb.Value(pointer_value).cast(runner_type).dereference()
        data_win = candidate["dataWin"].dereference()
        if not (0 <= int(candidate["currentRoomIndex"]) < 1000):
            continue
        if not (100 <= int(data_win["objt"]["count"]) < 10000):
            continue
        runner_pointer = pointer_value
        break
    except (gdb.error, gdb.MemoryError):
        continue

if not runner_pointer:
    raise gdb.GdbError("could not locate the live AM2R Runner")

room_value = gdb.parse_and_eval("$qa_room")
if room_value.type.code == gdb.TYPE_CODE_VOID:
    raise gdb.GdbError("set $qa_room before sourcing this script")
target_room = int(room_value)
if not (0 <= target_room < 1000):
    raise gdb.GdbError("$qa_room is outside the supported room range")

runner = gdb.Value(runner_pointer).cast(runner_type).dereference()
print(
    "ROOM_QA_QUEUE current=%d pending=%d target=%d frame=%d"
    % (
        int(runner["currentRoomIndex"]),
        int(runner["pendingRoom"]),
        target_room,
        int(runner["frameCount"]),
    )
)
gdb.execute(
    "set var ((Runner*)0x%x)->pendingRoom = %d" % (runner_pointer, target_room),
    to_string=True,
)
