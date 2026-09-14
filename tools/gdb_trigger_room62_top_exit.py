"""Position the live AM2R character on room 62's top-exit trigger.

This is a hardware regression probe: it changes only the running process and
uses the ordinary oGotoRoom End Step event to perform the transition.
"""

import gdb


def arrlen(pointer):
    if not int(pointer):
        return 0
    header_type = gdb.lookup_type("stbds_array_header").pointer()
    return int((pointer.cast(header_type) - 1).dereference()["length"])


runner = gdb.parse_and_eval("g_runner").dereference()
if int(runner["currentRoomIndex"]) != 62:
    raise gdb.GdbError("room 62 is not active")

character_index = None
top_exit_index = None
data_win = runner["dataWin"].dereference()
for index in range(arrlen(runner["instances"])):
    pointer = runner["instances"][index]
    if not int(pointer):
        continue
    instance = pointer.dereference()
    if int(instance["instanceId"]) == 108036:
        top_exit_index = index
    object_index = int(instance["objectIndex"])
    if 0 <= object_index < int(data_win["objt"]["count"]):
        name_pointer = data_win["objt"]["objects"][object_index]["name"]
        if int(name_pointer) and name_pointer.string() == "oCharacter":
            character_index = index

if character_index is None or top_exit_index is None:
    raise gdb.GdbError("character or room-62 top exit is missing")

gdb.execute("set g_runner->instances[%d]->active=1" % top_exit_index)
gdb.execute("set g_runner->instances[%d]->active=1" % character_index)
gdb.execute("set g_runner->instances[%d]->visible=1" % character_index)
for field, value in (
    ("x", 150.0), ("y", 0.0),
    ("xprevious", 150.0), ("yprevious", 0.0),
    ("hspeed", 0.0), ("vspeed", 0.0),
):
    gdb.execute("set g_runner->instances[%d]->%s=%s" %
                (character_index, field, value))
print("ROOM62_TOP_EXIT_ARMED character_index=%d character=(150,0) "
      "exit_index=%d exit=108036 active=1" %
      (character_index, top_exit_index))
