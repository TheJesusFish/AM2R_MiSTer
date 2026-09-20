"""Move the live AM2R character for room-transition hardware QA.

The target position is supplied through GDB convenience variables ``$qa_x``
and ``$qa_y``.  This deliberately changes no gameplay variables, health, or
inventory so a normal save made after traversing retains the checkpoint's
real progression state without QA cheats.
"""

import struct

import gdb


def array_length(pointer):
    if not int(pointer):
        return 0
    header_type = gdb.lookup_type("stbds_array_header").pointer()
    return int((pointer.cast(header_type) - 1).dereference()["length"])


def required_coordinate(name):
    value = gdb.parse_and_eval("$" + name)
    if value.type.code == gdb.TYPE_CODE_VOID:
        raise gdb.GdbError("set $%s before sourcing this script" % name)
    return float(value)


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

runner = gdb.Value(runner_pointer).cast(runner_type).dereference()
data_win = runner["dataWin"].dereference()
character_pointer = 0
for index in range(array_length(runner["instances"])):
    pointer = runner["instances"][index]
    if not int(pointer):
        continue
    instance = pointer.dereference()
    object_index = int(instance["objectIndex"])
    if object_index < 0 or object_index >= int(data_win["objt"]["count"]):
        continue
    name_pointer = data_win["objt"]["objects"][object_index]["name"]
    name = name_pointer.string() if int(name_pointer) else ""
    if name == "oCharacter" and bool(instance["active"]) and not bool(instance["destroyed"]):
        character_pointer = int(pointer)
        break

if not character_pointer:
    raise gdb.GdbError("active oCharacter was not found")

qa_x = required_coordinate("qa_x")
qa_y = required_coordinate("qa_y")
for field, value in (
    ("x", qa_x),
    ("y", qa_y),
    ("xprevious", qa_x),
    ("yprevious", qa_y),
    ("speed", 0.0),
    ("hspeed", 0.0),
    ("vspeed", 0.0),
):
    gdb.execute(
        "set var ((Instance*)0x%x)->%s = %.9g" % (character_pointer, field, value),
        to_string=True,
    )
gdb.execute(
    "set var ((Instance*)0x%x)->spatialGridDirty = 1" % character_pointer,
    to_string=True,
)

# The runtime's dirty flag is paired with an ID queue.  A debugger write to the
# flag alone is insufficient: SpatialGrid_syncGrid only walks dirtyInstances.
# Reuse the queue's existing allocation so the next collision query reindexes
# the character at the staged coordinates.
dirty_instances = runner["spatialGrid"].dereference()["dirtyInstances"]
if not int(dirty_instances):
    raise gdb.GdbError("spatial-grid dirty queue is not allocated")
dirty_header = (dirty_instances.cast(gdb.lookup_type("stbds_array_header").pointer()) - 1)
dirty_length = int(dirty_header.dereference()["length"])
dirty_capacity = int(dirty_header.dereference()["capacity"])
character_id = int(
    gdb.Value(character_pointer)
    .cast(gdb.lookup_type("Instance").pointer())
    .dereference()["instanceId"]
)
queued = any(int(dirty_instances[index]) == character_id for index in range(dirty_length))
if not queued:
    if dirty_length >= dirty_capacity:
        raise gdb.GdbError("spatial-grid dirty queue has no spare capacity")
    gdb.execute(
        "set var ((int32_t*)0x%x)[%d] = %d"
        % (int(dirty_instances), dirty_length, character_id),
        to_string=True,
    )
    gdb.execute(
        "set var ((stbds_array_header*)0x%x - 1)->length = %d"
        % (int(dirty_instances), dirty_length + 1),
        to_string=True,
    )

room = runner["currentRoom"]
print(
    "CHARACTER_QA_PLACED room=%d:%s x=%.3f y=%.3f grid_queued=%d"
    % (
        int(runner["currentRoomIndex"]),
        room["name"].string() if int(room["name"]) else "<?>",
        qa_x,
        qa_y,
        0 if queued else 1,
    )
)
