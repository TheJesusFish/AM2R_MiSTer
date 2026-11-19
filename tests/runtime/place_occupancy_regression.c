/* Production occupancy helpers with bounded, independent collision/grid mocks.
 * This exercises candidate rejection and lifecycle ordering, not the actual
 * sprite-mask/OBB algorithms, the game, or hardware performance.
 */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define STB_DS_IMPLEMENTATION
#include "stb_ds.h"

#ifdef USE_FLOAT_REALS
typedef float GMLReal;
#else
typedef double GMLReal;
#endif
#define nullptr NULL
#define MAYBE_UNUSED __attribute__((unused))
#define repeat(n, i) for (int32_t i = 0; i < (int32_t)(n); ++i)
#define CHECK(condition) do { if (!(condition)) { \
    fprintf(stderr, "%s:%d: %s\n", __FILE__, __LINE__, #condition); \
    exit(1); \
} } while (0)

typedef struct { uint32_t bboxMode; int32_t originX, originY; } Sprite;
typedef struct { struct { uint32_t count; Sprite* sprites; } sprt; } DataWin;

typedef struct Instance {
    uint32_t instanceId;
    float x, y; /* Native built-ins retain float positions in both real modes. */
    bool active, solid, destroyed, spatialGridDirty;
    bool collisionGridBBoxValid;
    GMLReal collisionGridLeft, collisionGridRight;
    GMLReal collisionGridTop, collisionGridBottom;
    bool shapeValid, preciseMiss;
    int32_t spriteIndex, maskIndex;
    Sprite* collisionSprite;
    GMLReal offsetX, offsetY, width, height;
    float indexedX, indexedY; /* Stand-in for membership at the real position. */
    unsigned bboxCalls, preciseCalls, dirtyMarks;
} Instance;

typedef struct { unsigned syncCalls, rebuilt; } SpatialGrid;
typedef struct { Instance** instances; SpatialGrid* spatialGrid; DataWin* dataWin; } Runner;
typedef struct { Runner* runner; DataWin* dataWin; } VMContext;
typedef struct { GMLReal real; } RValue;
typedef struct { GMLReal left, right, top, bottom; bool valid; } InstanceBBox;

static int32_t RValue_toInt32(RValue value) { return (int32_t)value.real; }
static GMLReal RValue_toReal(RValue value) { return value.real; }
static RValue RValue_makeReal(GMLReal value) { RValue result = {value}; return result; }
static RValue RValue_makeUndefined(void) { return RValue_makeReal(0); }

static Instance* observedCaller;
static float realCallerX, realCallerY;
static uint32_t preciseOrder[256];
static size_t preciseOrderCount;

static InstanceBBox modelBBox(const Instance* instance) {
    GMLReal originX = instance->collisionSprite != NULL ? instance->collisionSprite->originX : 0;
    GMLReal originY = instance->collisionSprite != NULL ? instance->collisionSprite->originY : 0;
    InstanceBBox result = {
        (GMLReal)instance->x + instance->offsetX - originX,
        (GMLReal)instance->x + instance->offsetX - originX + instance->width,
        (GMLReal)instance->y + instance->offsetY - originY,
        (GMLReal)instance->y + instance->offsetY - originY + instance->height,
        instance->shapeValid
    };
    return result;
}

static bool modelPreciseOverlap(const Instance* a, const Instance* b,
                                InstanceBBox bboxA, InstanceBBox bboxB) {
    if (!bboxA.valid || !bboxB.valid) return false;
    if (bboxA.left >= bboxB.right || bboxB.left >= bboxA.right ||
        bboxA.top >= bboxB.bottom || bboxB.top >= bboxA.bottom) return false;
    /* A loose rotated AABB or pixel mask can overlap without a precise hit.
     * The independent mock deliberately represents that result; it does not
     * attempt to validate production SAT or pixel-mask geometry here. */
    return !a->preciseMiss && !b->preciseMiss;
}

static InstanceBBox Collision_computeBBox(Runner* runner, Instance* instance) {
    (void)runner;
    ++instance->bboxCalls;
    return modelBBox(instance);
}

static bool Collision_instancesOverlapPrecise(Runner* runner, Instance* a,
                                              Instance* b, InstanceBBox bboxA,
                                              InstanceBBox bboxB) {
    (void)runner;
    ++b->preciseCalls;
    CHECK(preciseOrderCount < sizeof(preciseOrder) / sizeof(preciseOrder[0]));
    preciseOrder[preciseOrderCount++] = b->instanceId;
    return modelPreciseOverlap(a, b, bboxA, bboxB);
}

static void SpatialGrid_syncGrid(Runner* runner, SpatialGrid* grid) {
    ++grid->syncCalls;
    if (observedCaller != NULL) {
        CHECK(observedCaller->x == realCallerX);
        CHECK(observedCaller->y == realCallerY);
    }
    for (size_t i = 0; i < (size_t)arrlen(runner->instances); ++i) {
        Instance* instance = runner->instances[i];
        if (!instance->spatialGridDirty) continue;
        ++grid->rebuilt;
        InstanceBBox bbox = Collision_computeBBox(runner, instance);
        instance->collisionGridBBoxValid = bbox.valid && !instance->destroyed;
        instance->collisionGridLeft = bbox.left;
        instance->collisionGridRight = bbox.right;
        instance->collisionGridTop = bbox.top;
        instance->collisionGridBottom = bbox.bottom;
        instance->indexedX = instance->x;
        instance->indexedY = instance->y;
        instance->spatialGridDirty = false;
    }
}

static void SpatialGrid_markInstanceAsDirty(SpatialGrid* grid, Instance* instance) {
    (void)grid;
    ++instance->dirtyMarks;
    instance->spatialGridDirty = true;
}

#include "place_occupancy_actual.inc"

typedef struct {
    Runner runner;
    SpatialGrid grid;
    Instance actors[128];
    size_t count;
} Fixture;

static void initFixture(Fixture* fixture) {
    memset(fixture, 0, sizeof(*fixture));
    fixture->runner.spatialGrid = &fixture->grid;
    observedCaller = NULL;
    preciseOrderCount = 0;
}

static Instance* addActor(Fixture* fixture, float x, float y, bool solid) {
    CHECK(fixture->count < sizeof(fixture->actors) / sizeof(fixture->actors[0]));
    Instance* instance = &fixture->actors[fixture->count++];
    instance->instanceId = (uint32_t)(100000 + fixture->count);
    instance->x = x;
    instance->y = y;
    instance->active = true;
    instance->solid = solid;
    instance->shapeValid = true;
    instance->maskIndex = -1;
    instance->width = 4;
    instance->height = 4;
    instance->spatialGridDirty = true;
    arrput(fixture->runner.instances, instance);
    return instance;
}

static void clearCounters(Fixture* fixture) {
    fixture->grid.syncCalls = fixture->grid.rebuilt = 0;
    preciseOrderCount = 0;
    for (size_t i = 0; i < fixture->count; ++i) {
        fixture->actors[i].bboxCalls = 0;
        fixture->actors[i].preciseCalls = 0;
        fixture->actors[i].dirtyMarks = 0;
    }
}

static void seedGrid(Fixture* fixture) {
    observedCaller = NULL;
    SpatialGrid_syncGrid(&fixture->runner, &fixture->grid);
    clearCounters(fixture);
}

static void destroyFixture(Fixture* fixture) {
    arrfree(fixture->runner.instances);
    observedCaller = NULL;
}

/* Independent original forward scan: no cache, sync, instrumented collision
 * helpers, or calls into the production occupancy implementation. */
static bool linearOracle(Runner* runner, Instance* caller,
                         GMLReal testX, GMLReal testY, bool solidOnly) {
    float savedX = caller->x, savedY = caller->y;
    caller->x = (float)testX;
    caller->y = (float)testY;
    InstanceBBox callerBBox = modelBBox(caller);
    bool empty = true;
    if (callerBBox.valid) {
        for (size_t i = 0; i < (size_t)arrlen(runner->instances); ++i) {
            Instance* other = runner->instances[i];
            if (!other->active || other == caller || (solidOnly && !other->solid)) continue;
            if (modelPreciseOverlap(caller, other, callerBBox, modelBBox(other))) {
                empty = false;
                break;
            }
        }
    }
    caller->x = savedX;
    caller->y = savedY;
    return empty;
}

static bool checkAt(Fixture* fixture, Instance* caller,
                    GMLReal x, GMLReal y, bool solidOnly) {
    bool expected = linearOracle(&fixture->runner, caller, x, y, solidOnly);
    float savedX = caller->x, savedY = caller->y;
    float indexedX = caller->indexedX, indexedY = caller->indexedY;
    bool callerWasDirty = caller->spatialGridDirty;
    observedCaller = caller;
    realCallerX = savedX;
    realCallerY = savedY;
    bool actual = solidOnly ? placeFreeAt(&fixture->runner, caller, x, y)
                            : placeEmptyAt(&fixture->runner, caller, x, y);
    CHECK(actual == expected);
    CHECK(caller->x == savedX && caller->y == savedY);
    if (fixture->runner.spatialGrid != NULL && callerWasDirty) {
        CHECK(caller->indexedX == savedX && caller->indexedY == savedY);
    } else {
        CHECK(caller->indexedX == indexedX && caller->indexedY == indexedY);
    }
    return actual;
}

static void filtersCase(void) {
    Fixture fixture;
    initFixture(&fixture);
    Instance* caller = addActor(&fixture, 0, 0, true);
    Instance* other = addActor(&fixture, 40, 40, false);
    seedGrid(&fixture);
    CHECK(!checkAt(&fixture, caller, 40, 40, false));
    CHECK(checkAt(&fixture, caller, 40, 40, true));
    other->active = false;
    CHECK(checkAt(&fixture, caller, 40, 40, false));
    other->destroyed = true; /* Normally destruction also makes it inactive. */
    CHECK(checkAt(&fixture, caller, 40, 40, false));
    other->active = true;
    other->spatialGridDirty = true;
    /* Preserve the original active-only contract for this synthetic state. */
    CHECK(!checkAt(&fixture, caller, 40, 40, false));
    other->shapeValid = false;
    other->spatialGridDirty = true;
    CHECK(checkAt(&fixture, caller, 40, 40, false));
    caller->shapeValid = false;
    caller->spatialGridDirty = true;
    CHECK(checkAt(&fixture, caller, 0, 0, true));
    destroyFixture(&fixture);
}

static void dirtyCase(void) {
    Fixture fixture;
    initFixture(&fixture);
    Instance* caller = addActor(&fixture, 1.25f, -2.5f, true);
    Instance* other = addActor(&fixture, 400, 500, true);
    seedGrid(&fixture);
    caller->x = 67.5f;
    caller->y = 18.25f;
    caller->spatialGridDirty = true;
    other->x = 80.5f;
    other->y = -20.25f;
    other->offsetX = -0.25;
    other->spatialGridDirty = true;
    CHECK(!checkAt(&fixture, caller, 80.25, -20.25, false));
    CHECK(fixture.grid.syncCalls == 1 && fixture.grid.rebuilt == 2);
    CHECK(other->collisionGridLeft == (GMLReal)80.25);
    CHECK(other->indexedX == 80.5f && other->indexedY == -20.25f);
    CHECK(caller->indexedX == 67.5f && caller->indexedY == 18.25f);
    CHECK(!caller->spatialGridDirty && !other->spatialGridDirty);
    CHECK(!checkAt(&fixture, caller, 80.25, -20.25, true));
    CHECK(fixture.grid.rebuilt == 2);
    destroyFixture(&fixture);
}

static void fallbackCase(void) {
    Fixture fixture;
    initFixture(&fixture);
    Instance* caller = addActor(&fixture, 0, 0, true);
    Instance* other = addActor(&fixture, 20, 30, true);
    seedGrid(&fixture);
    other->collisionGridBBoxValid = false;
    other->collisionGridLeft = other->collisionGridRight = 10000;
    other->collisionGridTop = other->collisionGridBottom = 10000;
    CHECK(!checkAt(&fixture, caller, 20, 30, false));
    CHECK(other->bboxCalls == 1 && other->preciseCalls == 1);
    /* A missing grid cannot validate even a superficially valid cache. */
    fixture.runner.spatialGrid = NULL;
    other->collisionGridBBoxValid = true;
    clearCounters(&fixture);
    CHECK(!checkAt(&fixture, caller, 20, 30, true));
    CHECK(fixture.grid.syncCalls == 0);
    CHECK(other->bboxCalls == 1 && other->preciseCalls == 1);
    other->shapeValid = false;
    CHECK(checkAt(&fixture, caller, 20, 30, false));
    CHECK(other->bboxCalls == 2 && other->preciseCalls == 1);
    destroyFixture(&fixture);
}

static void preciseCase(void) {
    Fixture fixture;
    initFixture(&fixture);
    Instance* caller = addActor(&fixture, 0, 0, true);
    Instance* other = addActor(&fixture, 4, 0, true);
    seedGrid(&fixture);
    CHECK(checkAt(&fixture, caller, 0, 0, false));
    /* Touching cached bounds stay on the exact path (strict rejection only). */
    CHECK(other->bboxCalls == 1 && other->preciseCalls == 1);
    other->x = 0;
    other->width = 32;
    other->height = 32;
    other->offsetX = -16;
    other->offsetY = -16;
    other->preciseMiss = true; /* Loose rotated-AABB/mask false positive. */
    other->spatialGridDirty = true;
    clearCounters(&fixture);
    CHECK(checkAt(&fixture, caller, 0, 0, false));
    CHECK(other->bboxCalls == 2 && other->preciseCalls == 1);
    CHECK(checkAt(&fixture, caller, 0, 0, true));
    other->preciseMiss = false;
    CHECK(!checkAt(&fixture, caller, 0, 0, false));
    caller->preciseMiss = true;
    CHECK(checkAt(&fixture, caller, 0, 0, false));
    destroyFixture(&fixture);
}

static void orderCase(void) {
    Fixture fixture;
    initFixture(&fixture);
    Instance* caller = addActor(&fixture, -40, -40, true);
    Instance* first = addActor(&fixture, 0, 0, true);
    Instance* distant = addActor(&fixture, 200, 200, true);
    Instance* second = addActor(&fixture, 0, 0, true);
    Instance* third = addActor(&fixture, 0, 0, true);
    first->preciseMiss = true;
    second->preciseMiss = true;
    /* Duplicated membership in the original traversal is not deduplicated. */
    fixture.runner.instances[2] = first;
    arrput(fixture.runner.instances, distant);
    seedGrid(&fixture);
    CHECK(!checkAt(&fixture, caller, 0, 0, false));
    CHECK(preciseOrderCount == 4);
    CHECK(preciseOrder[0] == first->instanceId && preciseOrder[1] == first->instanceId);
    CHECK(preciseOrder[2] == second->instanceId && preciseOrder[3] == third->instanceId);
    CHECK(distant->bboxCalls == 0);
    destroyFixture(&fixture);
}

static void extremesCase(void) {
    Fixture fixture;
    initFixture(&fixture);
    Instance* caller = addActor(&fixture, 67.125f, -78.875f, true);
    Instance* other = addActor(&fixture, -10000000, -10000000, true);
    other->width = other->height = 100000000;
    seedGrid(&fixture);
    CHECK(!checkAt(&fixture, caller, -10000000, -10000000, false));
    CHECK(!checkAt(&fixture, caller, -1, -1, true));
    CHECK(checkAt(&fixture, caller, 100000000, 100000000, false));
    other->x = 1.0e20f;
    other->y = -1.0e20f;
    other->width = other->height = (GMLReal)1.0e15;
    other->spatialGridDirty = true;
    /* Four pixels can round away at this magnitude in both real modes. The
     * required result is equality with the original scan, not a forced hit. */
    checkAt(&fixture, caller, (GMLReal)other->x, (GMLReal)other->y, false);
    CHECK(checkAt(&fixture, caller, (GMLReal)-1.0e20, (GMLReal)1.0e20, true));
    other->x = -1000;
    other->y = 2000;
    other->width = 3000;
    other->height = 4000;
    other->spatialGridDirty = true;
    CHECK(!checkAt(&fixture, caller, -999.125, 2001.25, true));
    destroyFixture(&fixture);
}

static void distantCase(void) {
    Fixture fixture;
    initFixture(&fixture);
    Instance* caller = addActor(&fixture, 10.5f, 20.5f, true);
    for (unsigned i = 0; i < 119; ++i) {
        Instance* light = addActor(&fixture, (float)(1000 + i * 20), 1500, true);
        light->offsetX = -7;
        light->offsetY = -11;
        light->width = 14;
        light->height = 22;
        light->preciseMiss = true; /* Represent a distant rotated-light shape. */
    }
    seedGrid(&fixture);
    for (unsigned i = 0; i < 4; ++i) {
        CHECK(checkAt(&fixture, caller, 10.5 + i, 20.5 - i, false));
        CHECK(checkAt(&fixture, caller, 10.5 + i, 20.5 - i, true));
    }
    CHECK(caller->bboxCalls == 8 && fixture.grid.rebuilt == 0);
    for (size_t i = 1; i < fixture.count; ++i) {
        CHECK(fixture.actors[i].bboxCalls == 0);
        CHECK(fixture.actors[i].preciseCalls == 0);
    }
    fixture.actors[1].collisionGridBBoxValid = false;
    CHECK(checkAt(&fixture, caller, 10.5, 20.5, false));
    CHECK(fixture.actors[1].bboxCalls == 1 && fixture.actors[1].preciseCalls == 1);
    destroyFixture(&fixture);
}

static uint32_t randomState = 0x26010001;
static uint32_t nextRandom(void) {
    randomState = randomState * 1664525u + 1013904223u;
    return randomState;
}

static float randomCoord(void) {
    return ((int32_t)(nextRandom() % 4001) - 2000) / 8.0f;
}

static void equivalenceCase(void) {
    Fixture fixture;
    initFixture(&fixture);
    Instance* caller = addActor(&fixture, 1.125f, -7.25f, true);
    caller->offsetX = -1.25;
    caller->offsetY = -0.75;
    caller->width = 11.5;
    caller->height = 7.25;
    for (unsigned i = 0; i < 63; ++i) {
        Instance* other = addActor(&fixture, randomCoord(), randomCoord(), (i % 3) == 0);
        other->active = (i % 9) != 0;
        other->shapeValid = (i % 11) != 0;
        other->preciseMiss = (i % 7) == 0;
        other->offsetX = (GMLReal)((int)(i % 5) - 2) * 0.25;
        other->offsetY = (GMLReal)((int)(i % 7) - 3) * 0.5;
        other->width = (GMLReal)(1 + i % 19) * 1.25;
        other->height = (GMLReal)(1 + i % 23) * 0.75;
    }
    seedGrid(&fixture);
    for (unsigned i = 0; i < 400; ++i) {
        /* Include hits at actor origins, misses, dirty shape/position updates,
         * and float-rounding of a higher-precision temporary probe. */
        Instance* target = &fixture.actors[1 + i % 63];
        if ((i % 5) == 0) {
            target->x = randomCoord();
            target->y = randomCoord();
            target->width = 3 + i % 37;
            target->spatialGridDirty = true;
        }
        GMLReal x = (i % 2) ? (GMLReal)target->x + (GMLReal)0.0000007 : randomCoord();
        GMLReal y = (i % 2) ? (GMLReal)target->y + (GMLReal)0.0000009 : randomCoord();
        clearCounters(&fixture);
        checkAt(&fixture, caller, x, y, false);
        checkAt(&fixture, caller, x, y, true);
    }
    CHECK(caller->indexedX == 1.125f && caller->indexedY == -7.25f);
    destroyFixture(&fixture);
}

static void settersCase(void) {
    Fixture fixture;
    initFixture(&fixture);
    Sprite sprites[3] = {{0}};
    DataWin dataWin = {{3, sprites}};
    fixture.runner.dataWin = &dataWin;
    VMContext ctx = {&fixture.runner, &dataWin};
    Instance* caller = addActor(&fixture, -100, -100, true);
    Instance* active = addActor(&fixture, 100, 0, true);
    Instance* inactive = addActor(&fixture, 100, 0, true);
    Instance* override = addActor(&fixture, 100, 0, true);
    Instance* displayOnly = addActor(&fixture, 100, 0, true);
    Instance* destroyed = addActor(&fixture, 100, 0, true);
    active->spriteIndex = inactive->spriteIndex = destroyed->spriteIndex = 1;
    active->collisionSprite = inactive->collisionSprite = &sprites[1];
    inactive->active = false;
    override->spriteIndex = 2;
    override->maskIndex = 1;
    override->collisionSprite = &sprites[1];
    override->active = false;
    displayOnly->spriteIndex = 1;
    displayOnly->maskIndex = 2;
    displayOnly->collisionSprite = &sprites[2];
    destroyed->destroyed = true;
    destroyed->active = false;
    seedGrid(&fixture);
    RValue offsetArgs[] = {{1}, {80}, {0}};
    builtin_sprite_set_offset(&ctx, offsetArgs, 3);
    CHECK(sprites[1].originX == 80 && sprites[1].originY == 0);
    CHECK(active->dirtyMarks == 1 && inactive->dirtyMarks == 1 && override->dirtyMarks == 1);
    CHECK(active->spatialGridDirty && inactive->spatialGridDirty && override->spatialGridDirty);
    CHECK(caller->dirtyMarks == 0 && displayOnly->dirtyMarks == 0 && destroyed->dirtyMarks == 0);
    CHECK(!checkAt(&fixture, caller, 20, 0, false));
    CHECK(active->collisionGridLeft == 20 && inactive->collisionGridLeft == 20);
    active->active = false;
    inactive->active = true; /* Reactivation must use bounds refreshed while inactive. */
    CHECK(!checkAt(&fixture, caller, 20, 0, true));
    clearCounters(&fixture);
    RValue modeArgs[] = {{1}, {2}};
    builtin_sprite_set_bbox_mode(&ctx, modeArgs, 2);
    CHECK(sprites[1].bboxMode == 2);
    CHECK(active->dirtyMarks == 1 && inactive->dirtyMarks == 1 && override->dirtyMarks == 1);
    CHECK(active->spatialGridDirty && inactive->spatialGridDirty && override->spatialGridDirty);
    CHECK(caller->dirtyMarks == 0 && displayOnly->dirtyMarks == 0 && destroyed->dirtyMarks == 0);
    builtin_sprite_set_bbox_mode(&ctx, modeArgs, 2); /* Unchanged mode is a no-op. */
    CHECK(active->dirtyMarks == 1 && inactive->dirtyMarks == 1 && override->dirtyMarks == 1);
    modeArgs[0].real = -1;
    builtin_sprite_set_bbox_mode(&ctx, modeArgs, 2);
    CHECK(active->dirtyMarks == 1 && inactive->dirtyMarks == 1 && override->dirtyMarks == 1);
    offsetArgs[0].real = 3;
    builtin_sprite_set_offset(&ctx, offsetArgs, 3);
    CHECK(active->dirtyMarks == 1 && inactive->dirtyMarks == 1 && override->dirtyMarks == 1);
    destroyFixture(&fixture);
}

int main(int argc, char** argv) {
    CHECK(argc == 2);
    if (strcmp(argv[1], "filters") == 0) filtersCase();
    else if (strcmp(argv[1], "dirty") == 0) dirtyCase();
    else if (strcmp(argv[1], "fallback") == 0) fallbackCase();
    else if (strcmp(argv[1], "precise") == 0) preciseCase();
    else if (strcmp(argv[1], "order") == 0) orderCase();
    else if (strcmp(argv[1], "extremes") == 0) extremesCase();
    else if (strcmp(argv[1], "distant") == 0) distantCase();
    else if (strcmp(argv[1], "equivalence") == 0) equivalenceCase();
    else if (strcmp(argv[1], "setters") == 0) settersCase();
    else CHECK(!"unknown case");
    puts("PASS");
    return 0;
}
