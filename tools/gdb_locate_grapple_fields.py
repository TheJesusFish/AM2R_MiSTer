"""Write live AM2R character field addresses for a read-only /proc sampler."""

import json
import gdb


CHARACTER_OBJECT = 266
CHARACTER_VARIABLES = (
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
)
GLOBAL_VARIABLES = ("powergrip", "morphball")


def array_length(expression):
    return int(gdb.parse_and_eval(
        "((stbds_array_header*)(%s) - 1)->length" % expression
    ))


def find_var_ids(vm, wanted):
    name_map = vm["varNameMap"]
    count = array_length("g_runner->vmContext->varNameMap - 1") - 1
    result = {}
    for index in range(count):
        entry = name_map[index]
        if not int(entry["key"]):
            continue
        try:
            name = entry["key"].string()
        except (gdb.MemoryError, UnicodeError):
            continue
        if name in wanted:
            result[name] = int(entry["value"])
    return result


def find_rvalue(instance, variable_id):
    table = instance["selfVars"]
    for slot in range(int(table["capacity"])):
        entry = table["entries"][slot]
        if int(entry["key"]) == variable_id:
            return entry["value"]
    raise RuntimeError("variable id %d is absent" % variable_id)


runner_pointer = gdb.parse_and_eval("g_runner")
runner = runner_pointer.dereference()
vm = runner["vmContext"].dereference()
character_pointer = runner["instancesByObject"][CHARACTER_OBJECT][0]
character = character_pointer.dereference()
global_instance = vm["globalScopeInstance"].dereference()
wanted = set(CHARACTER_VARIABLES) | set(GLOBAL_VARIABLES)
var_ids = find_var_ids(vm, wanted)

fields = {
    "frame": {
        "address": int(runner["frameCount"].address),
        "format": "i",
    },
    "x": {
        "address": int(character["x"].address),
        "format": "f",
    },
    "y": {
        "address": int(character["y"].address),
        "format": "f",
    },
}

for name in CHARACTER_VARIABLES:
    value = find_rvalue(character, var_ids[name])
    fields[name] = {
        "address": int(value["real"].address),
        "format": "d",
        "type_address": int(value["type"].address),
    }

for name in GLOBAL_VARIABLES:
    value = find_rvalue(global_instance, var_ids[name])
    fields[name] = {
        "address": int(value["real"].address),
        "format": "d",
        "type_address": int(value["type"].address),
    }

document = {
    "pid": int(gdb.selected_inferior().pid),
    "runner": int(runner_pointer),
    "character": int(character_pointer),
    "room": int(runner["currentRoomIndex"]),
    "fields": fields,
}
with open("/tmp/am2r-grapple-addresses.json", "w", encoding="ascii") as output:
    json.dump(document, output, sort_keys=True, indent=2)
    output.write("\n")
print("GRAPPLE_ADDRESSES_WRITTEN pid=%d character=0x%x" % (
    document["pid"], document["character"]
))
