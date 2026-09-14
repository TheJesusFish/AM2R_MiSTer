"""Place the live AM2R character at a controlled QA coordinate.

Define GDB convenience variables ``$am2r_room``, ``$am2r_x``, and ``$am2r_y``
before sourcing this script.  It refuses to act in a different room and only
changes transient character/camera position and velocity in the running game.
"""

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


runner = gdb.parse_and_eval("g_runner").dereference()
expected_room = int(gdb.parse_and_eval("$am2r_room"))
target_x = float(gdb.parse_and_eval("$am2r_x"))
target_y = float(gdb.parse_and_eval("$am2r_y"))
actual_room = int(runner["currentRoomIndex"])
if actual_room != expected_room:
    raise gdb.GdbError(
        "refusing character placement: expected room %d, found %d"
        % (expected_room, actual_room)
    )

data_win = runner["dataWin"].dereference()
character_index = None
camera_index = None
for index in range(array_length(runner["instances"])):
    pointer = runner["instances"][index]
    if not int(pointer):
        continue
    instance = pointer.dereference()
    name = object_name(data_win, int(instance["objectIndex"]))
    if name == "oCharacter" and bool(instance["active"]) and not bool(instance["destroyed"]):
        character_index = index
    elif name == "oCamera" and bool(instance["active"]) and not bool(instance["destroyed"]):
        camera_index = index

if character_index is None:
    raise gdb.GdbError("active oCharacter not found")

for field, value in (
    ("x", target_x),
    ("y", target_y),
    ("xprevious", target_x),
    ("yprevious", target_y),
    ("hspeed", 0.0),
    ("vspeed", 0.0),
):
    gdb.execute(
        "set g_runner->instances[%d]->%s=%.9g" % (character_index, field, value)
    )

if camera_index is not None:
    # Let the ordinary camera step settle from a nearby point instead of
    # forcing a view coordinate copied from a potentially different state.
    for field, value in (
        ("x", target_x),
        ("y", target_y - 16.0),
        ("xprevious", target_x),
        ("yprevious", target_y - 16.0),
        ("hspeed", 0.0),
        ("vspeed", 0.0),
    ):
        gdb.execute(
            "set g_runner->instances[%d]->%s=%.9g" % (camera_index, field, value)
        )

print(
    "CHARACTER_PLACED room=%d character_index=%d camera_index=%s x=%.6f y=%.6f"
    % (
        actual_room,
        character_index,
        str(camera_index) if camera_index is not None else "none",
        target_x,
        target_y,
    )
)
