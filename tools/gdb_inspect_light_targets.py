"""Read-only inventory of instances visited by AM2R's lighting with-blocks."""

import struct

import gdb


TARGET_NAMES = (
    "oLight", "oBeam", "oMissile", "oBomb", "oBomb2", "oPickup",
    "oMGammaElec", "oGlowPlant1", "oSpikePlant", "oPincherFly",
    "oA3LabLight", "oA3LabDoor", "oFXAnimSpark", "oLightBug",
    "oChargeBeamSpark1", "oItemBall", "oItem", "oDoor",
    "oMOmegaFlame", "oMOmega_Projectile", "oA8Lamp", "oA8RedLight",
    "oA6Dust", "oA8RedLightFX", "oGenesisAcid", "oGenesisSlashProj",
)


def array_length(pointer):
    if not int(pointer):
        return 0
    header = gdb.lookup_type("stbds_array_header").pointer()
    return int((pointer.cast(header) - 1).dereference()["length"])


def locate_runner():
    inferior = gdb.selected_inferior()
    runner_type = gdb.lookup_type("Runner").pointer()
    for address in range(0x001CF000, 0x00220000, 4):
        try:
            pointer_value = struct.unpack(
                "<I", bytes(inferior.read_memory(address, 4)))[0]
            runner = gdb.Value(pointer_value).cast(runner_type).dereference()
            data_win = runner["dataWin"].dereference()
            if not (0 <= int(runner["currentRoomIndex"]) < 1000):
                continue
            if not (100 <= int(data_win["objt"]["count"]) < 10000):
                continue
            if not (0 < int(runner["frameCount"]) < 100000000):
                continue
            return runner
        except (gdb.error, gdb.MemoryError, struct.error):
            continue
    raise gdb.GdbError("could not locate restored Runner")


runner = locate_runner()
data_win = runner["dataWin"].dereference()
objects = data_win["objt"]["objects"]
object_count = int(data_win["objt"]["count"])
name_to_index = {}
for index in range(object_count):
    pointer = objects[index]["name"]
    if int(pointer):
        try:
            name_to_index[pointer.string()] = index
        except (gdb.MemoryError, UnicodeError):
            pass


def is_descendant(object_index, target_index):
    depth = 0
    while 0 <= object_index < object_count and depth < 32:
        if object_index == target_index:
            return True
        object_index = int(objects[object_index]["parentId"])
        depth += 1
    return False


print(
    "LIGHT_ROOM room=%d frame=%d instances=%d"
    % (
        int(runner["currentRoomIndex"]), int(runner["frameCount"]),
        array_length(runner["instances"]),
    )
)
for target_name in TARGET_NAMES:
    target_index = name_to_index.get(target_name, -1)
    matches = []
    if target_index >= 0:
        for slot in range(array_length(runner["instances"])):
            pointer = runner["instances"][slot]
            if not int(pointer):
                continue
            instance = pointer.dereference()
            if not bool(instance["active"]):
                continue
            object_index = int(instance["objectIndex"])
            if is_descendant(object_index, target_index):
                name_pointer = objects[object_index]["name"]
                concrete = name_pointer.string() if int(name_pointer) else "<?>"
                matches.append(
                    "%s#%d(%.1f,%.1f,spr=%d,sub=%.2f,a=%.3f)"
                    % (
                        concrete, int(instance["instanceId"]),
                        float(instance["x"]), float(instance["y"]),
                        int(instance["spriteIndex"]), float(instance["imageIndex"]),
                        float(instance["imageAlpha"]),
                    )
                )
    print(
        "LIGHT_TARGET object=%s index=%d count=%d %s"
        % (target_name, target_index, len(matches), " ".join(matches))
    )
