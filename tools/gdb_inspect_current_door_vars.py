"""Read the current-build AM2R save-room door and projectile state in GDB."""

import gdb


WANTED = {
    "open", "lock", "block", "event", "stayopen", "showlock", "lockdelay",
    "facing", "state", "kShoot", "kShootPressed", "kShootPushedSteps",
    "wbeam", "ibeam", "sbeam", "pbeam", "chargebeam", "maindir",
    "dohit", "time", "speed", "direction",
}


def scalar(value):
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
data_win = runner["dataWin"].dereference()
name_map = vm["varNameMap"]
name_count = int(gdb.parse_and_eval(
    "((stbds_array_header*)(g_runner->vmContext->varNameMap - 1) - 1)->length - 1"
))
var_ids = {}
for index in range(name_count):
    entry = name_map[index]
    if not int(entry["key"]):
        continue
    try:
        name = entry["key"].string()
    except (gdb.MemoryError, UnicodeError):
        continue
    if name in WANTED:
        var_ids[name] = int(entry["value"])

object_indices = {}
for object_index in range(int(data_win["objt"]["count"])):
    pointer = data_win["objt"]["objects"][object_index]["name"]
    if not int(pointer):
        continue
    try:
        name = pointer.string()
    except (gdb.MemoryError, UnicodeError):
        continue
    if name in ("oDoor", "oBeam", "oMissile", "oMissileExpl", "oCharacter"):
        object_indices[name] = object_index


def array_length(expression):
    return int(gdb.parse_and_eval(
        "((stbds_array_header*)(%s) - 1)->length" % expression
    ))


def dump_instance(instance, label):
    print(
        "CURRENT_DOOR_INSTANCE label=%s id=%d object=%d x=%.3f y=%.3f "
        "active=%d destroyed=%d sprite=%d image=%.3f" % (
            label, int(instance["instanceId"]), int(instance["objectIndex"]),
            float(instance["x"]), float(instance["y"]),
            int(instance["active"]), int(instance["destroyed"]),
            int(instance["spriteIndex"]), float(instance["imageIndex"]),
        )
    )
    wanted_by_id = {value: key for key, value in var_ids.items()}
    variables = {}
    table = instance["selfVars"]
    for slot in range(int(table["capacity"])):
        entry = table["entries"][slot]
        name = wanted_by_id.get(int(entry["key"]))
        if name is not None:
            variables[name] = scalar(entry["value"])
    for name in sorted(variables):
        value_type, value = variables[name]
        print(" CURRENT_DOOR_VAR label=%s name=%s type=%d value=%s" % (
            label, name, value_type, value
        ))


print("CURRENT_DOOR_AUDIT room=%d frame=%d vars=%d ids=%s" % (
    int(runner["currentRoomIndex"]), int(runner["frameCount"]), name_count,
    object_indices,
))
for name, object_index in sorted(object_indices.items()):
    bucket = runner["instancesByObject"][object_index]
    count = array_length("g_runner->instancesByObject[%d]" % object_index) if int(bucket) else 0
    print("CURRENT_DOOR_OBJECT name=%s index=%d count=%d" % (
        name, object_index, count
    ))
    for index in range(count):
        dump_instance(bucket[index].dereference(), "%s[%d]" % (name, index))
