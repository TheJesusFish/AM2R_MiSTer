"""Read AM2R boss/player variables from a process attached by GDB.

This module is sourced from GDB after loading the exact runner ELF.  It only
reads inferior memory; it does not call code in or mutate the game process.
"""

import gdb


WANTED_VARIABLES = {
    "myhealth",
    "starthealth",
    "canbehit",
    "flashing",
    "myid",
    "dead",
    "damage",
    "missiles",
    "maxmissiles",
    "weapon",
    "opmslstyle",
    "armmsl",
    "state",
    "facing",
    "xVel",
    "yVel",
    "kRight",
    "kLeft",
    "kJump",
    "kJumpPushedSteps",
    "dash",
    "canclimb",
    "powergrip",
    "morphball",
    "kSelect",
    "kSelectPressed",
    "kSelectPushedSteps",
    "kSelectReleased",
    "health",
    "maxhealth",
    "x",
    "y",
}


def scalar_rvalue(value):
    value_type = int(value["type"])
    if value_type in (2, 4):
        return value_type, int(value["int32"])
    if value_type == 3:
        return value_type, int(value["int64"])
    if value_type == 5:
        return value_type, float(value["real"])
    if value_type == 1:
        pointer = value["string"]
        return value_type, pointer.string() if int(pointer) else None
    return value_type, "<non-scalar>"


runner = gdb.parse_and_eval("g_runner").dereference()
vm = runner["vmContext"].dereference()
name_map = vm["varNameMap"]
name_count = int(
    gdb.parse_and_eval(
        "((stbds_array_header*)(g_runner->vmContext->varNameMap - 1) - 1)->length - 1"
    )
)

var_ids = {}
for index in range(name_count):
    entry = name_map[index]
    key_pointer = entry["key"]
    if not int(key_pointer):
        continue
    try:
        name = key_pointer.string()
    except (gdb.MemoryError, UnicodeError):
        continue
    lowered = name.lower()
    if name in WANTED_VARIABLES or any(fragment in lowered for fragment in (
        "missile", "weapon", "ammo", "health", "shoot", "fire", "aim",
    )):
        var_ids[name] = int(entry["value"])

print("FRAME room=%d frame=%d vars=%d" % (
    int(runner["currentRoomIndex"]), int(runner["frameCount"]), name_count
))
print("VAR_IDS " + " ".join("%s=%d" % item for item in sorted(var_ids.items())))

object_names = {}
all_object_names = {}
data_win = runner["dataWin"].dereference()
object_table = data_win["objt"]
for object_index in range(int(object_table["count"])):
    name_pointer = object_table["objects"][object_index]["name"]
    if not int(name_pointer):
        continue
    try:
        name = name_pointer.string()
    except (gdb.MemoryError, UnicodeError):
        continue
    all_object_names[object_index] = name
    lowered = name.lower()
    if any(fragment in lowered for fragment in (
        "player", "samus", "control", "missile", "beam", "shot",
        "bullet", "malpha", "hud",
    )):
        object_names[name] = object_index
print("OBJECT_IDS " + " ".join("%s=%d" % item for item in sorted(object_names.items())))

instance_count = int(
    gdb.parse_and_eval("((stbds_array_header*)g_runner->instances - 1)->length")
)
print("ACTIVE_INSTANCES count=%d" % instance_count)
for instance_number in range(instance_count):
    instance = runner["instances"][instance_number].dereference()
    if not int(instance["active"]) or int(instance["destroyed"]):
        continue
    object_index = int(instance["objectIndex"])
    print(" ACTIVE object=%s index=%d id=%d x=%s y=%s sprite=%d" % (
        all_object_names.get(object_index, "<?>"), object_index,
        int(instance["instanceId"]), float(instance["x"]), float(instance["y"]),
        int(instance["spriteIndex"]),
    ))


def dump_object(object_index, label):
    object_array = runner["instancesByObject"][object_index]
    count = int(
        gdb.parse_and_eval(
            "((stbds_array_header*)g_runner->instancesByObject[%d] - 1)->length"
            % object_index
        )
    ) if int(object_array) else 0
    print("OBJECT %s index=%d count=%d" % (label, object_index, count))
    for object_number in range(count):
        instance = object_array[object_number].dereference()
        fields = {
            "instanceId": int(instance["instanceId"]),
            "x": float(instance["x"]),
            "y": float(instance["y"]),
            "spriteIndex": int(instance["spriteIndex"]),
            "imageIndex": float(instance["imageIndex"]),
            "imageAngle": float(instance["imageAngle"]),
            "imageXscale": float(instance["imageXscale"]),
            "imageYscale": float(instance["imageYscale"]),
            "maskIndex": int(instance["maskIndex"]),
            "visible": int(instance["visible"]),
            "active": int(instance["active"]),
            "destroyed": int(instance["destroyed"]),
        }
        print(" INSTANCE " + " ".join("%s=%s" % item for item in fields.items()))
        values = {}
        self_vars = instance["selfVars"]
        entries = self_vars["entries"]
        capacity = int(self_vars["capacity"])
        for slot in range(capacity):
            slot_entry = entries[slot]
            variable_id = int(slot_entry["key"])
            if variable_id == -1:
                continue
            for name, wanted_id in var_ids.items():
                if variable_id == wanted_id:
                    values[name] = scalar_rvalue(slot_entry["value"])
        for name in sorted(values):
            value_type, value = values[name]
            print("  VAR %s type=%d value=%s" % (name, value_type, value))


def dump_instance_pointer(instance_pointer, label):
    instance = instance_pointer.dereference()
    print("OBJECT %s index=%d count=1" % (label, int(instance["objectIndex"])))
    entries = instance["selfVars"]["entries"]
    capacity = int(instance["selfVars"]["capacity"])
    values = {}
    for slot in range(capacity):
        slot_entry = entries[slot]
        variable_id = int(slot_entry["key"])
        if variable_id == -1:
            continue
        for name, wanted_id in var_ids.items():
            if variable_id == wanted_id:
                values[name] = scalar_rvalue(slot_entry["value"])
    for name in sorted(values):
        value_type, value = values[name]
        print("  VAR %s type=%d value=%s" % (name, value_type, value))


dump_object(494, "oMAlpha")
dump_object(438, "oMissile")
dump_object(266, "oCharacter")
dump_instance_pointer(vm["globalScopeInstance"], "global")


def dump_sprite(sprite_index, label):
    sprite = data_win["sprt"]["sprites"][sprite_index]
    print(
        "SPRITE %s index=%d name=%s size=%dx%d origin=%d,%d "
        "margin=%d,%d,%d,%d bboxMode=%d sepMasks=%d maskSize=%dx%d "
        "maskOffset=%d,%d maskCount=%d"
        % (
            label, sprite_index, sprite["name"].string(),
            int(sprite["width"]), int(sprite["height"]),
            int(sprite["originX"]), int(sprite["originY"]),
            int(sprite["marginLeft"]), int(sprite["marginTop"]),
            int(sprite["marginRight"]), int(sprite["marginBottom"]),
            int(sprite["bboxMode"]), int(sprite["sepMasks"]),
            int(sprite["maskWidth"]), int(sprite["maskHeight"]),
            int(sprite["maskOffsetX"]), int(sprite["maskOffsetY"]),
            int(sprite["maskCount"]),
        )
    )
    mask_count = int(sprite["maskCount"])
    if not mask_count or not int(sprite["masks"]):
        return
    width = int(sprite["maskWidth"])
    height = int(sprite["maskHeight"])
    row_bytes = (width + 7) // 8
    inferior = gdb.selected_inferior()
    for frame in range(mask_count):
        pointer = sprite["masks"][frame]
        if not int(pointer):
            print(" MASK frame=%d missing" % frame)
            continue
        raw = bytes(inferior.read_memory(int(pointer), row_bytes * height))
        points = []
        for y in range(height):
            for x in range(width):
                if raw[y * row_bytes + (x >> 3)] & (1 << (7 - (x & 7))):
                    points.append((x, y))
        if points:
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            print(" MASK frame=%d pixels=%d bounds=%d,%d,%d,%d" % (
                frame, len(points), min(xs), min(ys), max(xs), max(ys)
            ))
        else:
            print(" MASK frame=%d pixels=0" % frame)


dump_sprite(567, "alpha-body-active")
dump_sprite(574, "alpha-shell-active")
missile_sprite = int(data_win["objt"]["objects"][438]["spriteId"])
dump_sprite(missile_sprite, "missile-default")


def dump_collision_events(object_index, label):
    event_list = runner["flattenedCollisionEvents"][object_index]
    event_count = int(event_list["eventCount"])
    print("COLLISION_EVENTS %s index=%d count=%d" % (
        label, object_index, event_count
    ))
    for event_index in range(event_count):
        event = event_list["events"][event_index]
        code_id = int(event["codeId"])
        code_name = "<?>"
        if code_id >= 0:
            code_pointer = data_win["code"]["entries"][code_id]["name"]
            if int(code_pointer):
                try:
                    code_name = code_pointer.string()
                except (gdb.MemoryError, UnicodeError):
                    pass
        print(" COLLISION_EVENT target=%d code=%d owner=%d name=%s" % (
            int(event["targetObjectIndex"]), code_id,
            int(event["ownerObjectIndex"]), code_name,
        ))


dump_collision_events(438, "oMissile")
dump_collision_events(494, "oMAlpha")
for object_name in ("oControl", "oPlayer", "oPlayerControl", "oSamus"):
    if object_name in object_names:
        dump_object(object_names[object_name], object_name)
