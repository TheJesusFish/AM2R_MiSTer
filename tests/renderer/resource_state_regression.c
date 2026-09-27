/* Compile the actual diagnostic helper with real Renderer/SWRenderer layouts.
 * All GPU globals are ordinary-RAM metadata. Pixel pointers are intentionally
 * invalid sentinels: the helper must not inspect pixels or mapped hardware. */
#undef NDEBUG
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sw_renderer.h"
#include "runner.h"
#include "resource_state_types.inc"

static MisterGpuTexture g_gpu_texture_records[GPU_TEXTURE_RECORDS];
static MisterGpuOffscreen g_gpu_offscreen[GPU_OFFSCREEN_RECORDS];
static GpuSurface g_gpu_surfaces[GPU_SURFACE_RECORDS];
static uint32_t g_gpu_texture_record_count, g_gpu_frame_serial;
static size_t g_gpu_texture_offset;
static bool g_gpu_available, g_gpu_surface_failed, g_gpu_pending;
static bool g_vblank_status_disabled;
static uint32_t g_vblank_timeout_count;
static const char* setting;
static unsigned environmentReads, logCount;
static char lastLog[4096];

static char* resourceGetenv(const char* key) {
    assert(strcmp(key, "AM2R_RESOURCE_SAMPLES") == 0);
    ++environmentReads;
    return (char*)setting;
}
static void resourceLog(const char* format, ...) {
    va_list args;
    va_start(args, format);
    int length = vsnprintf(lastLog, sizeof(lastLog), format, args);
    va_end(args);
    assert(length > 0 && (size_t)length < sizeof(lastLog));
    ++logCount;
}
#define getenv resourceGetenv
#define logInfo resourceLog
#include "resource_state_production.inc"
#undef getenv
#undef logInfo

int main(int argc, char** argv) {
    assert(argc == 3);
    setting = strcmp(argv[1], "unset") ? argv[1] : NULL;
    const bool truncated = !strcmp(argv[2], "truncated");
    const bool legacy = !strcmp(argv[2], "legacy");
    const bool uninitialized = !strcmp(argv[2], "uninitialized");
    assert(gpuResourceSampleLimit(NULL) == 0);
    assert(gpuResourceSampleLimit("") == 0);
    assert(gpuResourceSampleLimit("720") == 720);
    assert(gpuResourceSampleLimit("721") == 0);
    assert(gpuResourceSampleLimit("-1") == 0);
    assert(gpuResourceSampleLimit("+2") == 0);
    assert(gpuResourceSampleLimit("1 ") == 0);
    assert(gpuResourceSampleLimit("/private/path") == 0);
    assert(gpuResourceSampleLimit("0000") == 0);

    Runner runner = {0};
    SWRenderer sw = {0};
    bool exists[128] = {true, false, true, true};
    bool loaded[128] = {true, false, true, true};
    uint32_t addresses[128] = {0x24000000u, 0u, 0u, 0x24010000u};
    sw.base.runner = &runner;
    sw.base.currentShader = -1;
    runner.currentRoomIndex = 166;
    sw.surfaceExistsFlags = exists;
    sw.surfaceCount = truncated ? UINT32_MAX : 4;
    sw.surfaceCapacity = truncated ? 128u : 4u;
    sw.textureCount = truncated ? UINT32_MAX : 4;
    sw.textureLoaded = loaded;
    sw.gpuTextureAddresses = addresses;
    sw.currentSurface = 3;
    sw.unifiedEnabled = !legacy;
    sw.unifiedStrict = !legacy;
    sw.gpuFrameEligible = legacy;
    sw.offscreenEnabled = legacy;
    sw.blendEnable = true;
    sw.blendMode = bm_subtract;
    sw.blendFactors = (BlendFactors){bm_one, bm_one, bm_src_alpha, bm_inv_src_alpha};
    sw.fogEnable = true;
    sw.colorWriteR = sw.colorWriteB = sw.colorWriteA = true;
    sw.alphaTestEnable = true;
    sw.alphaTestRef = 127;
    sw.framebuffer = (uint8_t*)(uintptr_t)1;
    sw.surfacePixels = (uint8_t**)(uintptr_t)1;
    sw.texturePixels = (uint8_t**)(uintptr_t)1;
    g_gpu_available = true;
    g_gpu_pending = true;
    g_gpu_texture_record_count = truncated ? UINT32_MAX : 4;
    g_gpu_texture_offset = 9876543;
    g_gpu_arena_initialized = !uninitialized;
    g_gpu_arena_generation = 55;
    // Two independently owned allocations, one deliberately absent from the
    // texture/target records (a pinned internal allocation). Free intervals
    // include the final metadata slot; unused capacity must not be counted.
    g_gpu_arena[0] = (GpuArenaSegment){.capacity=128, .generation=1, .state=2};
    g_gpu_arena[17] = (GpuArenaSegment){.offset=128, .capacity=256, .state=1};
    g_gpu_arena[511] = (GpuArenaSegment){.offset=384, .capacity=1024, .generation=2, .state=2};
    g_gpu_arena[1023] = (GpuArenaSegment){.offset=1408, .capacity=GPU_TEXTURE_BYTES-1408u, .state=1};
    g_gpu_arena[700].capacity = 777; // unused metadata is ignored
    if (truncated)
        for (unsigned i=0; i<GPU_ARENA_SEGMENTS; ++i)
            g_gpu_arena[i] = (GpuArenaSegment){.offset=i*128u, .capacity=128, .generation=i+1u, .state=2};
    g_gpu_frame_serial = 91;
    g_vblank_status_disabled = true;
    g_vblank_timeout_count = 3;
    for (unsigned i = 0; i < GPU_TEXTURE_RECORDS; ++i) {
        g_gpu_texture_records[i].released = true;
        g_gpu_texture_records[i].source = (void*)(uintptr_t)1;
        g_gpu_texture_records[i].shadow = (void*)(uintptr_t)1;
    }
    g_gpu_texture_records[0].released = false;
    g_gpu_texture_records[0].physical[0] = 0x24000000u;
    g_gpu_texture_records[2].released = false; // live but missing address
    g_gpu_texture_records[3].released = false;
    g_gpu_texture_records[3].active = 255; // invalid active slot must not index OOB
    g_gpu_offscreen[0].key = (void*)(uintptr_t)1;
    g_gpu_offscreen[0].committed_frame = 91;
    g_gpu_offscreen[1].key = (void*)(uintptr_t)1;
    g_gpu_offscreen[1].committed_frame = 90;
    g_gpu_surfaces[0].capacity = 128;
    g_gpu_surfaces[0].live = true;
    g_gpu_surfaces[3].physical = 0x24010000u;

    MisterGpuTexture textureBefore[GPU_TEXTURE_RECORDS];
    MisterGpuOffscreen offscreenBefore[GPU_OFFSCREEN_RECORDS];
    GpuSurface surfacesBefore[GPU_SURFACE_RECORDS];
    GpuArenaSegment arenaBefore[GPU_ARENA_SEGMENTS];
    memcpy(textureBefore, g_gpu_texture_records, sizeof(textureBefore));
    memcpy(offscreenBefore, g_gpu_offscreen, sizeof(offscreenBefore));
    memcpy(surfacesBefore, g_gpu_surfaces, sizeof(surfacesBefore));
    memcpy(arenaBefore, g_gpu_arena, sizeof(arenaBefore));
    SWRenderer swBefore = sw;
    MisterGpu_logResourceState(NULL);
    Runner* savedRunner = sw.base.runner;
    sw.base.runner = NULL;
    MisterGpu_logResourceState(&sw.base);
    sw.base.runner = savedRunner;
    for (int32_t frame = -1; frame <= 216300; ++frame) {
        runner.frameCount = frame;
        MisterGpu_logResourceState(&sw.base);
        if (frame % 300 == 0) MisterGpu_logResourceState(&sw.base); // no duplicate
    }
    assert(environmentReads == 1);
    assert(logCount == gpuResourceSampleLimit(setting));
    assert(memcmp(textureBefore, g_gpu_texture_records, sizeof(textureBefore)) == 0);
    assert(memcmp(offscreenBefore, g_gpu_offscreen, sizeof(offscreenBefore)) == 0);
    assert(memcmp(surfacesBefore, g_gpu_surfaces, sizeof(surfacesBefore)) == 0);
    assert(memcmp(arenaBefore, g_gpu_arena, sizeof(arenaBefore)) == 0);
    assert(g_gpu_arena_initialized == !uninitialized && g_gpu_arena_generation == 55);
    assert(memcmp(&swBefore, &sw, sizeof(sw)) == 0);
    assert(g_gpu_texture_offset == 9876543 && g_gpu_frame_serial == 91);
    assert(g_gpu_pending && g_gpu_available && g_vblank_status_disabled);
    assert(!g_gpu_surface_failed && g_vblank_timeout_count == 3);
    if (logCount) fputs(lastLog, stdout);
    printf("QA_RESOURCE samples=%u env_reads=%u read_only=1\n", logCount, environmentReads);
    return 0;
}
