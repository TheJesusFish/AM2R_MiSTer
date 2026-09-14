"""Audit the current runner's precomputed collision-dispatch order in GDB."""

import gdb


def arrlen(expression):
    pointer = gdb.parse_and_eval(expression)
    if not int(pointer):
        return 0
    return int(gdb.parse_and_eval(
        "((stbds_array_header*)(%s) - 1)->length" % expression
    ))


runner = gdb.parse_and_eval("g_runner").dereference()
data_win = runner["dataWin"].dereference()
objects = data_win["objt"]["objects"]
object_count = int(data_win["objt"]["count"])

names = {}
for object_index in range(object_count):
    pointer = objects[object_index]["name"]
    if int(pointer):
        try:
            names[object_index] = pointer.string()
        except (gdb.MemoryError, UnicodeError):
            pass

collision_objects = runner["objectsWithAnyEventOfType"][4]
count = arrlen("g_runner->objectsWithAnyEventOfType[4]")
positions = {}
for position in range(count):
    object_index = int(collision_objects[position])
    name = names.get(object_index, "<?>")
    if name in ("oBeam", "oDoor", "oSolid", "oSolid1"):
        positions[name] = position

print("COLLISION_DISPATCH_ORDER count=%d positions=%s" % (count, positions))
for wanted_name in ("oBeam", "oDoor", "oSolid", "oSolid1"):
    if wanted_name not in positions:
        continue
    position = positions[wanted_name]
    first = max(0, position - 2)
    last = min(count, position + 3)
    print(" COLLISION_ORDER_AROUND name=%s position=%d" % (wanted_name, position))
    for nearby in range(first, last):
        object_index = int(collision_objects[nearby])
        print("  position=%d object=%d name=%s" % (
            nearby, object_index, names.get(object_index, "<?>")))

for wanted_name in ("oBeam", "oDoor"):
    object_index = next((idx for idx, name in names.items() if name == wanted_name), -1)
    if object_index < 0:
        continue
    event_list = runner["flattenedCollisionEvents"][object_index]
    event_count = int(event_list["eventCount"])
    print(" COLLISION_HANDLERS name=%s object=%d count=%d" % (
        wanted_name, object_index, event_count))
    for event_index in range(event_count):
        event = event_list["events"][event_index]
        target = int(event["targetObjectIndex"])
        print("  event=%d target=%d target_name=%s owner=%d code=%d" % (
            event_index, target, names.get(target, "<?>"),
            int(event["ownerObjectIndex"]), int(event["codeId"])))
