"""Read-only snapshot of AM2R's character, transition globals, and exits.

Run under GDB while the MiSTer runner is stopped.  Names are resolved from the
loaded data.win so the audit does not depend on AM2R object/variable indices.
"""

import gdb


GLOBAL_NAMES = {
    "targetroom", "targetx", "targety", "offsetx", "offsety",
    "transitiontype", "transitionx", "transitiony", "camstartx", "camstarty",
}
EXIT_NAMES = {
    "targetroom", "targetx", "targety", "height", "direction",
    "transitionx", "transitiony", "camstartx", "camstarty",
}
CHARACTER_NAMES = {
    "state", "facing", "xVel", "yVel", "ballstate", "spiderball",
    "onSpider", "inSpider", "stickyball", "control", "cancontrol",
    "sbstate", "sbmove", "edgedl", "edgedr", "edgeul", "edgeur",
}


def arrlen(pointer):
    if not int(pointer):
        return 0
    header_type = gdb.lookup_type("stbds_array_header").pointer()
    return int((pointer.cast(header_type) - 1).dereference()["length"])


def object_name(data_win, object_index):
    if object_index < 0 or object_index >= int(data_win["objt"]["count"]):
        return "<?>"
    pointer = data_win["objt"]["objects"][object_index]["name"]
    return pointer.string() if int(pointer) else "<?>"


def make_var_names(vm):
    result = {}
    name_map = vm["varNameMap"]
    # stb_ds hash maps expose their default entry at [-1]; the array header is
    # therefore one element farther back than for a plain stb_ds array.
    name_count = int(gdb.parse_and_eval(
        "((stbds_array_header*)(g_runner->vmContext->varNameMap - 1) - 1)->length - 1"
    ))
    for index in range(name_count):
        entry = name_map[index]
        if not int(entry["key"]):
            continue
        try:
            name = entry["key"].string()
        except (gdb.MemoryError, UnicodeError):
            continue
        result[int(entry["value"])] = name
    return result


def format_rvalue(value):
    value_type = int(value["type"])
    if value_type in (2, 4):
        return str(int(value["int32"]))
    if value_type == 3:
        return str(int(value["int64"]))
    if value_type == 5:
        return "%.6f" % float(value["real"])
    if value_type == 1:
        pointer = value["string"]
        return repr(pointer.string() if int(pointer) else None)
    if value_type == 0:
        return "undefined"
    return "<type=%d>" % value_type


def selected_vars(instance, var_names, wanted):
    result = []
    table = instance["selfVars"]
    for slot in range(int(table["capacity"])):
        entry = table["entries"][slot]
        name = var_names.get(int(entry["key"]))
        if name in wanted:
            result.append((name, format_rvalue(entry["value"])))
    return sorted(result)


runner = gdb.parse_and_eval("g_runner").dereference()
vm = runner["vmContext"].dereference()
data_win = runner["dataWin"].dereference()
var_names = make_var_names(vm)

room_index = int(runner["currentRoomIndex"])
room = runner["currentRoom"]
print("ROOM_TRANSITION room=%d:%s frame=%d pending=%d instances=%d persistent=%d initialized=%d nextInstanceId=%d" % (
    room_index, room["name"].string(),
    int(runner["frameCount"]), int(runner["pendingRoom"]),
    arrlen(runner["instances"]),
    1 if bool(room["persistent"]) else 0,
    1 if bool(runner["savedRoomStates"][room_index]["initialized"]) else 0,
    int(runner["nextInstanceId"]),
))

live_ids = set()
for index in range(arrlen(runner["instances"])):
    pointer = runner["instances"][index]
    if int(pointer):
        live_ids.add(int(pointer.dereference()["instanceId"]))
id_map_entries = {}
id_map = runner["instancesById"]
if int(id_map):
    id_map_count = int(gdb.parse_and_eval(
        "((stbds_array_header*)(g_runner->instancesById - 1) - 1)->length - 1"
    ))
    for index in range(id_map_count):
        entry = id_map[index]
        id_map_entries[int(entry["key"])] = int(entry["value"])
for index in range(int(room["gameObjectCount"])):
    room_object = room["gameObjects"][index]
    room_name = object_name(data_win, int(room_object["objectDefinition"]))
    if room_name == "oGotoRoom":
        print(" ROOM_DEFINITION object=oGotoRoom id=%d x=%d y=%d creation=%d" % (
            int(room_object["instanceID"]), int(room_object["x"]),
            int(room_object["y"]), int(room_object["creationCode"]),
        ))
    if int(room_object["instanceID"]) not in live_ids:
        stale_pointer = id_map_entries.get(int(room_object["instanceID"]), 0)
        print(" ROOM_MISSING object=%s id=%d x=%d y=%d creation=%d" % (
            room_name, int(room_object["instanceID"]), int(room_object["x"]),
            int(room_object["y"]), int(room_object["creationCode"]),
        ))
        if stale_pointer:
            print("  ROOM_STALE_ID id=%d pointer=0x%x" % (
                int(room_object["instanceID"]), stale_pointer,
            ))

globals_instance = vm["globalScopeInstance"].dereference()
for name, value in selected_vars(globals_instance, var_names, GLOBAL_NAMES):
    print(" ROOM_GLOBAL name=%s value=%s" % (name, value))

for index in range(arrlen(runner["instances"])):
    pointer = runner["instances"][index]
    if not int(pointer):
        continue
    instance = pointer.dereference()
    name = object_name(data_win, int(instance["objectIndex"]))
    if name not in ("oCharacter", "oGotoRoom"):
        continue
    print(
        " ROOM_INSTANCE object=%s id=%d x=%.6f y=%.6f prev=(%.6f,%.6f) "
        "speed=(%.6f,%.6f) sprite=%d mask=%d active=%d destroyed=%d "
        "visible=%d persistent=%d" % (
            name, int(instance["instanceId"]), float(instance["x"]),
            float(instance["y"]), float(instance["xprevious"]),
            float(instance["yprevious"]), float(instance["hspeed"]),
            float(instance["vspeed"]), int(instance["spriteIndex"]),
            int(instance["maskIndex"]), 1 if bool(instance["active"]) else 0,
            1 if bool(instance["destroyed"]) else 0,
            1 if bool(instance["visible"]) else 0,
            1 if bool(instance["persistent"]) else 0,
        )
    )
    wanted = CHARACTER_NAMES if name == "oCharacter" else EXIT_NAMES
    for var_name, value in selected_vars(instance, var_names, wanted):
        print("  ROOM_VAR object=%s name=%s value=%s" % (name, var_name, value))
