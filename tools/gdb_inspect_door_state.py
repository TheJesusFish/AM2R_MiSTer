"""Read-only AM2R door/projectile state inspection for GDB.

The script finds objects by their data.win names rather than hard-coded object
indices.  It reports the character, nearby doors and their blocker instances,
active beam/missile objects, and each instance's spatial-grid membership.
"""

import math
import gdb


INTERESTING_VARS = {
    "block", "open", "lock", "event", "stayopen", "showlock", "lockdelay",
    "facing", "state", "kShoot", "kShootPushedSteps", "weapon", "wbeam",
    "ibeam", "sbeam", "pbeam", "chargebeam", "maindir", "direction",
    "speed", "dohit", "time", "smissile", "damage",
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
    for index in range(arrlen(name_map)):
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
    # Butterscotch's numeric RValues use the real member for the game variables
    # inspected here.  Include the raw type so unexpected representations remain
    # visible rather than being silently misread.
    try:
        number = float(value["real"])
        return "type=%d value=%.6g" % (value_type, number)
    except gdb.error:
        return "type=%d" % value_type


def selected_vars(instance, var_names):
    result = []
    table = instance["selfVars"]
    for slot in range(int(table["capacity"])):
        entry = table["entries"][slot]
        key = int(entry["key"])
        name = var_names.get(key)
        if name in INTERESTING_VARS:
            result.append((name, format_rvalue(entry["value"])))
    return sorted(result)


try:
    runner_pointer = gdb.parse_and_eval("$am2r_runner_override")
    if runner_pointer.type.code == gdb.TYPE_CODE_VOID or not int(runner_pointer):
        runner_pointer = gdb.parse_and_eval("g_runner")
except gdb.error:
    runner_pointer = gdb.parse_and_eval("g_runner")
runner = runner_pointer.cast(gdb.lookup_type("Runner").pointer()).dereference()
data_win = runner["dataWin"].dereference()
instances = []
character = None
for index in range(arrlen(runner["instances"])):
    pointer = runner["instances"][index]
    if not int(pointer):
        continue
    instance = pointer.dereference()
    if not bool(instance["active"]) or bool(instance["destroyed"]):
        continue
    name = object_name(data_win, int(instance["objectIndex"]))
    item = (pointer, instance, name)
    instances.append(item)
    if name == "oCharacter":
        character = item

if character is None:
    raise RuntimeError("active oCharacter not found")

char_x = float(character[1]["x"])
char_y = float(character[1]["y"])
print("DOOR_AUDIT room=%d frame=%d character=%d x=%.3f y=%.3f instances=%d" % (
    int(runner["currentRoomIndex"]), int(runner["frameCount"]),
    int(character[1]["instanceId"]), char_x, char_y, len(instances),
))

interesting = []
for pointer, instance, name in instances:
    distance = math.hypot(float(instance["x"]) - char_x, float(instance["y"]) - char_y)
    if True:
        interesting.append((distance, pointer, instance, name))

for distance, pointer, instance, name in sorted(interesting, key=lambda item: item[0]):
    print(
        "DOOR_INSTANCE id=%d object=%d:%s x=%.3f y=%.3f distance=%.3f "
        "active=%d sprite=%d mask=%d solid=%d" % (
            int(instance["instanceId"]), int(instance["objectIndex"]), name,
            float(instance["x"]), float(instance["y"]), distance,
            1 if bool(instance["active"]) else 0,
            int(instance["spriteIndex"]), int(instance["maskIndex"]),
            1 if bool(instance["solid"]) else 0,
        )
    )
