"""Stage Samus directly below a live Alpha for a deterministic missile hit.

This mutates only the paused QA process. It does not change inventory, health,
enemy health, or game code; the ordinary collision event remains responsible
for deciding whether the subsequent missile damages the Alpha.
"""

import gdb


def array_length(pointer):
    if not int(pointer):
        return 0
    header = gdb.lookup_type("stbds_array_header").pointer()
    return int((pointer.cast(header) - 1).dereference()["length"])


def object_name(data_win, object_index):
    if object_index < 0 or object_index >= int(data_win["objt"]["count"]):
        return "<?>"
    pointer = data_win["objt"]["objects"][object_index]["name"]
    return pointer.string() if int(pointer) else "<?>"


def place(index, x, y):
    for field, value in (
        ("x", x),
        ("y", y),
        ("xprevious", x),
        ("yprevious", y),
        ("hspeed", 0.0),
        ("vspeed", 0.0),
    ):
        gdb.execute("set g_runner->instances[%d]->%s=%.9g" % (index, field, value))


runner = gdb.parse_and_eval("g_runner").dereference()
if int(runner["currentRoomIndex"]) != 81:
    raise gdb.GdbError("Alpha staging requires room 81")
data_win = runner["dataWin"].dereference()
character = None
alpha = None
camera = None
for index in range(array_length(runner["instances"])):
    pointer = runner["instances"][index]
    if not int(pointer):
        continue
    instance = pointer.dereference()
    if not bool(instance["active"]) or bool(instance["destroyed"]):
        continue
    name = object_name(data_win, int(instance["objectIndex"]))
    if name == "oCharacter":
        character = index
    elif name == "oMAlpha":
        alpha = index
    elif name == "oCamera":
        camera = index

if character is None or alpha is None:
    raise gdb.GdbError("live character and Alpha are required")

# The authored enemy collision mask's vulnerable underside now sits directly
# above Samus's muzzle, leaving a short vertical missile flight.
place(character, 550.0, 150.0)
place(alpha, 550.0, 122.0)
if camera is not None:
    place(camera, 480.0, 120.0)
print(
    "ALPHA_HIT_STAGED character_index=%d alpha_index=%d camera_index=%s"
    % (character, alpha, str(camera) if camera is not None else "none")
)
