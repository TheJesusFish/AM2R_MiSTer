/* Actual cache-hit helper/public wrapper, with ordinary-RAM backend mocks.
 * These checks prove dispatch and ordering, not FPGA pixels or frame rate. */
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define GPU_TEXTURE_RECORDS 8u
#define TEST_BYTES 64u
#define RD_SCOPE(operation, surface, pixels) ((void)0)
#include "texture_cache_hit_types.inc"

static MisterGpuTexture g_gpu_texture_records[GPU_TEXTURE_RECORDS];
static uint32_t g_gpu_texture_record_count;
static bool g_gpu_available, g_gpu_unified_enabled, g_gpu_surface_failed;
static bool flushSucceeds;
static uint8_t shadows[GPU_TEXTURE_RECORDS][TEST_BYTES];
static uint8_t gpuPixels[GPU_TEXTURE_RECORDS][2][TEST_BYTES];
static uint32_t nextPhysical = 0x1000u;
static unsigned flushCalls, uploadCalls, writes;
static char events[32];
static unsigned eventCount;
static bool pendingRead;
static uint32_t pendingPhysical;
static size_t pendingBytes;
static uint8_t pendingPixels[TEST_BYTES];

static bool MisterGpu_unifiedEnabled(void) {
    return g_gpu_unified_enabled && g_gpu_available && !g_gpu_surface_failed;
}

static void event(char kind) {
    assert(eventCount + 1u < sizeof(events));
    events[eventCount++] = kind;
    events[eventCount] = '\0';
}

static uint8_t* pixelsFor(uint32_t physical) {
    for (uint32_t i = 0; i < g_gpu_texture_record_count; ++i)
        for (unsigned copy = 0; copy < 2; ++copy)
            if (g_gpu_texture_records[i].physical[copy] == physical)
                return gpuPixels[i][copy];
    assert(!"unknown GPU address");
    return NULL;
}

static void queueRead(uint32_t physical, size_t bytes) {
    assert(!pendingRead && bytes <= TEST_BYTES);
    pendingRead = true;
    pendingPhysical = physical;
    pendingBytes = bytes;
    memcpy(pendingPixels, pixelsFor(physical), bytes);
}

static bool MisterGpu_flushNoPresent(void) {
    ++flushCalls;
    event('F');
    if (!MisterGpu_unifiedEnabled() || !flushSucceeds) return false;
    /* Earlier descriptors must consume their old pixels before any upload. */
    if (pendingRead) {
        assert(memcmp(pixelsFor(pendingPhysical), pendingPixels, pendingBytes) == 0);
        pendingRead = false;
    }
    return true;
}

static uint32_t uploadDynamicTexture(const void* source, size_t bytes,
                                     bool revisionValid, uint32_t revision) {
    ++uploadCalls;
    event('U');
    if (!g_gpu_available || !source || !bytes) return 0;
    assert(bytes <= TEST_BYTES);
    if (g_gpu_unified_enabled) {
        assert(eventCount >= 2 && events[eventCount - 2] == 'F');
        assert(!pendingRead); /* A wrapper miss cannot update queued readers. */
    }
    uint32_t slot;
    for (slot = 0; slot < g_gpu_texture_record_count; ++slot) {
        MisterGpuTexture* record = &g_gpu_texture_records[slot];
        if (!record->released && record->source == source && record->bytes == bytes)
            break;
    }
    if (slot == g_gpu_texture_record_count) {
        assert(slot < GPU_TEXTURE_RECORDS);
        ++g_gpu_texture_record_count;
    }
    MisterGpuTexture* record = &g_gpu_texture_records[slot];
    /* This mock repairs invalid fixture records instead of implementing the
     * production allocator. Its observable contract is fence-before-write. */
    if (record->active >= 2 || !record->physical[record->active]) {
        record->active = 0;
        record->physical[0] = nextPhysical;
        record->physical[1] = nextPhysical + 0x80u;
        nextPhysical += 0x100u;
    }
    record->source = source;
    record->bytes = record->capacity = bytes;
    record->shadow = shadows[slot];
    record->shadow_valid = true;
    record->released = false;
    record->dynamic = true;
    record->last_upload_frame = 7;
    ++record->generation;
    record->source_revision = revision;
    record->source_revision_valid = revisionValid;
    memcpy(record->shadow, source, bytes);
    memcpy(gpuPixels[slot][record->active], source, bytes);
    ++writes;
    return record->physical[record->active];
}

#include "texture_cache_hit_actual.inc"

static void clearCounters(void) {
    flushCalls = uploadCalls = writes = eventCount = 0;
    events[0] = '\0';
}

static void reset(void) {
    memset(g_gpu_texture_records, 0, sizeof(g_gpu_texture_records));
    memset(shadows, 0, sizeof(shadows));
    memset(gpuPixels, 0, sizeof(gpuPixels));
    g_gpu_texture_record_count = 0;
    g_gpu_available = g_gpu_unified_enabled = flushSucceeds = true;
    g_gpu_surface_failed = pendingRead = false;
    clearCounters();
}

static uint32_t seed(const void* source, size_t bytes) {
    uint32_t address = MisterGpu_uploadDynamicTexture(source, bytes);
    assert(address && flushCalls == 1 && uploadCalls == 1 && writes == 1);
    assert(strcmp(events, "FU") == 0);
    clearCounters();
    return address;
}

static void hits(void) {
    reset();
    const uint32_t white = 0xffffffffu;
    uint32_t address = seed(&white, sizeof(white));
    MisterGpuTexture before = g_gpu_texture_records[0];
    queueRead(address, sizeof(white));
    for (unsigned i = 0; i < 1000; ++i) {
        assert(MisterGpu_uploadDynamicTexture(&white, sizeof(white)) == address);
        assert(flushCalls == 0 && uploadCalls == 0 && writes == 0 && pendingRead);
    }
    assert(flushCalls == 0 && uploadCalls == 0 && writes == 0 && pendingRead);
    assert(memcmp(&before, &g_gpu_texture_records[0], sizeof(before)) == 0);
    assert(white == 0xffffffffu);
    /* A valid second copy is the returned address, not always physical[0]. */
    pendingRead = false;
    g_gpu_texture_records[0].active = 1;
    memcpy(gpuPixels[0][1], &white, sizeof(white));
    assert(MisterGpu_uploadDynamicTexture(&white, sizeof(white)) ==
           g_gpu_texture_records[0].physical[1]);
    assert(flushCalls == 0 && uploadCalls == 0 && writes == 0);
    puts("Repeated constant cache hits and active-copy identity PASS");
}

static void changed(void) {
    reset();
    uint32_t pixel = 0xffffffffu;
    uint32_t address = seed(&pixel, sizeof(pixel));
    queueRead(address, sizeof(pixel));
    pixel = 0xff010203u;
    uint32_t generation = g_gpu_texture_records[0].generation;
    assert(MisterGpu_uploadDynamicTexture(&pixel, sizeof(pixel)) == address);
    assert(strcmp(events, "FU") == 0);
    assert(flushCalls == 1 && uploadCalls == 1 && writes == 1 && !pendingRead);
    assert(g_gpu_texture_records[0].generation == generation + 1);
    assert(memcmp(pixelsFor(address), &pixel, sizeof(pixel)) == 0);
    assert(pixel == 0xff010203u);
    clearCounters();
    assert(MisterGpu_uploadDynamicTexture(&pixel, sizeof(pixel)) == address);
    assert(flushCalls == 0 && uploadCalls == 0);
    puts("Changed same-frame data retains fence-before-write order PASS");
}

static void lifetime(void) {
    reset();
    const uint32_t white = 0xffffffffu;
    uint32_t old = seed(&white, sizeof(white));
    g_gpu_texture_records[0].released = true;
    uint32_t replacement = MisterGpu_uploadDynamicTexture(&white, sizeof(white));
    assert(replacement && replacement != old && strcmp(events, "FU") == 0);
    clearCounters();
    assert(MisterGpu_uploadDynamicTexture(&white, sizeof(white)) == replacement);
    assert(flushCalls == 0 && uploadCalls == 0); /* Skip stale released entry. */
    /* Simulate a rebuilt backend table, retaining the same CPU source pointer. */
    reset();
    uint32_t restored = seed(&white, sizeof(white));
    assert(restored != old && restored != replacement);
    g_gpu_texture_records[0].physical[0] = nextPhysical;
    nextPhysical += 0x100u;
    assert(MisterGpu_uploadDynamicTexture(&white, sizeof(white)) ==
           g_gpu_texture_records[0].physical[0]);
    assert(flushCalls == 0 && uploadCalls == 0);
    puts("Release, pointer reuse, backend reset and current-address lookup PASS");
}

static void guards(void) {
    const uint32_t white = 0xffffffffu;
    for (unsigned fault = 0; fault < 9; ++fault) {
        reset();
        seed(&white, sizeof(white));
        MisterGpuTexture* record = &g_gpu_texture_records[0];
        switch (fault) {
        case 0: record->released = true; break;
        case 1: record->sparse = true; break;
        case 2: record->shadow = NULL; break;
        case 3:
            record->shadow_valid = false;
            record->source_revision = 77;
            record->source_revision_valid = true;
            break;
        case 4: record->physical[record->active] = 0; break;
        case 5: record->active = 2; break;
        case 6: record->source = NULL; break;
        case 7: record->bytes = 1; break;
        default:
            record->source_revision = 77;
            record->source_revision_valid = true;
            shadows[0][0] ^= 1; /* Revision alone cannot prove a plain hit. */
            break;
        }
        MisterGpuTexture before = *record;
        assert(gpuUnchangedDynamicTexture(&white, sizeof(white)) == 0);
        assert(memcmp(record, &before, sizeof(before)) == 0);
        assert(flushCalls == 0 && uploadCalls == 0);
        assert(MisterGpu_uploadDynamicTexture(&white, sizeof(white)) != 0);
        assert(flushCalls == 1 && uploadCalls == 1 && strcmp(events, "FU") == 0);
        assert(white == 0xffffffffu);
    }
    puts("Released/sparse/shadow/address/index/key/size/content guards PASS");
}

static void metadata(void) {
    reset();
    const uint32_t white = 0xffffffffu;
    uint32_t address = seed(&white, sizeof(white));
    g_gpu_texture_records[0].source_revision = 99;
    g_gpu_texture_records[0].source_revision_valid = true;
    MisterGpuTexture before = g_gpu_texture_records[0];
    assert(MisterGpu_uploadDynamicTexture(&white, sizeof(white)) == address);
    assert(g_gpu_texture_records[0].source_revision == 0);
    assert(!g_gpu_texture_records[0].source_revision_valid);
    before.source_revision = 0;
    before.source_revision_valid = false;
    assert(memcmp(&before, &g_gpu_texture_records[0], sizeof(before)) == 0);
    assert(flushCalls == 0 && uploadCalls == 0 && writes == 0);
    puts("Mixed versioned/plain bookkeeping clears old revision memo PASS");
}

static void failure(void) {
    const uint32_t white = 0xffffffffu;
    for (unsigned fault = 0; fault < 2; ++fault) {
        reset();
        seed(&white, sizeof(white));
        if (fault) g_gpu_available = false;
        else g_gpu_surface_failed = true;
        if (fault) assert(gpuUnchangedDynamicTexture(&white, sizeof(white)) == 0);
        /* The wrapper owns unified health validation; the helper is only
         * reached after it, so failure must not even clear revision metadata. */
        g_gpu_texture_records[0].source_revision = 99;
        g_gpu_texture_records[0].source_revision_valid = true;
        assert(MisterGpu_uploadDynamicTexture(&white, sizeof(white)) == 0);
        assert(uploadCalls == 0 && writes == 0);
        assert(g_gpu_texture_records[0].source_revision == 99);
        assert(g_gpu_texture_records[0].source_revision_valid);
    }
    reset();
    flushSucceeds = false; /* Healthy cache miss, failing required fence. */
    assert(MisterGpu_uploadDynamicTexture(&white, sizeof(white)) == 0);
    assert(flushCalls == 1 && uploadCalls == 0 && writes == 0);
    reset();
    assert(gpuUnchangedDynamicTexture(NULL, sizeof(white)) == 0);
    assert(gpuUnchangedDynamicTexture(&white, 0) == 0);
    assert(MisterGpu_uploadDynamicTexture(NULL, sizeof(white)) == 0);
    assert(MisterGpu_uploadDynamicTexture(&white, 0) == 0);
    assert(writes == 0 && g_gpu_texture_record_count == 0);
    puts("Unavailable/failed GPU, failing miss barrier and invalid inputs PASS");
}

static void legacy(void) {
    reset();
    const uint32_t white = 0xffffffffu;
    uint32_t address = seed(&white, sizeof(white));
    g_gpu_unified_enabled = false;
    g_gpu_texture_records[0].source_revision = 99;
    g_gpu_texture_records[0].source_revision_valid = true;
    assert(MisterGpu_uploadDynamicTexture(&white, sizeof(white)) == address);
    assert(flushCalls == 0 && uploadCalls == 1 && strcmp(events, "U") == 0);
    assert(g_gpu_texture_records[0].source_revision == 0);
    assert(!g_gpu_texture_records[0].source_revision_valid);
    puts("Nonunified dispatch retains original uploader path PASS");
}

static void immutableInputs(void) {
    reset();
    uint8_t input[TEST_BYTES + 1], before[sizeof(input)];
    for (unsigned i = 0; i < sizeof(input); ++i) input[i] = (uint8_t)(i * 7u);
    memcpy(before, input, sizeof(input));
    /* Odd-sized unaligned content also exercises gpuBuffersEqual's fallback. */
    uint32_t address = seed(input + 1, TEST_BYTES - 1);
    uint8_t shadowBefore[TEST_BYTES];
    memcpy(shadowBefore, shadows[0], sizeof(shadowBefore));
    assert(MisterGpu_uploadDynamicTexture(input + 1, TEST_BYTES - 1) == address);
    assert(memcmp(input, before, sizeof(input)) == 0);
    assert(memcmp(shadows[0], shadowBefore, sizeof(shadowBefore)) == 0);
    assert(flushCalls == 0 && uploadCalls == 0 && writes == 0);
    puts("Caller pixels and cached shadow remain immutable on hit PASS");
}

int main(int argc, char** argv) {
    assert(argc == 2);
    if (strcmp(argv[1], "hits") == 0) hits();
    else if (strcmp(argv[1], "changed") == 0) changed();
    else if (strcmp(argv[1], "lifetime") == 0) lifetime();
    else if (strcmp(argv[1], "guards") == 0) guards();
    else if (strcmp(argv[1], "metadata") == 0) metadata();
    else if (strcmp(argv[1], "failure") == 0) failure();
    else if (strcmp(argv[1], "legacy") == 0) legacy();
    else if (strcmp(argv[1], "immutable") == 0) immutableInputs();
    else assert(!"unknown test case");
    return 0;
}
