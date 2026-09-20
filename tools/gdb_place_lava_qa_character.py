"""Place the current-runner character at the attached slot-1 lava ledge.

This is a hardware-QA-only debugger action.  It does not modify the runtime,
ordinary save, or checkpoint on disk.
"""

import struct

import gdb


def array_length(pointer):
    if not int(pointer):
        return 0
    header_type = gdb.lookup_type("stbds_array_header").pointer()
    return int((pointer.cast(header_type) - 1).dereference()["length"])


inferior = gdb.selected_inferior()


def locate_runner_pointer():
    """Find the stripped production Runner without relying on link addresses."""
    runner_type = gdb.lookup_type("Runner").pointer()
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
            return address, pointer_value
        except (gdb.error, gdb.MemoryError):
            continue
    raise gdb.GdbError("could not locate the live AM2R Runner")


runner_slot, runner_pointer = locate_runner_pointer()
runner = gdb.Value(runner_pointer).cast(gdb.lookup_type("Runner").pointer()).dereference()
data_win = runner["dataWin"].dereference()


def qa_coordinate(name, fallback):
    try:
        value = gdb.parse_and_eval("$" + name)
        if value.type.code != gdb.TYPE_CODE_VOID:
            return float(value)
    except gdb.error:
        pass
    return fallback


qa_x = qa_coordinate("lava_qa_x", 1468.0)
qa_y = qa_coordinate("lava_qa_y", 160.0)
instances = runner["instances"]
character = None
character_address = 0
for index in range(array_length(instances)):
    pointer = instances[index]
    if not int(pointer):
        continue
    instance = pointer.dereference()
    object_index = int(instance["objectIndex"])
    if object_index < 0 or object_index >= int(data_win["objt"]["count"]):
        continue
    name_pointer = data_win["objt"]["objects"][object_index]["name"]
    name = name_pointer.string() if int(name_pointer) else ""
    if name == "oCharacter" and bool(instance["active"]) and not bool(instance["destroyed"]):
        character = instance
        character_address = int(pointer)
        break

if character is None:
    raise gdb.GdbError("active oCharacter was not found")

qa_variable_ids = {"invincible": set(), "myhealth": set()}
for index in range(int(data_win["vari"]["variableCount"])):
    variable = data_win["vari"]["variables"][index]
    name_pointer = variable["name"]
    if int(name_pointer):
        name = name_pointer.string()
        if name in qa_variable_ids:
            qa_variable_ids[name].add(int(variable["varID"]))

invincible_entry_address = 0
health_entry_address = 0
self_vars = character["selfVars"]
entries = self_vars["entries"]
for index in range(int(self_vars["capacity"])):
    entry = entries[index]
    if int(entry["key"]) in qa_variable_ids["invincible"]:
        invincible_entry_address = int(entries) + index * int(entry.type.sizeof)
    if int(entry["key"]) in qa_variable_ids["myhealth"]:
        health_entry_address = int(entries) + index * int(entry.type.sizeof)
if not invincible_entry_address:
    raise gdb.GdbError("oCharacter has no existing invincible variable slot")
gdb.execute(
    "set var ((IntRValueEntry*)0x%x)->value.int32 = 1" % invincible_entry_address,
    to_string=True,
)
gdb.execute(
    "set var ((IntRValueEntry*)0x%x)->value.type = 4" % invincible_entry_address,
    to_string=True,
)
gdb.execute(
    "set var ((IntRValueEntry*)0x%x)->value.ownsReference = 0" % invincible_entry_address,
    to_string=True,
)
gdb.execute(
    "set var ((IntRValueEntry*)0x%x)->value.gmlStackType = 4" % invincible_entry_address,
    to_string=True,
)
if health_entry_address:
    gdb.execute(
        "set var ((IntRValueEntry*)0x%x)->value.real = 9999.0" % health_entry_address,
        to_string=True,
    )
    gdb.execute(
        "set var ((IntRValueEntry*)0x%x)->value.type = 5" % health_entry_address,
        to_string=True,
    )
    gdb.execute(
        "set var ((IntRValueEntry*)0x%x)->value.ownsReference = 0" % health_entry_address,
        to_string=True,
    )
    gdb.execute(
        "set var ((IntRValueEntry*)0x%x)->value.gmlStackType = 0" % health_entry_address,
        to_string=True,
    )

for field, value in (
    ("x", qa_x),
    ("y", qa_y),
    ("xprevious", qa_x),
    ("yprevious", qa_y),
    ("xstart", qa_x),
    ("ystart", qa_y),
    ("speed", 0.0),
    ("hspeed", 0.0),
    ("vspeed", 0.0),
):
    gdb.execute(
        "set var ((Instance*)0x%x)->%s = %.9g" % (character_address, field, value),
        to_string=True,
    )
gdb.execute(
    "set var ((Instance*)0x%x)->spatialGridDirty = 1" % character_address,
    to_string=True,
)

dirty_instances = runner["spatialGrid"].dereference()["dirtyInstances"]
if not int(dirty_instances):
    raise gdb.GdbError("spatial-grid dirty queue is not allocated")
dirty_header = (dirty_instances.cast(gdb.lookup_type("stbds_array_header").pointer()) - 1)
dirty_length = int(dirty_header.dereference()["length"])
dirty_capacity = int(dirty_header.dereference()["capacity"])
character_id = int(character["instanceId"])
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
    "LAVA_QA_PLACED slot=0x%x room=%d:%s character=%d x=%.1f y=%.1f invincible=1 myhealth=%s"
    % (
        runner_slot,
        int(runner["currentRoomIndex"]),
        room["name"].string() if int(room["name"]) else "<?>",
        int(character["instanceId"]),
        float(character["x"]),
        float(character["y"]),
        "9999" if health_entry_address else "n/a",
    )
)
