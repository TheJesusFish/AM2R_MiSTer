/* Actual production identity/lifetime functions; mock backing storage only. */
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "surface_id_lifetime_limit.inc"
#define nullptr NULL

typedef struct Runner { int32_t applicationSurfaceId; } Runner;
typedef struct Renderer { Runner *runner; } Renderer;
/* Independent heap journal: payload size is illustrative, not an ABI replica. */
typedef struct SWOffscreenJournal {
    bool active;
    uint32_t count;
    uint8_t payload[1024];
} SWOffscreenJournal;
typedef struct SWRenderer {
    Renderer renderer;
    uint32_t surfaceCount, surfaceCapacity, surfaceWriteSerial;
    uint8_t **surfacePixels;
    SWOffscreenJournal **offscreenJournals;
    int32_t *surfaceWidths, *surfaceHeights;
    bool *surfaceExistsFlags, *gpuResident, *offscreenResident;
    uint32_t *surfaceRevisions, *unifiedSurfaceHandles;
    int32_t currentSurface, am2rMapSurfaceId;
    bool am2rMapSurfaceRetained, am2rMapSurfaceReused, am2rMapSurfaceCacheValid;
    bool applicationViewValid, unifiedEnabled;
    uint32_t unifiedHostHandle;
    uint64_t am2rMapSurfaceTopologyHash, am2rMapSurfaceDiscoveryHash;
} SWRenderer;

typedef struct PixelAllocation {
    size_t bytes;
    uint32_t magic;
    bool textureResident;
} PixelAllocation;
static size_t liveBytes, liveBlocks, allocations, deallocations;
static size_t journalBytes, journalLive, journalAllocations, journalDeallocations;
static size_t gpuLive, offscreenLive, textureLive, hostSelections;
static size_t boundsResets, metadataGrowths;
static SWRenderer *activeRenderer;

static void *trackedMalloc(size_t bytes) {
    PixelAllocation *p = malloc(sizeof(*p) + bytes);
    assert(p != NULL);
    p->bytes = bytes; p->magic = 0xa2f01234; p->textureResident = false;
    liveBytes += bytes; ++liveBlocks; ++allocations;
    return p + 1;
}
static void *trackedCalloc(size_t count, size_t size) {
    void *p = trackedMalloc(count * size);
    memset(p, 0, count * size);
    return p;
}
static SWOffscreenJournal *allocateJournal(void) {
    PixelAllocation *p = calloc(1, sizeof(*p) + sizeof(SWOffscreenJournal));
    assert(p != NULL);
    p->bytes = sizeof(SWOffscreenJournal); p->magic = 0xa2f05678;
    journalBytes += p->bytes; ++journalLive; ++journalAllocations;
    SWOffscreenJournal *journal = (SWOffscreenJournal *)(p + 1);
    journal->active = true; journal->count = 3;
    return journal;
}
static void trackedFree(void *pixels) {
    if (pixels == NULL) return;
    PixelAllocation *p = (PixelAllocation *)pixels - 1;
    if (p->magic == 0xa2f05678) {
        SWOffscreenJournal *journal = pixels;
        /* Discard must precede release, never just abandon an active journal. */
        assert(!journal->active && journal->count == 0);
        journalBytes -= p->bytes; --journalLive; ++journalDeallocations;
        p->magic = 0; free(p); return;
    }
    assert(p->magic == 0xa2f01234 && !p->textureResident);
    liveBytes -= p->bytes; --liveBlocks; ++deallocations;
    p->magic = 0; free(p);
}
static void *growArray(void *array, size_t elementSize, uint32_t old, uint32_t capacity) {
    void *grown = realloc(array, elementSize * capacity);
    assert(grown != NULL);
    memset((uint8_t *)grown + elementSize * old, 0, elementSize * (capacity - old));
    return grown;
}
static void swEnsureSurfaceCapacity(SWRenderer *sw, uint32_t required) {
    assert(required <= SW_SURFACE_ID_LIMIT);
    if (required <= sw->surfaceCapacity) return;
    uint32_t old = sw->surfaceCapacity, capacity = old ? old : 8;
    while (capacity < required) capacity *= 2;
    if (capacity > SW_SURFACE_ID_LIMIT) capacity = SW_SURFACE_ID_LIMIT;
#define GROW(member) sw->member = growArray(sw->member, sizeof(*sw->member), old, capacity)
    GROW(surfacePixels); GROW(surfaceWidths); GROW(surfaceHeights);
    GROW(surfaceExistsFlags); GROW(surfaceRevisions); GROW(unifiedSurfaceHandles);
    GROW(gpuResident); GROW(offscreenResident); GROW(offscreenJournals);
#undef GROW
    sw->surfaceCapacity = capacity; ++metadataGrowths;
}
static void swUnifiedRelease(SWRenderer *sw, int32_t id) {
    assert(id >= 0 && (uint32_t)id < sw->surfaceCapacity);
    if (sw->gpuResident[id]) { --gpuLive; sw->gpuResident[id] = false; }
    sw->unifiedSurfaceHandles[id] = 0;
}
static void swOffscreenDiscard(SWRenderer *sw, int32_t id) {
    assert(id >= 0 && (uint32_t)id < sw->surfaceCapacity);
    if (sw->offscreenResident[id]) { --offscreenLive; sw->offscreenResident[id] = false; }
    if (sw->offscreenJournals[id]) {
        sw->offscreenJournals[id]->active = false;
        sw->offscreenJournals[id]->count = 0;
    }
}
static void MisterGpu_releaseTexture(uint8_t *pixels) {
    if (pixels == NULL) return;
    PixelAllocation *p = (PixelAllocation *)pixels - 1;
    assert(p->magic == 0xa2f01234);
    if (p->textureResident) { --textureLive; p->textureResident = false; }
}
static void swResetSurfaceContentBounds(SWRenderer *sw, int32_t id, bool restored) {
    (void)restored;
    assert(id >= 0 && (uint32_t)id < sw->surfaceCapacity); ++boundsResets;
}
static void swMarkSurfaceWrite(SWRenderer *sw, int32_t id) {
    sw->surfaceRevisions[id] = ++sw->surfaceWriteSerial;
}
static void swUseHostTarget(SWRenderer *sw) { sw->currentSurface = -1; ++hostSelections; }
static void VM_surfaceTraceResetRecords(void) {}
static void Am2rOrphanSurfaceReset(void) {}
static bool MisterGpu_surfaceReset(void) {
    for (uint32_t id = 0; id < activeRenderer->surfaceCapacity; ++id)
        swUnifiedRelease(activeRenderer, (int32_t)id);
    return true;
}
static void swUnifiedCheck(SWRenderer *sw, bool result, const char *operation) {
    (void)sw; (void)operation; assert(result);
}

#define safeMalloc trackedMalloc
#define safeCalloc trackedCalloc
#define free trackedFree
#include "surface_id_lifetime_production.inc"
#undef free
#undef safeCalloc
#undef safeMalloc

static void attachGpuStorage(SWRenderer *sw, int32_t id) {
    assert(swSurfaceExists(&sw->renderer, id));
    assert(!sw->gpuResident[id] && !sw->offscreenResident[id]);
    sw->gpuResident[id] = sw->offscreenResident[id] = true;
    sw->unifiedSurfaceHandles[id] = (uint32_t)id;
    ++gpuLive; ++offscreenLive;
    PixelAllocation *p = (PixelAllocation *)sw->surfacePixels[id] - 1;
    assert(!p->textureResident); p->textureResident = true; ++textureLive;
    assert(sw->offscreenJournals[id] == NULL);
    sw->offscreenJournals[id] = allocateJournal();
}
static void init(SWRenderer *sw, Runner *runner) {
    memset(sw, 0, sizeof(*sw)); runner->applicationSurfaceId = -1;
    sw->renderer.runner = runner; sw->currentSurface = sw->am2rMapSurfaceId = -1;
    sw->unifiedEnabled = true; activeRenderer = sw;
}
static void finish(SWRenderer *sw) {
    SWRenderer_fastStateResetSurfaces(&sw->renderer);
    assert(liveBlocks == 0 && liveBytes == 0 && allocations == deallocations);
    assert(gpuLive == 0 && offscreenLive == 0 && textureLive == 0);
    assert(journalLive == 0 && journalBytes == 0 && journalAllocations == journalDeallocations);
    free(sw->surfacePixels); free(sw->surfaceWidths); free(sw->surfaceHeights);
    free(sw->surfaceExistsFlags); free(sw->surfaceRevisions); free(sw->unifiedSurfaceHandles);
    free(sw->gpuResident); free(sw->offscreenResident); free(sw->offscreenJournals);
}

static void stale(SWRenderer *sw) {
    Renderer *r = &sw->renderer;
    int32_t snapshot = swCreateSurface(r, 512, 256);
    attachGpuStorage(sw, snapshot); swSurfaceFree(r, snapshot);
    int32_t boss = swCreateSurface(r, 160, 128);
    assert(boss > snapshot); attachGpuStorage(sw, boss);
    uint8_t *pixels = sw->surfacePixels[boss]; pixels[0] = 0x5a;
    for (int frame = 0; frame < 120; ++frame) {
        swSurfaceFree(r, snapshot); /* oControl's persistent stale snapshot handle. */
        assert(!swSurfaceExists(r, snapshot) && swSurfaceExists(r, boss));
        assert(sw->surfacePixels[boss] == pixels && pixels[0] == 0x5a);
        assert(gpuLive == 1 && offscreenLive == 1 && textureLive == 1);
        assert(journalLive == 1 && sw->offscreenJournals[boss]->active);
    }
    swSurfaceFree(r, boss);
}
static void churn(SWRenderer *sw) {
    Renderer *r = &sw->renderer;
    for (int32_t expected = 1; expected <= 20000; ++expected) {
        int32_t id = swCreateSurface(r, 2, 2);
        assert(id == expected); attachGpuStorage(sw, id);
        assert(liveBlocks == 1 && liveBytes == 16);
        sw->currentSurface = id; swSurfaceFree(r, id);
        assert(sw->currentSurface == -1 && !swSurfaceExists(r, id));
        assert(sw->surfacePixels[id] == NULL && sw->surfaceRevisions[id] == 0);
        assert(sw->offscreenJournals[id] == NULL);
        assert(liveBlocks == 0 && liveBytes == 0);
        assert(gpuLive == 0 && offscreenLive == 0 && textureLive == 0);
        assert(journalLive == 0 && journalBytes == 0);
    }
    assert(sw->surfaceCount == 20001 && hostSelections == 20000);
    assert(allocations == 20000 && deallocations == 20000);
    assert(journalAllocations == 20000 && journalDeallocations == 20000);
    assert(metadataGrowths <= 14); /* metadata grows geometrically, not per frame */
}
static void retainedMap(SWRenderer *sw) {
    Renderer *r = &sw->renderer;
    int32_t map = swCreateSurface(r, 2048, 1024);
    sw->am2rMapSurfaceId = map; sw->am2rMapSurfaceCacheValid = true;
    attachGpuStorage(sw, map); sw->currentSurface = map;
    uint8_t *pixels = sw->surfacePixels[map]; pixels[100] = 0x39;
    SWOffscreenJournal *journal = sw->offscreenJournals[map];
    uint32_t count = sw->surfaceCount;
    for (int repeat = 0; repeat < 20; ++repeat) {
        swSurfaceFree(r, map);
        assert(sw->am2rMapSurfaceRetained && !sw->am2rMapSurfaceReused);
        assert(swSurfaceExists(r, map) && sw->currentSurface == -1);
        assert(swCreateSurface(r, 2048, 1024) == map);
        assert(!sw->am2rMapSurfaceRetained && sw->am2rMapSurfaceReused);
        assert(sw->surfaceCount == count && sw->surfacePixels[map] == pixels && pixels[100] == 0x39);
        assert(allocations == 1 && deallocations == 0);
        assert(sw->offscreenJournals[map] == journal && journal->active && journal->count == 3);
        assert(journalLive == 1 && journalAllocations == 1 && journalDeallocations == 0);
    }
    swSurfaceFree(r, map);
    int32_t other = swCreateSurface(r, 2, 2);
    assert(other > map && sw->am2rMapSurfaceRetained);
    swSurfaceFree(r, other);
    sw->am2rMapSurfaceCacheValid = false;
    swSurfaceFree(r, map);
    assert(!swSurfaceExists(r, map) && liveBlocks == 0);
    assert(sw->offscreenJournals[map] == NULL && journalLive == 0 && journalBytes == 0);
}
static void invalid(SWRenderer *sw) {
    Renderer *r = &sw->renderer;
    assert(!swSurfaceExists(r, 0) && !swSurfaceExists(r, -1));
    assert(swCreateSurface(r, 0, 10) == -1 && swCreateSurface(r, 10, -1) == -1);
    assert(allocations == 0 && metadataGrowths == 0 && sw->surfaceCount == 0);
    int32_t app = swCreateSurface(r, 2, 2); assert(app == 1);
    r->runner->applicationSurfaceId = app; attachGpuStorage(sw, app);
    swSurfaceFree(r, app); swSurfaceFree(r, 0); swSurfaceFree(r, -1);
    swSurfaceFree(r, (int32_t)SW_SURFACE_ID_LIMIT);
    assert(swSurfaceExists(r, app) && !swSurfaceExists(r, 0));
    assert(!swSurfaceExists(r, (int32_t)SW_SURFACE_ID_LIMIT));
    assert(liveBlocks == 1 && gpuLive == 1);
}
static void cap(SWRenderer *sw) {
    Renderer *r = &sw->renderer;
    assert(SWRenderer_fastStateReserveSurfaceIds(r, SW_SURFACE_ID_LIMIT - 1));
    assert(swCreateSurface(r, 1, 1) == (int32_t)SW_SURFACE_ID_LIMIT - 1);
    assert(sw->surfaceCount == SW_SURFACE_ID_LIMIT);
    size_t before = allocations, growths = metadataGrowths, resets = boundsResets;
    for (int repeat = 0; repeat < 5; ++repeat) assert(swCreateSurface(r, 1, 1) == -1);
    assert(allocations == before && metadataGrowths == growths && boundsResets == resets);
    swSurfaceFree(r, (int32_t)SW_SURFACE_ID_LIMIT - 1);
    assert(swCreateSurface(r, 1, 1) == -1); /* freed identity is not reusable at cap */
    assert(liveBlocks == 0 && sw->surfaceCount == SW_SURFACE_ID_LIMIT);
}
static void restore(SWRenderer *sw) {
    Renderer *r = &sw->renderer;
    int32_t live = swCreateSurface(r, 1, 1);
    int32_t dead = swCreateSurface(r, 1, 1);
    swSurfaceFree(r, dead);
    uint32_t savedCount = sw->surfaceCount;
    const uint8_t savedPixels[4] = {1, 2, 3, 4};
    SWRenderer_fastStateResetSurfaces(r);
    assert(sw->surfaceCount == 1 && !swSurfaceExists(r, 0));
    assert(SWRenderer_fastStateReserveSurfaceIds(r, savedCount));
    assert(SWRenderer_fastStateRestoreSurface(r, (uint32_t)live, 1, 1, 19, savedPixels));
    assert(sw->surfaceCount == savedCount && !swSurfaceExists(r, dead));
    assert(sw->surfaceRevisions[live] == 19 && sw->surfaceWriteSerial == 19);
    assert(memcmp(sw->surfacePixels[live], savedPixels, 4) == 0);
    int32_t next = swCreateSurface(r, 1, 1); assert(next == (int32_t)savedCount);
    swSurfaceFree(r, dead); assert(swSurfaceExists(r, next));
    uint32_t count = sw->surfaceCount;
    size_t before = allocations;
    assert(!SWRenderer_fastStateReserveSurfaceIds(r, 0));
    assert(!SWRenderer_fastStateReserveSurfaceIds(r, SW_SURFACE_ID_LIMIT + 1));
    assert(!SWRenderer_fastStateReserveSurfaceIds(NULL, 1));
    assert(!SWRenderer_fastStateRestoreSurface(r, 0, 1, 1, 1, savedPixels));
    assert(!SWRenderer_fastStateRestoreSurface(r, SW_SURFACE_ID_LIMIT, 1, 1, 1, savedPixels));
    assert(sw->surfaceCount == count && allocations == before);
    SWRenderer_fastStateResetSurfaces(r);
    assert(SWRenderer_fastStateReserveSurfaceIds(r, SW_SURFACE_ID_LIMIT));
    assert(swCreateSurface(r, 1, 1) == -1); /* serialized fully-retired range */
}
static void reset(SWRenderer *sw) {
    Renderer *r = &sw->renderer;
    for (int i = 0; i < 8; ++i) attachGpuStorage(sw, swCreateSurface(r, 2, 2));
    sw->currentSurface = 3; sw->applicationViewValid = true;
    sw->am2rMapSurfaceId = 4; sw->am2rMapSurfaceRetained = true;
    sw->am2rMapSurfaceReused = sw->am2rMapSurfaceCacheValid = true;
    SWRenderer_fastStateResetSurfaces(r);
    assert(sw->surfaceCount == 1 && sw->currentSurface == -1 && !sw->applicationViewValid);
    assert(sw->am2rMapSurfaceId == -1 && !sw->am2rMapSurfaceRetained);
    assert(!sw->am2rMapSurfaceReused && !sw->am2rMapSurfaceCacheValid);
    assert(liveBlocks == 0 && gpuLive == 0 && offscreenLive == 0 && textureLive == 0);
    assert(journalLive == 0 && journalBytes == 0 && journalDeallocations == 8);
    for (int id = 0; id <= 8; ++id) assert(sw->offscreenJournals[id] == NULL);
    assert(swCreateSurface(r, 2, 2) == 1); /* new VM session, unlike ordinary free */
}

int main(int argc, char **argv) {
    assert(argc == 2);
    SWRenderer sw; Runner runner; init(&sw, &runner);
    if (!strcmp(argv[1], "stale")) stale(&sw);
    else if (!strcmp(argv[1], "churn")) churn(&sw);
    else if (!strcmp(argv[1], "map")) retainedMap(&sw);
    else if (!strcmp(argv[1], "invalid")) invalid(&sw);
    else if (!strcmp(argv[1], "cap")) cap(&sw);
    else if (!strcmp(argv[1], "restore")) restore(&sw);
    else if (!strcmp(argv[1], "reset")) reset(&sw);
    else assert(!"unknown test case");
    finish(&sw); printf("PASS %s\n", argv[1]); return 0;
}
