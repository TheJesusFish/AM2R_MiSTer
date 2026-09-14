// Regression for inactive-before-first-sync instances disappearing forever.

#include <assert.h>
#include <stdarg.h>
#include <stdio.h>

#include "runner.h"
#include "spatial_grid.h"
#include "stb_ds.h"

void platformLog(const logType type, const char* format, va_list args) {
    (void)type;
    vfprintf(stderr, format, args);
}

int main(void) {
    Sprite sprite = {0};
    sprite.present = true;
    sprite.width = 16;
    sprite.height = 16;
    sprite.marginLeft = 0;
    sprite.marginRight = 15;
    sprite.marginTop = 0;
    sprite.marginBottom = 15;
    sprite.originX = 0;
    sprite.originY = 0;

    DataWin dataWin = {0};
    dataWin.sprt.count = 1;
    dataWin.sprt.sprites = &sprite;

    Runner runner = {0};
    runner.dataWin = &dataWin;
    runner.spatialGrid = SpatialGrid_create(128, 128);

    Instance instance = {0};
    instance.instanceId = 100001;
    instance.objectIndex = 0;
    instance.active = true;
    instance.visible = true;
    instance.spriteIndex = 0;
    instance.maskIndex = -1;
    instance.imageXscale = 1.0f;
    instance.imageYscale = 1.0f;
    instance.x = 65.0f;
    instance.y = 65.0f;
    int instanceKey = (int)instance.instanceId;
    hmput(runner.instancesById, instanceKey, &instance);

    // A room-created instance is queued, then an AM2R activation region turns
    // it off before the first collision query synchronizes the grid.
    SpatialGrid_markInstanceAsDirty(runner.spatialGrid, &instance);
    assert(instance.spatialGridDirty);
    assert(arrlen(runner.spatialGrid->dirtyInstances) == 1);
    Runner_setActiveState(&runner, &instance, false);
    assert(!instance.active);
    assert(!instance.spatialGridDirty);

    SpatialGrid_syncGrid(&runner, runner.spatialGrid);
    assert(arrlen(runner.spatialGrid->dirtyInstances) == 0);
    assert(arrlen(instance.collisionCells) == 0);

    // Reactivation must enqueue and insert it.  The old behavior left the
    // stale dirty bit set, so this call returned early and the instance never
    // participated in position_meeting or any other optimized collision API.
    Runner_setActiveState(&runner, &instance, true);
    assert(instance.active);
    assert(instance.spatialGridDirty);
    assert(arrlen(runner.spatialGrid->dirtyInstances) == 1);
    SpatialGrid_syncGrid(&runner, runner.spatialGrid);
    assert(!instance.spatialGridDirty);
    assert(arrlen(instance.collisionCells) > 0);

    int cellIndex = SpatialGrid_cellIndex(runner.spatialGrid, 1, 1);
    Instance** cell = runner.spatialGrid->grid[cellIndex];
    bool found = false;
    for (int i = 0; i < arrlen(cell); ++i) {
        if (cell[i] == &instance) found = true;
    }
    assert(found);

    // AM2R repeats deactivate-object/activate-region every frame in many
    // rooms.  A clean inserted instance can retain its cells while inactive
    // because collision queries filter inactive members. Reactivation must do
    // no queue/grid work and must not duplicate the cell entry.
    Runner_setActiveState(&runner, &instance, false);
    assert(!instance.active);
    assert(!instance.spatialGridDirty);
    assert(arrlen(instance.collisionCells) > 0);
    cell = runner.spatialGrid->grid[cellIndex];
    int inactiveOccurrences = 0;
    for (int i = 0; i < arrlen(cell); ++i) {
        if (cell[i] == &instance) inactiveOccurrences++;
    }
    assert(inactiveOccurrences == 1);

    Runner_setActiveState(&runner, &instance, true);
    Runner_setActiveState(&runner, &instance, true);
    assert(arrlen(runner.spatialGrid->dirtyInstances) == 0);
    SpatialGrid_syncGrid(&runner, runner.spatialGrid);
    assert(arrlen(instance.collisionCells) > 0);
    cell = runner.spatialGrid->grid[cellIndex];
    int occurrences = 0;
    for (int i = 0; i < arrlen(cell); ++i) {
        if (cell[i] == &instance) occurrences++;
    }
    assert(occurrences == 1);

    arrfree(instance.collisionCells);
    hmfree(runner.instancesById);
    SpatialGrid_free(runner.spatialGrid);
    puts("spatial-grid activation regression: PASS");
    return 0;
}
