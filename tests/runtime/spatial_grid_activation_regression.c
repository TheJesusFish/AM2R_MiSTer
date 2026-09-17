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
    runner.spatialGrid->trackedRemovalPasses = 0;
    runner.spatialGrid->fullRemovalPasses = 0;
    Runner_setActiveState(&runner, &instance, false);
    assert(!instance.active);
    assert(instance.spatialGridDirty);
    assert(arrlen(runner.spatialGrid->dirtyInstances) == 2);
    assert(runner.spatialGrid->trackedRemovalPasses == 0);
    assert(runner.spatialGrid->fullRemovalPasses == 0);

    SpatialGrid_syncGrid(&runner, runner.spatialGrid);
    assert(arrlen(runner.spatialGrid->dirtyInstances) == 0);
    assert(!instance.spatialGridDirty);
    assert(arrlen(instance.collisionCells) > 0);
    assert(instance.collisionGridBBoxValid);

    // Reactivation reuses the inactive instance's retained cell. The old
    // behavior left the stale dirty bit set while also leaving the instance
    // out of the grid, so it disappeared from optimized collision APIs.
    Runner_setActiveState(&runner, &instance, true);
    assert(instance.active);
    assert(!instance.spatialGridDirty);
    assert(arrlen(runner.spatialGrid->dirtyInstances) == 0);
    SpatialGrid_syncGrid(&runner, runner.spatialGrid);
    assert(!instance.spatialGridDirty);
    assert(arrlen(instance.collisionCells) > 0);
    assert(instance.collisionGridBBoxValid);
    assert(instance.collisionGridLeft == 65.0);
    assert(instance.collisionGridTop == 65.0);
    assert(instance.collisionGridRight == 81.0);
    assert(instance.collisionGridBottom == 81.0);

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
    assert(instance.collisionGridBBoxValid);
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

    // Moving an already-indexed active instance is the common AM2R path.  Its
    // exact collisionCells cache must remove it without scanning every cell in
    // the room, then insert it at the new position.
    runner.spatialGrid->trackedRemovalPasses = 0;
    runner.spatialGrid->fullRemovalPasses = 0;
    instance.x = 1.0f;
    instance.y = 1.0f;
    SpatialGrid_markInstanceAsDirty(runner.spatialGrid, &instance);
    SpatialGrid_syncGrid(&runner, runner.spatialGrid);
    assert(runner.spatialGrid->trackedRemovalPasses == 1);
    assert(runner.spatialGrid->fullRemovalPasses == 0);
    assert(instance.collisionGridBBoxValid);
    assert(instance.collisionGridLeft == 1.0);
    assert(instance.collisionGridTop == 1.0);
    cell = runner.spatialGrid->grid[cellIndex];
    for (int i = 0; i < arrlen(cell); ++i)
        assert(cell[i] != &instance);
    int newCellIndex = SpatialGrid_cellIndex(runner.spatialGrid, 0, 0);
    cell = runner.spatialGrid->grid[newCellIndex];
    occurrences = 0;
    for (int i = 0; i < arrlen(cell); ++i) {
        if (cell[i] == &instance) occurrences++;
    }
    assert(occurrences == 1);

    SpatialGridRange exactCellRange = SpatialGrid_computeCellRange(
        runner.spatialGrid, 0.0f, 0.0f, 63.0f, 63.0f);
    assert(SpatialGrid_instanceOverlapsRange(&instance, exactCellRange));
    assert(SpatialGrid_instanceHasCellFullyInsideBounds(
        &instance, 0.0f, 0.0f, 127.0f, 127.0f));
    assert(!SpatialGrid_instanceHasCellFullyInsideBounds(
        &instance, 1.0f, 1.0f, 62.0f, 62.0f));

    // If a moved instance is deactivated before the pending sync, remove it
    // through its tracked cells. The stale dirty-list ID must not cause a
    // second exhaustive scan when the grid is synchronized later.
    runner.spatialGrid->trackedRemovalPasses = 0;
    runner.spatialGrid->fullRemovalPasses = 0;
    instance.x = 70.0f;
    instance.y = 70.0f;
    SpatialGrid_markInstanceAsDirty(runner.spatialGrid, &instance);
    assert(instance.spatialGridDirty);
    Runner_setActiveState(&runner, &instance, false);
    assert(!instance.active);
    assert(instance.spatialGridDirty);
    assert(runner.spatialGrid->trackedRemovalPasses == 1);
    assert(runner.spatialGrid->fullRemovalPasses == 0);
    SpatialGrid_syncGrid(&runner, runner.spatialGrid);
    assert(runner.spatialGrid->trackedRemovalPasses == 1);
    assert(runner.spatialGrid->fullRemovalPasses == 0);
    assert(!instance.spatialGridDirty);
    assert(arrlen(instance.collisionCells) > 0);
    assert(instance.collisionGridBBoxValid);

    Runner_setActiveState(&runner, &instance, true);
    SpatialGrid_syncGrid(&runner, runner.spatialGrid);
    assert(instance.active);
    assert(!instance.spatialGridDirty);
    assert(arrlen(instance.collisionCells) > 0);

    // Sprite-less instances use their origin as their activation cell. They
    // remain non-colliding (no bbox), but no longer require a full scan of all
    // room instances for every activation-region call.
    Instance pointInstance = {0};
    pointInstance.instanceId = 100002;
    pointInstance.objectIndex = 0;
    pointInstance.active = true;
    pointInstance.visible = true;
    pointInstance.spriteIndex = -1;
    pointInstance.maskIndex = -1;
    pointInstance.imageXscale = 1.0f;
    pointInstance.imageYscale = 1.0f;
    pointInstance.x = 96.0f;
    pointInstance.y = 32.0f;
    int pointKey = (int)pointInstance.instanceId;
    hmput(runner.instancesById, pointKey, &pointInstance);
    SpatialGrid_markInstanceAsDirty(runner.spatialGrid, &pointInstance);
    Runner_setActiveState(&runner, &pointInstance, false);
    SpatialGrid_syncGrid(&runner, runner.spatialGrid);
    assert(!pointInstance.active);
    assert(!pointInstance.spatialGridDirty);
    assert(arrlen(pointInstance.collisionCells) == 1);
    assert(!pointInstance.collisionGridBBoxValid);
    int pointCellIndex = SpatialGrid_cellIndex(runner.spatialGrid, 1, 0);
    cell = runner.spatialGrid->grid[pointCellIndex];
    found = false;
    for (int i = 0; i < arrlen(cell); ++i) {
        if (cell[i] == &pointInstance) found = true;
    }
    assert(found);
    SpatialGrid_removeInstance(runner.spatialGrid, &pointInstance);
    arrfree(pointInstance.collisionCells);
    hmdel(runner.instancesById, pointKey);

    // Lifecycle removal remains deliberately exhaustive.  Simulate the stale
    // untracked entry seen after a room transition and prove it is purged.
    int staleCellIndex = SpatialGrid_cellIndex(runner.spatialGrid, 1, 1);
    arrput(runner.spatialGrid->grid[staleCellIndex], &instance);
    runner.spatialGrid->trackedRemovalPasses = 0;
    runner.spatialGrid->fullRemovalPasses = 0;
    SpatialGrid_removeInstance(runner.spatialGrid, &instance);
    assert(runner.spatialGrid->trackedRemovalPasses == 0);
    assert(runner.spatialGrid->fullRemovalPasses == 1);
    repeat(runner.spatialGrid->gridWidth * runner.spatialGrid->gridHeight, ci) {
        cell = runner.spatialGrid->grid[ci];
        for (int i = 0; i < arrlen(cell); ++i)
            assert(cell[i] != &instance);
    }
    assert(arrlen(instance.collisionCells) == 0);
    assert(!instance.collisionGridBBoxValid);
    assert(!instance.spatialGridDirty);

    arrfree(instance.collisionCells);
    hmfree(runner.instancesById);
    SpatialGrid_free(runner.spatialGrid);
    puts("spatial-grid activation regression: PASS");
    return 0;
}
