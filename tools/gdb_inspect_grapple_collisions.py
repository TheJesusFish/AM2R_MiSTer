"""Inspect the exact spatial-grid inputs to AM2R's ledge point probes.

Run only while the supplied-state runner is already stopped.  The script reads
the spatial grid and the complete active-instance list, allowing missing-grid
entries to be distinguished from bounding-box or inheritance errors.
"""

import math
import gdb


CHARACTER_OBJECT = 266
SOLID_OBJECT = 267
MOVING_SOLID_OBJECT = 268


def arrlen(pointer):
    if not int(pointer):
        return 0
    header_type = gdb.lookup_type("stbds_array_header").pointer()
    return int((pointer.cast(header_type) - 1).dereference()["length"])


def object_name(data_win, object_index):
    pointer = data_win["objt"]["objects"][object_index]["name"]
    return pointer.string() if int(pointer) else "<?>"


def matches_target(data_win, instance, target):
    current = int(instance["objectIndex"])
    for _ in range(32):
        if current == target:
            return True
        if current < 0 or current >= int(data_win["objt"]["count"]):
            return False
        current = int(data_win["objt"]["objects"][current]["parentId"])
    return False


def bbox(data_win, instance, compatibility):
    sprite_index = int(instance["maskIndex"])
    if sprite_index < 0:
        sprite_index = int(instance["spriteIndex"])
    if sprite_index < 0 or sprite_index >= int(data_win["sprt"]["count"]):
        return None, None
    sprite = data_win["sprt"]["sprites"][sprite_index]
    if int(sprite["bboxMode"]) == 1:
        margin_left = 0.0
        margin_right = float(sprite["width"])
        margin_top = 0.0
        margin_bottom = float(sprite["height"])
    else:
        margin_left = float(sprite["marginLeft"])
        margin_right = float(sprite["marginRight"] + 1)
        margin_top = float(sprite["marginTop"])
        margin_bottom = float(sprite["marginBottom"] + 1)
    x = float(instance["x"])
    y = float(instance["y"])
    x_scale = float(instance["imageXscale"])
    y_scale = float(instance["imageYscale"])
    angle = float(instance["imageAngle"])
    origin_x = float(sprite["originX"])
    origin_y = float(sprite["originY"])
    if abs(angle) <= 0.0001:
        left = x + x_scale * (margin_left - origin_x)
        right = x + x_scale * (margin_right - origin_x)
        top = y + y_scale * (margin_top - origin_y)
        bottom = y + y_scale * (margin_bottom - origin_y)
        left, right = min(left, right), max(left, right)
        top, bottom = min(top, bottom), max(top, bottom)
    else:
        radians = angle * math.pi / 180.0
        cosine = math.cos(radians)
        sine = math.sin(radians)
        xs = (x_scale * (margin_left - origin_x),
              x_scale * (margin_right - origin_x))
        ys = (y_scale * (margin_top - origin_y),
              y_scale * (margin_bottom - origin_y))
        corners = [
            (x + cosine * local_x + sine * local_y,
             y - sine * local_x + cosine * local_y)
            for local_x in xs for local_y in ys
        ]
        left = min(point[0] for point in corners)
        right = max(point[0] for point in corners)
        top = min(point[1] for point in corners)
        bottom = max(point[1] for point in corners)
    if compatibility:
        left, right, top, bottom = map(round, (left, right, top, bottom))
    return (left, top, right, bottom), sprite


def point_in_precise_mask(inferior, sprite, instance, px, py):
    if int(sprite["sepMasks"]) != 1 or not int(sprite["masks"]):
        return True
    x_scale = float(instance["imageXscale"])
    y_scale = float(instance["imageYscale"])
    if abs(x_scale) < 0.0001 or abs(y_scale) < 0.0001:
        return False
    dx = px - float(instance["x"])
    dy = py - float(instance["y"])
    angle = float(instance["imageAngle"])
    if abs(angle) > 0.0001:
        radians = angle * math.pi / 180.0
        cosine = math.cos(radians)
        sine = math.sin(radians)
        dx, dy = cosine * dx - sine * dy, sine * dx + cosine * dy
    local_x = int(dx / x_scale + float(sprite["originX"]))
    local_y = int(dy / y_scale + float(sprite["originY"]))
    if (local_x < 0 or local_y < 0 or
            local_x >= int(sprite["width"]) or
            local_y >= int(sprite["height"])):
        return False
    mask_count = int(sprite["maskCount"])
    frame = int(float(instance["imageIndex"])) % mask_count
    mask = sprite["masks"][frame]
    mask_x = local_x - int(sprite["maskOffsetX"])
    mask_y = local_y - int(sprite["maskOffsetY"])
    mask_width = int(sprite["maskWidth"])
    mask_height = int(sprite["maskHeight"])
    if mask_x < 0 or mask_y < 0 or mask_x >= mask_width or mask_y >= mask_height:
        return False
    row_bytes = (mask_width + 7) // 8
    byte = bytes(inferior.read_memory(
        int(mask) + mask_y * row_bytes + mask_x // 8, 1
    ))[0]
    return bool(byte & (1 << (7 - (mask_x & 7))))


runner = gdb.parse_and_eval("g_runner").dereference()
data_win = runner["dataWin"].dereference()
grid = runner["spatialGrid"].dereference()
character = runner["instancesByObject"][CHARACTER_OBJECT][0].dereference()
inferior = gdb.selected_inferior()
compatibility = bool(runner["collisionCompatibilityMode"])
x = float(character["x"])
y = float(character["y"])

points = (
    ("floor-clear", x, y + 10.0, SOLID_OBJECT),
    ("head-clear", x, y - 32.0, SOLID_OBJECT),
    ("ledge-solid", x + 7.0, y - 26.0, SOLID_OBJECT),
    ("ledge-moving", x + 7.0, y - 26.0, MOVING_SOLID_OBJECT),
    ("ledge-head-clear", x + 7.0, y - 32.0, SOLID_OBJECT),
)

print("GRAPPLE_COLLISION_STATE room=%d frame=%d character=%d x=%.3f y=%.3f "
      "compatibility=%d grid=%dx%d dirty=%d" % (
          int(runner["currentRoomIndex"]), int(runner["frameCount"]),
          int(character["instanceId"]), x, y, 1 if compatibility else 0,
          int(grid["gridWidth"]), int(grid["gridHeight"]),
          arrlen(grid["dirtyInstances"]),
      ))

for dirty_index in range(arrlen(grid["dirtyInstances"])):
    dirty_id = int(grid["dirtyInstances"][dirty_index])
    dirty_pointer = None
    for instance_index in range(arrlen(runner["instances"])):
        candidate = runner["instances"][instance_index]
        if int(candidate) and int(candidate.dereference()["instanceId"]) == dirty_id:
            dirty_pointer = candidate
            break
    if dirty_pointer is None:
        print("GRAPPLE_DIRTY id=%d missing=1" % dirty_id)
    else:
        dirty = dirty_pointer.dereference()
        print("GRAPPLE_DIRTY id=%d object=%d:%s flag=%d cells=%s" % (
            dirty_id, int(dirty["objectIndex"]),
            object_name(data_win, int(dirty["objectIndex"])),
            1 if bool(dirty["spatialGridDirty"]) else 0,
            [int(dirty["collisionCells"][cell_index])
             for cell_index in range(arrlen(dirty["collisionCells"]))],
        ))

instance_count = arrlen(runner["instances"])
for label, px, py, target in points:
    if compatibility:
        px, py = round(px), round(py)
    cell_x = max(0, min(int(grid["gridWidth"]) - 1, int(px / 64.0)))
    cell_y = max(0, min(int(grid["gridHeight"]) - 1, int(py / 64.0)))
    cell_index = cell_y * int(grid["gridWidth"]) + cell_x
    cell = grid["grid"][cell_index]
    grid_hits = []
    grid_members = []
    all_cell_members = []
    for index in range(arrlen(cell)):
        instance_pointer = cell[index]
        if not int(instance_pointer):
            continue
        instance = instance_pointer.dereference()
        if not bool(instance["active"]):
            continue
        box, sprite = bbox(data_win, instance, compatibility)
        if box is None:
            continue
        object_index = int(instance["objectIndex"])
        all_cell_members.append((int(instance["instanceId"]), object_index,
                                 object_name(data_win, object_index)))
        if matches_target(data_win, instance, target):
            inside_box = box[0] <= px < box[2] and box[1] <= py < box[3]
            precise = inside_box and point_in_precise_mask(
                inferior, sprite, instance, px, py
            )
            grid_members.append((int(instance["instanceId"]), object_index,
                                 object_name(data_win, object_index), box,
                                 int(instance["spriteIndex"]),
                                 int(sprite["sepMasks"]), precise))
            if precise:
                grid_hits.append(int(instance["instanceId"]))
    full_hits = []
    for index in range(instance_count):
        instance_pointer = runner["instances"][index]
        if not int(instance_pointer):
            continue
        instance = instance_pointer.dereference()
        if not bool(instance["active"]) or not matches_target(data_win, instance, target):
            continue
        box, sprite = bbox(data_win, instance, compatibility)
        if box is None:
            continue
        if (box[0] <= px < box[2] and box[1] <= py < box[3] and
                point_in_precise_mask(inferior, sprite, instance, px, py)):
            full_hits.append(int(instance["instanceId"]))
    print("GRAPPLE_POINT label=%s point=%.3f,%.3f target=%d cell=%d,%d "
          "cell_count=%d cell_members=%s grid_hits=%s full_hits=%s" % (
              label, px, py, target, cell_x, cell_y, arrlen(cell),
              all_cell_members, grid_hits, full_hits,
          ))
    for member in grid_members:
        print(" GRAPPLE_MEMBER id=%d object=%d:%s bbox=%.1f,%.1f,%.1f,%.1f "
              "sprite=%d sepMasks=%d hit=%d" % (
                  member[0], member[1], member[2], *member[3], member[4],
                  member[5], 1 if member[6] else 0,
              ))
    for full_id in full_hits:
        for instance_index in range(instance_count):
            full_pointer = runner["instances"][instance_index]
            if not int(full_pointer):
                continue
            full_instance = full_pointer.dereference()
            if int(full_instance["instanceId"]) != full_id:
                continue
            print(" GRAPPLE_FULL_HIT id=%d dirty=%d cells=%s" % (
                full_id,
                1 if bool(full_instance["spatialGridDirty"]) else 0,
                [int(full_instance["collisionCells"][full_cell])
                 for full_cell in range(arrlen(full_instance["collisionCells"]))],
            ))
            break
