"""Stage the supplied Torizo checkpoint for a deterministic real missile hit.

This mutates only the disposable, restored QA process.  Inventory, enemy
health, game code, and persistent saves are untouched; the ordinary AM2R
collision handler must still accept and damage the boss.
"""

import gdb


def array_length(pointer):
    if not int(pointer):
        return 0
    header = gdb.lookup_type("stbds_array_header").pointer()
    return int((pointer.cast(header) - 1).dereference()["length"])


def object_name(data_win, object_index):
    pointer = data_win["objt"]["objects"][object_index]["name"]
    return pointer.string() if int(pointer) else "<?>"


def place(index, x, y):
    for field, value in (
        ("x", x), ("y", y), ("xprevious", x), ("yprevious", y),
        ("hspeed", 0.0), ("vspeed", 0.0), ("speed", 0.0),
    ):
        gdb.execute("set g_runner->instances[%d]->%s=%.9g" %
                    (index, field, value))
    gdb.execute("set g_runner->instances[%d]->spatialGridDirty=1" % index)


runner = gdb.parse_and_eval("g_runner").dereference()
if int(runner["currentRoomIndex"]) != 128:
    raise gdb.GdbError("Torizo staging requires room 128")
data_win = runner["dataWin"].dereference()
character = None
torizo = None
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
    elif name == "oTorizo":
        torizo = index

if character is None or torizo is None:
    raise gdb.GdbError("live character and Torizo are required")

# At these authored floor coordinates, the horizontally fired projectile has a
# short flight while a normal jump still determines whether it clears the
# separate lower-body blocker and reaches Torizo's upper collision object.
place(torizo, 500.0, 432.0)
place(character, 590.0, 432.0)
print("TORIZO_HIT_STAGED character_index=%d torizo_index=%d" %
      (character, torizo))
