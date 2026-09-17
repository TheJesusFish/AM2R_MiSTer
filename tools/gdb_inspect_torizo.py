"""Report live Torizo combat state without changing the game process."""

import gdb


RVALUE_REAL = 5


def array_length(pointer):
    if not int(pointer):
        return 0
    header = gdb.lookup_type("stbds_array_header").pointer()
    return int((pointer.cast(header) - 1).dereference()["length"])


def hash_map_length(pointer):
    header = gdb.lookup_type("stbds_array_header").pointer()
    return int(((pointer - 1).cast(header) - 1).dereference()["length"] - 1)


def object_name(data_win, object_index):
    if object_index < 0 or object_index >= int(data_win["objt"]["count"]):
        return "<?>"
    pointer = data_win["objt"]["objects"][object_index]["name"]
    return pointer.string() if int(pointer) else "<?>"


runner = gdb.parse_and_eval("g_runner").dereference()
vm = runner["vmContext"].dereference()
names = {}
name_map = vm["varNameMap"]
for index in range(hash_map_length(name_map)):
    entry = name_map[index]
    if not int(entry["key"]):
        continue
    try:
        names[int(entry["value"])] = entry["key"].string()
    except (gdb.MemoryError, UnicodeError):
        pass

wanted = {
    "myhealth", "flashing", "fxtimer", "state", "statetime", "canbehit",
    "facing", "missiles", "maxmissiles", "weapon", "currentweapon",
    "opmslstyle", "armmsl", "samushealth",
}
data_win = runner["dataWin"].dereference()
print("TORIZO_FRAME room=%d frame=%d frame_address=0x%x" %
      (int(runner["currentRoomIndex"]), int(runner["frameCount"]),
       int(runner["frameCount"].address)))
for index in range(array_length(runner["instances"])):
    pointer = runner["instances"][index]
    if not int(pointer):
        continue
    instance = pointer.dereference()
    name = object_name(data_win, int(instance["objectIndex"]))
    if name not in ("oTorizo", "oTorizoBottom", "oCharacter", "oMissile"):
        continue
    values = {}
    table = instance["selfVars"]
    for slot in range(int(table["capacity"])):
        entry = table["entries"][slot]
        variable = names.get(int(entry["key"]))
        value = entry["value"]
        if variable in wanted and int(value["type"]) == RVALUE_REAL:
            values[variable] = float(value["real"])
            if name == "oTorizo" and variable == "myhealth":
                print("TORIZO_HEALTH_ADDRESS rvalue=0x%x real=0x%x" %
                      (int(value.address), int(value["real"].address)))
    print(
        "TORIZO_OBJECT name=%s index=%d id=%d x=%.3f y=%.3f "
        "speed=(%.3f,%.3f) active=%d destroyed=%d sprite=%d mask=%d %s"
        % (
            name, index, int(instance["instanceId"]), float(instance["x"]),
            float(instance["y"]), float(instance["hspeed"]),
            float(instance["vspeed"]), int(instance["active"]),
            int(instance["destroyed"]), int(instance["spriteIndex"]),
            int(instance["maskIndex"]),
            " ".join("%s=%.3f" % item for item in sorted(values.items())),
        )
    )

    for sprite_label, sprite_index in (
        ("sprite", int(instance["spriteIndex"])),
        ("mask", int(instance["maskIndex"])),
    ):
        if sprite_index < 0 or sprite_index >= int(data_win["sprt"]["count"]):
            continue
        sprite = data_win["sprt"]["sprites"][sprite_index]
        print(
            "TORIZO_SPRITE owner=%s kind=%s index=%d name=%s size=%dx%d "
            "origin=(%d,%d) margin=(%d,%d,%d,%d)"
            % (
                name, sprite_label, sprite_index,
                sprite["name"].string() if int(sprite["name"]) else "<?>",
                int(sprite["width"]), int(sprite["height"]),
                int(sprite["originX"]), int(sprite["originY"]),
                int(sprite["marginLeft"]), int(sprite["marginTop"]),
                int(sprite["marginRight"]), int(sprite["marginBottom"]),
            )
        )

global_values = {}
table = vm["globalScopeInstance"].dereference()["selfVars"]
for slot in range(int(table["capacity"])):
    entry = table["entries"][slot]
    variable = names.get(int(entry["key"]))
    value = entry["value"]
    if variable in wanted and int(value["type"]) == RVALUE_REAL:
        global_values[variable] = float(value["real"])
print("TORIZO_GLOBAL " +
      " ".join("%s=%.3f" % item for item in sorted(global_values.items())))
