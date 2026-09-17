"""Describe an AM2R scene from a DMTCP image paused at restart level 4.

The checkpointed executable may no longer exist at /proc/PID/exe, so locate
the restored Runner through independent layout invariants instead of relying
on the g_runner symbol.  This script is read-only: it reports the room,
character, exits, and nearby instances without resuming the checkpoint.
"""

import math
import struct

import gdb


def array_length(pointer):
    if not int(pointer):
        return 0
    header_type = gdb.lookup_type("stbds_array_header").pointer()
    return int((pointer.cast(header_type) - 1).dereference()["length"])


def object_name(data_win, object_index):
    if object_index < 0 or object_index >= int(data_win["objt"]["count"]):
        return "<?>"
    pointer = data_win["objt"]["objects"][object_index]["name"]
    return pointer.string() if int(pointer) else "<?>"


def locate_runner():
    inferior = gdb.selected_inferior()
    runner_type = gdb.lookup_type("Runner").pointer()
    # With the exact unstripped companion ELF, DMTCP restores the production
    # image at its original non-PIE addresses and the symbol is authoritative.
    # Retain the scan below for older checkpoints whose original ELF is gone.
    try:
        direct = gdb.parse_and_eval("g_runner")
        if int(direct):
            return int(gdb.parse_and_eval("&g_runner")), int(direct), direct.dereference()
    except (gdb.error, gdb.MemoryError):
        pass
    # Production runners are stripped after link, and the final v30 link grew
    # beyond the historical 0x1d8000 BSS ceiling as later renderer fixes were
    # folded in without changing the state format.  Scan the complete small
    # executable data/BSS window rather than baking in an obsolete end address.
    for address in range(0x001CF000, 0x00220000, 4):
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
        if not (0 <= current_room < 1000 and -1 <= pending_room < 1000):
            continue
        if not (0 < frame < 100000000 and 100000 <= next_instance < 10000000):
            continue
        if not (100 <= object_count < 10000 and 1 <= room_count < 10000
                and 100 <= sprite_count < 10000):
            continue
        return address, pointer_value, runner
    raise gdb.GdbError("could not locate a plausible restored AM2R Runner")


global_address, runner_address, runner = locate_runner()
data_win = runner["dataWin"].dereference()
room = runner["currentRoom"]
room_index = int(runner["currentRoomIndex"])
room_name = room["name"].string() if int(room["name"]) else "<?>"

print(
    "CHECKPOINT_SCENE global=0x%x runner=0x%x room=%d:%s pending=%d "
    "frame=%d instances=%d persistent=%d"
    % (
        global_address,
        runner_address,
        room_index,
        room_name,
        int(runner["pendingRoom"]),
        int(runner["frameCount"]),
        array_length(runner["instances"]),
        1 if bool(room["persistent"]) else 0,
    )
)

character = None
instances = []
for index in range(array_length(runner["instances"])):
    pointer = runner["instances"][index]
    if not int(pointer):
        continue
    instance = pointer.dereference()
    name = object_name(data_win, int(instance["objectIndex"]))
    item = (name, instance)
    instances.append(item)
    if name == "oCharacter" and bool(instance["active"]) and not bool(instance["destroyed"]):
        character = instance

if character is None:
    print("CHECKPOINT_CHARACTER missing")
    character_x = character_y = 0.0
else:
    character_x = float(character["x"])
    character_y = float(character["y"])
    print(
        "CHECKPOINT_CHARACTER id=%d x=%.6f y=%.6f previous=(%.6f,%.6f) "
        "speed=(%.6f,%.6f) sprite=%d mask=%d visible=%d"
        % (
            int(character["instanceId"]),
            character_x,
            character_y,
            float(character["xprevious"]),
            float(character["yprevious"]),
            float(character["hspeed"]),
            float(character["vspeed"]),
            int(character["spriteIndex"]),
            int(character["maskIndex"]),
            1 if bool(character["visible"]) else 0,
        )
    )

nearby = []
for name, instance in instances:
    if bool(instance["destroyed"]):
        continue
    x = float(instance["x"])
    y = float(instance["y"])
    distance = math.hypot(x - character_x, y - character_y)
    if name == "oGotoRoom" or distance <= 256.0:
        nearby.append((0 if name == "oGotoRoom" else 1, distance, name, instance))

for _, distance, name, instance in sorted(
        nearby, key=lambda item: (item[0], item[1], int(item[3]["instanceId"]))):
    print(
        "CHECKPOINT_INSTANCE object=%s id=%d x=%.6f y=%.6f distance=%.3f "
        "active=%d visible=%d persistent=%d"
        % (
            name,
            int(instance["instanceId"]),
            float(instance["x"]),
            float(instance["y"]),
            distance,
            1 if bool(instance["active"]) else 0,
            1 if bool(instance["visible"]) else 0,
            1 if bool(instance["persistent"]) else 0,
        )
    )
