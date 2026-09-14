"""Read-only consistency audit for a stopped Butterscotch spatial grid.

Use with GDB after stopping the runner, or let GDB's attach stop it.  The script
does not set breakpoints or mutate game state.  It checks the two-way invariant
between Instance.collisionCells and the grid, plus the dirty-queue invariant
that exposed AM2R's missing ledge collision.
"""

import gdb


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


try:
    runner_pointer = gdb.parse_and_eval("$am2r_runner_override")
    if runner_pointer.type.code == gdb.TYPE_CODE_VOID or not int(runner_pointer):
        runner_pointer = gdb.parse_and_eval("g_runner")
except gdb.error:
    runner_pointer = gdb.parse_and_eval("g_runner")
runner = runner_pointer.cast(gdb.lookup_type("Runner").pointer()).dereference()
data_win = runner["dataWin"].dereference()
grid = runner["spatialGrid"].dereference()
grid_width = int(grid["gridWidth"])
grid_height = int(grid["gridHeight"])

dirty_ids = {
    int(grid["dirtyInstances"][index])
    for index in range(arrlen(grid["dirtyInstances"]))
}

actual_cells = {}
destroyed_grid = []
for cell_index in range(grid_width * grid_height):
    cell = grid["grid"][cell_index]
    for member_index in range(arrlen(cell)):
        pointer = cell[member_index]
        if not int(pointer):
            continue
        address = int(pointer)
        actual_cells.setdefault(address, []).append(cell_index)
        instance = pointer.dereference()
        if bool(instance["destroyed"]):
            destroyed_grid.append((int(instance["instanceId"]), cell_index))

stranded_dirty = []
missing_clean = []
cache_mismatch = []
inactive_mismatch = []
retained_inactive = 0
active_count = 0
clean_collidable_count = 0

instances = runner["instances"]
for index in range(arrlen(instances)):
    pointer = instances[index]
    if not int(pointer):
        continue
    instance = pointer.dereference()
    instance_id = int(instance["instanceId"])
    object_index = int(instance["objectIndex"])
    active = bool(instance["active"])
    destroyed = bool(instance["destroyed"])
    dirty = bool(instance["spatialGridDirty"])
    cached = []
    for cached_index in range(arrlen(instance["collisionCells"])):
        packed = int(instance["collisionCells"][cached_index])
        grid_x = (packed >> 16) & 0xFFFF
        grid_y = packed & 0xFFFF
        cached.append(grid_y * grid_width + grid_x)
    cached.sort()
    actual = sorted(actual_cells.get(int(pointer), []))

    if active and not destroyed:
        active_count += 1
    if dirty and instance_id not in dirty_ids:
        stranded_dirty.append((instance_id, object_index, object_name(data_win, object_index)))
    if not active and not destroyed and (cached or actual):
        retained_inactive += 1
        if cached != actual:
            inactive_mismatch.append((instance_id, object_index, cached, actual))
    if destroyed and (cached or actual):
        inactive_mismatch.append((instance_id, object_index, cached, actual))
    if not active or destroyed or dirty or object_index < 0:
        continue

    sprite_index = int(instance["maskIndex"])
    if sprite_index < 0:
        sprite_index = int(instance["spriteIndex"])
    if sprite_index < 0 or sprite_index >= int(data_win["sprt"]["count"]):
        continue
    clean_collidable_count += 1
    if not cached:
        missing_clean.append((instance_id, object_index, object_name(data_win, object_index)))
    elif cached != actual:
        cache_mismatch.append((instance_id, object_index, cached, actual))

print(
    "SPATIAL_GRID_AUDIT room=%d frame=%d active=%d clean_collidable=%d "
    "dirty_queue=%d stranded_dirty=%d missing_clean=%d cache_mismatch=%d "
    "retained_inactive=%d inactive_mismatch=%d destroyed_grid=%d" % (
        int(runner["currentRoomIndex"]), int(runner["frameCount"]), active_count,
        clean_collidable_count, len(dirty_ids), len(stranded_dirty),
        len(missing_clean), len(cache_mismatch), retained_inactive,
        len(inactive_mismatch), len(destroyed_grid),
    )
)

for label, entries in (
    ("STRANDED_DIRTY", stranded_dirty),
    ("MISSING_CLEAN", missing_clean),
    ("CACHE_MISMATCH", cache_mismatch),
    ("INACTIVE_MISMATCH", inactive_mismatch),
    ("DESTROYED_GRID", destroyed_grid),
):
    for entry in entries[:50]:
        print("SPATIAL_GRID_%s %s" % (label, entry))
    if len(entries) > 50:
        print("SPATIAL_GRID_%s ... %d more" % (label, len(entries) - 50))
