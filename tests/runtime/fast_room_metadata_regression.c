/* Production stream/metadata logic with independent bounded VM/actor fixtures. */
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#define STB_DS_IMPLEMENTATION
#include "stb_ds.h"
#define nullptr NULL
#define repeat(n, i) for (uint32_t i = 0; i < (uint32_t)(n); ++i)
#define MAX_VIEWS 2
#define MAX_DEFAULT_ROOM_CAMERAS 2
#define ROOM_COUNT 200u
typedef uint32_t GMLArray;
typedef uint64_t RValue;
typedef uint32_t RuntimeBackground, RuntimeView, GMLCamera, RoomTile, RuntimeLayer;
typedef struct { int key, value; } TileLayerMapEntry;
typedef struct { uint32_t instanceId; int32_t objectIndex; float x, y; } FastInstanceFields;
typedef FastInstanceFields Instance;
typedef struct { uint32_t marker; } FastRunnerFields;
typedef struct Room {
    bool present, persistent, initialPersistent, payloadLoaded;
    uint32_t width, height, speed, initialWidth, initialHeight, initialSpeed;
    const char *name;
    RoomTile *tiles;
    uint32_t tileCount;
} Room;
typedef struct {
    bool initialized;
    Instance **instances;
    RuntimeBackground backgrounds[8];
    uint32_t backgroundColor;
    bool drawBackgroundColor;
    TileLayerMapEntry *tileLayerMap;
    RoomTile *tiles;
    uint32_t tileCount;
    RuntimeLayer *runtimeLayers;
    RuntimeView views[MAX_VIEWS];
    bool viewsEnabled;
    GMLCamera defaultCameras[MAX_DEFAULT_ROOM_CAMERAS];
} SavedRoomState;
typedef struct {
    struct { uint32_t count; Room *rooms; } room;
    struct { uint32_t count; } objt, code, sprt;
    uint64_t fileSize;
    struct { uint32_t gameID, licenseCRC32; uint8_t directPlayGuid[16]; } gen8;
} DataWin;
typedef struct { uint32_t selfVars; } GlobalScope;
typedef struct { GlobalScope *globalScopeInstance; } VM;
typedef struct Runner {
    DataWin *dataWin;
    VM *vmContext;
    Room *currentRoom;
    int32_t currentRoomIndex;
    SavedRoomState *savedRoomStates;
    Instance **instances;
    RuntimeBackground backgrounds[8];
    uint32_t backgroundColor;
    bool drawBackgroundColor;
    TileLayerMapEntry *tileLayerMap;
    RuntimeLayer *runtimeLayers;
    RuntimeView views[MAX_VIEWS];
    bool viewsEnabled;
    GMLCamera defaultCameras[MAX_DEFAULT_ROOM_CAMERAS], userCameras[2], surfaceCamera, guiCamera;
    uint32_t marker, resets, finishes, normalCreates, objectAdds;
} Runner;
#include "fast_room_primitives.inc"
#include "fast_room_reset.inc"

static bool writeVarMap(FastWriter *w, const uint32_t *value) { return writerU32(w, *value); }
static bool readVarMap(FastReader *r, Runner *runner, GlobalScope *scope) {
    (void)runner; scope->selfVars = readerU32(r); return !r->error;
}
static bool writeTileMap(FastWriter *w, TileLayerMapEntry *map) {
    assert(map == NULL); return writerU32(w, 0);
}
static bool readTileMap(FastReader *r, TileLayerMapEntry **map) {
    if (readerU32(r) != 0) { r->error = EBADMSG; return false; }
    *map = NULL; return !r->error;
}
#include "fast_room_visual.inc"

static void freeActors(Instance ***actors) {
    repeat(arrlen(*actors), i) free((*actors)[i]);
    arrfree(*actors); *actors = NULL;
}
static void clearFixtures(Runner *runner) {
    freeActors(&runner->instances);
    repeat(runner->dataWin->room.count, i) {
        SavedRoomState *saved = &runner->savedRoomStates[i];
        freeActors(&saved->instances);
        free(saved->tiles); saved->tiles = NULL;
        saved->tileCount = 0; saved->initialized = false;
        free(runner->dataWin->room.rooms[i].tiles);
        runner->dataWin->room.rooms[i].tiles = NULL;
        runner->dataWin->room.rooms[i].tileCount = 0;
    }
}
static bool Runner_beginFastStateRestore(Runner *runner, int32_t roomIndex) {
    if (roomIndex < 0 || (uint32_t)roomIndex >= runner->dataWin->room.count) return false;
    ++runner->resets;
    clearFixtures(runner);
    resetFastStateRoomMetadata(runner);
    runner->currentRoomIndex = roomIndex;
    runner->currentRoom = &runner->dataWin->room.rooms[roomIndex];
    return true;
}
static void Runner_finishFastStateRestore(Runner *runner) { ++runner->finishes; }
static FastRunnerFields captureRunnerFields(Runner *runner) {
    return (FastRunnerFields){ .marker = runner->marker };
}
static void restoreRunnerFields(Runner *runner, const FastRunnerFields *fields) {
    runner->marker = fields->marker;
}
static bool writeInstances(FastWriter *w, Instance **instances) {
    if (!writerU32(w, (uint32_t)arrlen(instances))) return false;
    repeat(arrlen(instances), i)
        if (!writerBytes(w, instances[i], sizeof(FastInstanceFields))) return false;
    return true;
}
static bool readInstances(FastReader *r, Runner *runner, Instance ***instances, bool current) {
    (void)runner; (void)current;
    uint32_t count = readerU32(r);
    if (!countOkay(r, count)) return false;
    repeat(count, i) {
        Instance *instance = calloc(1, sizeof(*instance)); assert(instance);
        if (!readerBytes(r, instance, sizeof(*instance))) { free(instance); return false; }
        arrput(*instances, instance);
    }
    return !r->error;
}
#define AUX_PAIR(name, tag) \
static bool write##name(FastWriter *w, Runner *runner) { (void)runner; return writerU32(w, tag); } \
static bool read##name(FastReader *r, Runner *runner) { (void)runner; \
    if (readerU32(r) != tag) { r->error = EBADMSG; return false; } return !r->error; }
AUX_PAIR(DataStructures, 0x11112222u)
AUX_PAIR(AuxiliaryPools, 0x22223333u)
AUX_PAIR(OpenTextFiles, 0x33334444u)
AUX_PAIR(FileFindState, 0x44445555u)
AUX_PAIR(Surfaces, 0x55556666u)
AUX_PAIR(Audio, 0x66667777u)
#include "fast_room_stream.inc"

static void replaceRoomTiles(Room *room, const RoomTile *tiles, uint32_t count) {
    free(room->tiles); room->tiles = NULL; room->tileCount = count;
    if (count) { room->tiles = malloc(count * sizeof(*tiles)); assert(room->tiles);
        memcpy(room->tiles, tiles, count * sizeof(*tiles)); }
}
static void freeRuntimeLayersArray(RuntimeLayer **layers) { free(*layers); *layers = NULL; }
static Instance **takePersistentInstances(Runner *runner) { (void)runner; return NULL; }
static void returnPersistentInstances(Runner *runner, Instance **actors) { (void)runner; assert(actors == NULL); }
static void Runner_addInstanceToObjectLists(Runner *runner, Instance *actor) { assert(actor); ++runner->objectAdds; }
#define logInfo(...) ((void)0)
static void reenterRoom(Runner *runner, int32_t roomIndex) {
    Room *room = &runner->dataWin->room.rooms[roomIndex];
    SavedRoomState *savedState = &runner->savedRoomStates[roomIndex];
    runner->currentRoom = room; runner->currentRoomIndex = roomIndex;
    freeActors(&runner->instances);
#include "fast_room_reenter.inc"
    ++runner->normalCreates;
}

static void seed(Runner *runner) {
    DataWin *dw = calloc(1, sizeof(*dw)); assert(dw);
    dw->room.count = ROOM_COUNT; dw->room.rooms = calloc(ROOM_COUNT, sizeof(Room));
    dw->objt.count = 737; dw->code.count = 8667; dw->sprt.count = 1500;
    dw->fileSize = 123456; dw->gen8.gameID = 77; dw->gen8.licenseCRC32 = 123;
    runner->dataWin = dw; runner->savedRoomStates = calloc(ROOM_COUNT, sizeof(SavedRoomState));
    runner->vmContext = calloc(1, sizeof(VM));
    runner->vmContext->globalScopeInstance = calloc(1, sizeof(GlobalScope));
    repeat(ROOM_COUNT, i) {
        Room *room = &dw->room.rooms[i]; room->present = true; room->name = "fixture";
        room->width = room->initialWidth = 640; room->height = room->initialHeight = 240;
        room->speed = room->initialSpeed = 60; room->payloadLoaded = i == 16;
    }
    dw->room.rooms[16].height = dw->room.rooms[16].initialHeight = 1500;
    runner->currentRoomIndex = 16; runner->currentRoom = &dw->room.rooms[16];
    runner->marker = 0xa2f05u;
    repeat(18, i) runner->savedRoomStates[i].initialized = true;
    SavedRoomState *parked = &runner->savedRoomStates[191]; parked->initialized = true;
    repeat(27, i) {
        Instance *actor = calloc(1, sizeof(*actor)); assert(actor);
        *actor = (Instance){ 148680 + i, i == 0 ? 266 : i == 1 ? 426 : 4,
                              i == 0 ? 109.0f : 32.0f, i == 0 ? 192.0f : 176.0f };
        arrput(parked->instances, actor);
    }
    dw->room.rooms[191].persistent = true;
    dw->room.rooms[17].persistent = true; /* exact v5 must retain true with zero parked actors */
}
static void destroy(Runner *runner) {
    clearFixtures(runner); free(runner->savedRoomStates); free(runner->dataWin->room.rooms);
    free(runner->dataWin); free(runner->vmContext->globalScopeInstance); free(runner->vmContext);
}
static void refreshHeader(FastWriter *writer, uint32_t version) {
    FastStateHeader *header = (FastStateHeader *)writer->data;
    header->version = version; header->payloadSize = (uint32_t)(writer->size - sizeof(*header));
    header->payloadCrc32 = crc32Bytes(writer->data + sizeof(*header), header->payloadSize);
}
static void loadOkay(FastWriter *writer, Runner *runner) {
    FastReader reader = { .data = writer->data, .size = writer->size };
    assert(deserializeState(&reader, runner)); assert(reader.offset == reader.size && !reader.error);
}
static void makeFuture(Runner *runner) {
    repeat(ROOM_COUNT, i) { Room *room = &runner->dataWin->room.rooms[i];
        room->persistent = true; room->width = 900; room->height = 700; room->speed = 50; }
}
static void assertUntouchedOnReject(FastWriter *writer) {
    Runner runner = {0}; seed(&runner); makeFuture(&runner);
    FastReader reader = { .data = writer->data, .size = writer->size };
    assert(!deserializeState(&reader, &runner)); assert(reader.error == EBADMSG);
    assert(runner.resets == 0 && runner.finishes == 0);
    repeat(ROOM_COUNT, i) { Room *room = &runner.dataWin->room.rooms[i];
        assert(room->persistent && room->width == 900 && room->height == 700 && room->speed == 50); }
    destroy(&runner);
}
int main(void) {
    Runner source = {0}; seed(&source);
    source.dataWin->room.rooms[191].speed = 20;
    source.dataWin->room.rooms[191].width = 768;
    FastWriter v5 = {0}; assert(serializeState(&v5, &source));
    FastStateHeader *header = (FastStateHeader *)v5.data;
    assert(header->version == 5 && header->headerSize == sizeof(*header));
    assert(v5.data[sizeof(*header) + 4 + 191 * 13 + 12] == 1);
    Runner restored = {0}; seed(&restored); makeFuture(&restored);
    loadOkay(&v5, &restored);
    assert(restored.marker == source.marker && restored.resets == 1 && restored.finishes == 1);
    assert(restored.dataWin->room.rooms[191].persistent);
    assert(restored.dataWin->room.rooms[191].speed == 20 && restored.dataWin->room.rooms[191].width == 768);
    assert(restored.dataWin->room.rooms[17].persistent && !restored.dataWin->room.rooms[5].persistent);
    assert(restored.dataWin->room.rooms[5].speed == 60 && restored.dataWin->room.rooms[5].width == 640);
    destroy(&restored);

    /* Reconstruct legacy wire format by removing only the new fixed metadata table. */
    size_t table = 4 + ROOM_COUNT * 13;
    FastWriter v4 = {0};
    assert(writerBytes(&v4, v5.data, sizeof(*header)));
    assert(writerBytes(&v4, v5.data + sizeof(*header) + table, v5.size - sizeof(*header) - table));
    refreshHeader(&v4, 4);
    Runner legacy = {0}; seed(&legacy); makeFuture(&legacy); loadOkay(&v4, &legacy);
    repeat(ROOM_COUNT, i) assert(legacy.dataWin->room.rooms[i].persistent == (i == 191));
    assert(legacy.dataWin->room.rooms[191].width == 640 && legacy.dataWin->room.rooms[191].speed == 60);
    assert(arrlen(legacy.savedRoomStates[191].instances) == 27);
    reenterRoom(&legacy, 191);
    assert(legacy.normalCreates == 0 && legacy.objectAdds == 27 && arrlen(legacy.instances) == 27);
    assert(legacy.instances[0]->instanceId == 148680 && legacy.instances[0]->x == 109 && legacy.instances[0]->y == 192);
    assert(legacy.savedRoomStates[191].instances == NULL);
    /* Native Control RoomStart clears this temporary flag; next ordinary visit creates. */
    legacy.dataWin->room.rooms[191].persistent = false;
    reenterRoom(&legacy, 191); assert(legacy.normalCreates == 1 && arrlen(legacy.instances) == 0);
    destroy(&legacy);

    /* Recompute CRC so these corruptions test metadata preflight, not just CRC rejection. */
    uint8_t *lastFlag = v5.data + sizeof(*header) + 4 + (ROOM_COUNT - 1) * 13 + 12;
    *lastFlag = 2; refreshHeader(&v5, 5); assertUntouchedOnReject(&v5);
    *lastFlag = 0; refreshHeader(&v5, 5);
    uint32_t wrongCount = ROOM_COUNT - 1;
    memcpy(v5.data + sizeof(*header), &wrongCount, sizeof(wrongCount));
    refreshHeader(&v5, 5); assertUntouchedOnReject(&v5);
    uint32_t correctCount = ROOM_COUNT;
    memcpy(v5.data + sizeof(*header), &correctCount, sizeof(correctCount));
    uint32_t zero = 0; memcpy(v5.data + sizeof(*header) + 4, &zero, sizeof(zero));
    refreshHeader(&v5, 5); assertUntouchedOnReject(&v5);
    uint32_t width = 640; memcpy(v5.data + sizeof(*header) + 4, &width, sizeof(width));
    refreshHeader(&v5, 99); assertUntouchedOnReject(&v5);
    refreshHeader(&v5, 5);
    uint32_t oversized = FAST_STATE_MAX_ROOM_DIMENSION + 1;
    memcpy(v5.data + sizeof(*header) + 4, &oversized, sizeof(oversized));
    refreshHeader(&v5, 5); assertUntouchedOnReject(&v5);
    memcpy(v5.data + sizeof(*header) + 4, &width, sizeof(width));
    uint32_t badSpeed = FAST_STATE_MAX_ROOM_SPEED + 1;
    memcpy(v5.data + sizeof(*header) + 4 + 8, &badSpeed, sizeof(badSpeed));
    refreshHeader(&v5, 5); assertUntouchedOnReject(&v5);
    uint32_t normalSpeed = 60;
    memcpy(v5.data + sizeof(*header) + 4 + 8, &normalSpeed, sizeof(normalSpeed));
    size_t fullSize = v5.size;
    v5.size = sizeof(*header) + table - 1;
    refreshHeader(&v5, 5); assertUntouchedOnReject(&v5);
    v5.size = fullSize; refreshHeader(&v5, 5);
    --v5.size; refreshHeader(&v5, 5); /* truncated auxiliary trailer rejects, possibly after reset */
    FastReader truncated = { .data = v5.data, .size = v5.size };
    Runner shortRunner = {0}; seed(&shortRunner);
    assert(!deserializeState(&truncated, &shortRunner)); assert(truncated.error == EBADMSG); destroy(&shortRunner);
    source.dataWin->room.rooms[1].speed = FAST_STATE_MAX_ROOM_SPEED + 1;
    FastWriter invalid = {0}; assert(!serializeState(&invalid, &source)); assert(invalid.error == ENOTSUP);
    destroy(&source); free(v5.data); free(v4.data); free(invalid.data);
    puts("PASS: production v4/v5 stream, exact metadata, 18 empty records, parked Samus, one-time consumption, timeline reset, CRC/version and pre-mutation bounds");
    return 0;
}
