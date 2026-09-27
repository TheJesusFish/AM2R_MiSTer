// Ordinary-RAM contract test. Production functions are extracted by the driver.
#undef NDEBUG
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define MISTER_GPU_BLEND_NORMAL 0u
#define MISTER_GPU_BLEND_ADDITIVE 1u
#define MISTER_GPU_BLEND_SUBTRACT 2u
#define logWarn(...) ((void)0)
#define logError(...) ((void)0)
#include "offscreen_types.inc"

static MisterGpuCommand commands[GPU_COMMAND_CAPACITY];
static MisterGpuCommand* g_gpu_commands = commands;
static MisterGpuTexture g_gpu_texture_records[GPU_TEXTURE_RECORDS];
static uint32_t g_gpu_texture_record_count, g_gpu_command_count, g_gpu_frame_serial;
static uint32_t g_gpu_write_buffer, g_gpu_last_completed_cycles;
static size_t g_gpu_texture_offset;
static uint32_t g_gpu_deferred_exports[GPU_TEXTURE_RECORDS], g_gpu_deferred_export_count;
static uint32_t g_gpu_water_table_rows, g_gpu_water_direct_rows;
static bool g_gpu_frame_open, g_gpu_available, g_gpu_presented, g_gpu_software_frame;
static bool g_gpu_pending, g_gpu_floor_tint;
// This fixture deliberately tests the legacy crop backend only. Unified
// ownership has its own ordinary-RAM descriptor regression; these rejecting
// stubs ensure none of the old assertions accidentally take the new path.
static bool g_gpu_unified_enabled;
static bool MisterGpu_unifiedEnabled(void) { assert(!g_gpu_unified_enabled); return false; }
static bool MisterGpu_setUnifiedEnabled(bool enabled) { assert(!enabled); return false; }
static bool gpuSurfacePreparePresent(void) { assert(!g_gpu_unified_enabled); return false; }
static bool MisterGpu_surfacePush(const uint64_t words[8], const uint64_t setup[8]) {
    (void)words; (void)setup; assert(!g_gpu_unified_enabled); return false;
}
static MisterGpuCommand* gpuLastBuiltCommand(void) {
    assert(!g_gpu_unified_enabled && g_gpu_command_count);
    return &g_gpu_commands[g_gpu_command_count - 1u];
}
static MisterGpuOffscreen g_gpu_offscreen[GPU_OFFSCREEN_RECORDS];
static uint32_t g_gpu_offscreen_index = UINT32_MAX;
static const void* g_gpu_offscreen_key;
static uint32_t g_gpu_offscreen_revision, g_gpu_offscreen_start, g_gpu_scene_start;
static bool g_gpu_offscreen_allowed, g_gpu_offscreen_needs_scene_clear;
static uint64_t g_gpu_opaque_coverage[1];
static unsigned waits, submissions, optimizer_calls;

static bool waitGpuCompletion(uint32_t* cycles) { (void)cycles; waits++; return true; }
static bool submitGpuCommandsAsync(void) { submissions++; return true; }
static bool appendGpuEnd(void) {
    if (g_gpu_command_count == GPU_COMMAND_CAPACITY) return false;
    memset(&g_gpu_commands[g_gpu_command_count++], 0, sizeof(MisterGpuCommand));
    return true;
}
static bool axisCommandCanOverwrite(const MisterGpuCommand* c) { return c->word[0] == 2u; }
static bool axisCommandSourceIsOpaque(const MisterGpuCommand* c) { return c->word[6] == 255u; }
static void markAxisCommandCoverage(const MisterGpuCommand* c) {
    if (c->word[6] == 255u) g_gpu_opaque_coverage[0] = 1;
}
static bool coverageIsFull(void) { return g_gpu_opaque_coverage[0] != 0; }
static uint32_t tryFuseWaterEffect(void) { optimizer_calls++; assert(g_gpu_commands[0].word[0] == 1); return 7; }
static void finalizeWaterExports(void) { optimizer_calls++; }
static uint32_t tryFuseRepeatedAdditiveTiles(void) { optimizer_calls++; return 3; }
static bool tryFuseMapBackground(void) { optimizer_calls++; return false; }
#include "offscreen_production.inc"

static const void* key(unsigned value) { return (const void*)(uintptr_t)(value * 4096u); }
static void reset(void) {
    memset(commands, 0, sizeof(commands));
    memset(g_gpu_offscreen, 0, sizeof(g_gpu_offscreen));
    memset(g_gpu_texture_records, 0, sizeof(g_gpu_texture_records));
    g_gpu_commands = commands;
    g_gpu_frame_serial = g_gpu_write_buffer = g_gpu_texture_offset = 0;
    gpuArenaReset();
    g_gpu_deferred_export_count = 0;
    g_gpu_available = true;
    g_gpu_floor_tint = false;
    g_gpu_pending = false;
    waits = submissions = optimizer_calls = 0;
    g_gpu_texture_record_count = 1;
    g_gpu_texture_records[0].physical[0] = GPU_TEXTURE_PHYS + 16u * 1024u * 1024u;
    g_gpu_texture_records[0].bytes = 1024u * 1024u * 4u;
    g_gpu_texture_records[0].shadow_valid = true;
    MisterGpu_beginFrame();
}
static bool atlasBlit(void) {
    return MisterGpu_addBlit(g_gpu_texture_records[0].physical[0], 4096,
        -10, -10, 128, 128, 0, 0, 65536, 65536, UINT32_MAX, MISTER_GPU_BLEND_SUBTRACT);
}
static bool floorAtlasBlit(void) {
    return MisterGpu_addBlitFloorTint(g_gpu_texture_records[0].physical[0], 4096,
        -10, -10, 128, 128, 0, 0, 65536, 65536, 0x7f83bd6fu, MISTER_GPU_BLEND_SUBTRACT);
}
static uint32_t pass(unsigned id, unsigned revision) {
    assert(MisterGpu_beginOffscreen(key(id), revision));
    assert(MisterGpu_addClear(0xffbbbbbb));
    assert(atlasBlit());
    return MisterGpu_commitOffscreen();
}
int main(void) {
    reset();
    assert(!MisterGpu_hasFloorTint());
    assert(MisterGpu_beginOffscreen(key(1), 1));
    assert(MisterGpu_addClear(0));
    MisterGpuCommand oldClear = commands[0];
    assert(!floorAtlasBlit() && g_gpu_command_count == 1);
    assert(memcmp(&oldClear, &commands[0], sizeof(oldClear)) == 0);
    assert(atlasBlit());
    commands[1].word[0] |= 1u << 10; // Bypassing the builder must still be rejected.
    assert(MisterGpu_commitOffscreen() == 0 && g_gpu_command_count == 0);

    reset();
    g_gpu_floor_tint = true;
    assert(MisterGpu_hasFloorTint());
    g_gpu_available = false;
    assert(!MisterGpu_hasFloorTint() && !floorAtlasBlit());
    assert(g_gpu_command_count == 0);
    g_gpu_available = true;
    assert(MisterGpu_beginOffscreen(key(1), 1));
    assert(MisterGpu_addClear(0));
    assert(floorAtlasBlit());
    assert((uint16_t)commands[1].word[0] == (2u | (1u << 9) | (1u << 10)));
    assert(commands[1].word[6] == 0x7f83bd6fu);
    assert(atlasBlit());
    assert((uint16_t)commands[2].word[0] == (2u | (1u << 9)));
    assert(MisterGpu_commitOffscreen() != 0 && commands[3].word[0] == 6u);
    assert(MisterGpu_addClear(0) && commands[4].word[0] == 1u);
    assert(atlasBlit() && !(commands[5].word[0] & (1u << 10)));
    MisterGpuCommand oldBlit = commands[5];
    g_gpu_floor_tint = false;
    assert(!floorAtlasBlit() && g_gpu_command_count == 6);
    assert(memcmp(&oldBlit, &commands[5], sizeof(oldBlit)) == 0);

    reset();
    g_gpu_floor_tint = true;
    g_gpu_command_count = GPU_COMMAND_CAPACITY - 1u;
    commands[g_gpu_command_count - 1u].word[0] = 2u;
    oldBlit = commands[g_gpu_command_count - 1u];
    assert(!floorAtlasBlit() && g_gpu_command_count == GPU_COMMAND_CAPACITY - 1u);
    assert(memcmp(&oldBlit, &commands[g_gpu_command_count - 1u], sizeof(oldBlit)) == 0);

    reset();
    g_gpu_floor_tint = true;
    assert(MisterGpu_beginOffscreen(key(1), 1));
    assert(MisterGpu_addClear(0));
    assert(MisterGpu_addFill(0, 0, 1, 1, 0, 0));
    commands[1].word[0] |= 1u << 10;
    assert(MisterGpu_commitOffscreen() == 0 && g_gpu_command_count == 0);

    reset();
    g_gpu_deferred_exports[0] = 0x25000000;
    g_gpu_deferred_export_count = 1;
    MisterGpu_beginFrame();
    assert(g_gpu_command_count == 1 && g_gpu_scene_start == 1);
    assert(commands[0].word[0] == 6 && commands[0].word[1] == 0x25000000);
    uint32_t address = pass(1, 7);
    assert(address != 0 && g_gpu_scene_start == 4 && g_gpu_command_count == 4);
    assert(commands[3].word[0] == 6 && commands[3].word[1] == address);
    assert(MisterGpu_findOffscreen(key(1), 7) == address);
    assert(MisterGpu_findOffscreen(key(1), 8) == 0);
    assert(!MisterGpu_finishFrame(NULL) && !atlasBlit());
    assert(waits == 0 && submissions == 0);
    assert(!MisterGpu_beginOffscreen(key(1), 8));
    MisterGpu_releaseOffscreen(key(1));
    assert(MisterGpu_findOffscreen(key(1), 7) == 0);
    uint32_t other = pass(2, 1);
    assert(other != 0 && other != address && commands[3].word[1] == address);
    assert(MisterGpu_addClear(0xff000000));
    assert(!MisterGpu_beginOffscreen(key(3), 1));
    assert(MisterGpu_finishFrame(NULL));
    assert(waits == 1 && submissions == 1 && !g_gpu_frame_open);
    assert(MisterGpu_findOffscreen(key(2), 1) == 0);

    g_gpu_write_buffer = 1;
    MisterGpu_beginFrame();
    assert(MisterGpu_findOffscreen(key(2), 1) == 0);
    uint32_t reused = pass(3, 2);
    assert(reused == address + MISTER_FB_BYTES && waits == 1);
    MisterGpu_abortOffscreen(); // no active transaction: preserve committed work
    assert(g_gpu_command_count == 3);

    reset();
    assert(MisterGpu_beginOffscreen(key(1), 1));
    assert(MisterGpu_addClear(0));
    assert(atlasBlit());
    assert(!MisterGpu_finishFrame(NULL));
    MisterGpu_abortOffscreen();
    assert(g_gpu_command_count == 0 && g_gpu_scene_start == 0);
    assert(MisterGpu_findOffscreen(key(1), 1) == 0);
    assert(MisterGpu_beginOffscreen(key(1), 1));
    assert(MisterGpu_commitOffscreen() == 0); // empty
    assert(g_gpu_command_count == 0);
    assert(MisterGpu_beginOffscreen(key(1), 1));
    assert(atlasBlit());
    assert(MisterGpu_commitOffscreen() == 0); // no initializing clear
    assert(g_gpu_command_count == 0);

    reset();
    g_gpu_texture_records[0].dynamic = true;
    assert(pass(1, 1) == 0 && g_gpu_command_count == 0);
    g_gpu_texture_records[0].dynamic = false;
    g_gpu_texture_records[0].released = true;
    assert(pass(1, 1) == 0);
    g_gpu_texture_records[0].released = false;
    g_gpu_texture_records[0].shadow_valid = false;
    assert(pass(1, 1) == 0);
    g_gpu_texture_records[0].shadow_valid = true;
    g_gpu_texture_records[0].bytes = 4;
    assert(pass(1, 1) == 0); // source range escapes stable allocation
    g_gpu_texture_records[0].bytes = 4194304;
    assert(MisterGpu_beginOffscreen(key(1), 1));
    assert(MisterGpu_addClear(0));
    assert(MisterGpu_addBlit(g_gpu_texture_records[0].physical[0], 4096,
        0, 0, 32, 32, -65536, 0, 65536, 65536, UINT32_MAX, 0));
    assert(MisterGpu_commitOffscreen() == 0);

    reset();
    assert(reserveGpuTexture(GPU_TEXTURE_BYTES - MISTER_FB_BYTES));
    size_t originalOffset = g_gpu_texture_offset;
    assert(!MisterGpu_beginOffscreen(key(1), 1));
    assert(g_gpu_texture_offset == originalOffset && g_gpu_command_count == 0);

    reset();
    for (unsigned i = 0; i < GPU_OFFSCREEN_RECORDS; ++i) assert(pass(i + 1, 1));
    assert(!MisterGpu_beginOffscreen(key(100), 1));
    for (unsigned i = 0; i < GPU_OFFSCREEN_RECORDS; ++i) MisterGpu_releaseOffscreen(key(i + 1));
    assert(!MisterGpu_beginOffscreen(key(100), 1)); // same-frame quarantine
    g_gpu_write_buffer = 1;
    MisterGpu_beginFrame();
    assert(pass(100, 1) != 0 && waits == 0);

    reset();
    assert(MisterGpu_beginOffscreen(key(1), 1));
    assert(MisterGpu_addClear(0));
    while (g_gpu_command_count < GPU_COMMAND_CAPACITY - 2u)
        assert(MisterGpu_addFill(0, 0, 1, 1, 0, 0));
    assert(MisterGpu_commitOffscreen() == 0 && g_gpu_command_count == 0);
    assert(MisterGpu_beginOffscreen(key(1), 1));
    assert(MisterGpu_addClear(0));
    while (g_gpu_command_count < GPU_COMMAND_CAPACITY - 3u)
        assert(MisterGpu_addFill(0, 0, 1, 1, 0, 0));
    assert(MisterGpu_commitOffscreen() != 0);
    assert(MisterGpu_addClear(0));
    assert(MisterGpu_finishFrame(NULL) && g_gpu_command_count == GPU_COMMAND_CAPACITY);

    reset();
    assert(pass(1, 1));
    MisterGpuCommand prefix[3];
    memcpy(prefix, commands, sizeof(prefix));
    assert(MisterGpu_addClear(0));
    commands[g_gpu_command_count].word[0] = 2;
    commands[g_gpu_command_count++].word[6] = 255; // mock opaque coverage proof
    uint32_t water, repeated;
    optimizeGpuScene(&water, &repeated);
    assert(water == 7 && repeated == 3 && optimizer_calls == 4);
    assert(g_gpu_commands == commands && g_gpu_command_count == 4);
    assert(memcmp(prefix, commands, sizeof(prefix)) == 0);
    assert(commands[3].word[0] == 2);

    reset(); // culling must preserve side effects even within the scene suffix
    commands[0].word[0] = 1;
    commands[1].word[0] = 6;
    commands[2].word[0] = 3;
    commands[3].word[0] = 2;
    commands[3].word[6] = 255;
    g_gpu_command_count = 4;
    assert(cullFullyCoveredPrefix() == 1 && g_gpu_command_count == 3);
    assert(commands[0].word[0] == 1 && commands[1].word[0] == 6 && commands[2].word[0] == 2);

    reset();
    assert(pass(1, 1));
    MisterGpu_useSoftwareFrame();
    assert(MisterGpu_findOffscreen(key(1), 1) == 0);
    MisterGpu_continueFrame();
    assert(!MisterGpu_beginOffscreen(key(2), 1));
    assert(MisterGpu_findOffscreen(key(1), 1) == 0);
    puts("Offscreen backend transactions, source bounds, ownership, capacity, optimizer barriers, floor-tint capability gates and descriptor isolation PASS");
    return 0;
}
