/* Production helper extraction, ordinary-RAM allocation/lifecycle evidence.
 * GPU completion is mocked successful and no pixel engine is executed. Each
 * case has at most one user allocation live except explicitly pinned source.
 */
#undef NDEBUG
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#if defined(__ARM_NEON)
#include <arm_neon.h>
#endif
#include "mister_gpu.h"
#include "allocator_types.inc"
#include "../render_diagnostics.h"
#define logWarn(...) ((void)0)
#define logInfo(...) ((void)0)
#define GPU_TEXTURE_COPY(d,s,n) memcpy((d),(s),(n))

static uint8_t* g_gpu_textures;
static MisterGpuTexture g_gpu_texture_records[GPU_TEXTURE_RECORDS];
static uint32_t g_gpu_texture_record_count, g_gpu_texture_generation, g_gpu_frame_serial;
static size_t g_gpu_texture_offset;
static MisterGpuCommand commands[GPU_COMMAND_CAPACITY];
static MisterGpuCommand* g_gpu_commands=commands;
static uint32_t g_gpu_command_count,g_gpu_sequence,g_gpu_write_buffer;
static bool g_gpu_available=true,g_gpu_surface_targets=true,g_gpu_generic_primitive=true;
static bool g_gpu_unified_enabled,g_gpu_offscreen_allowed,g_gpu_offscreen_needs_scene_clear;
static bool g_gpu_frame_open,g_gpu_presented,g_gpu_software_frame;
static uint32_t g_gpu_offscreen_index=UINT32_MAX;
static uint32_t g_gpu_deferred_exports[GPU_TEXTURE_RECORDS],g_gpu_deferred_export_count;
static bool g_gpu_fused_base_valid,g_gpu_fused_valid,g_gpu_map_overlay_cache_valid,g_gpu_map_base_cache_valid;
static const MisterGpuTexture *g_gpu_fused_source_record,*g_gpu_fused_overlay_record;
static const MisterGpuTexture *g_gpu_map_overlay_cache_record,*g_gpu_map_base_cache_record;
static uint32_t g_gpu_fused_base_source_count;
static unsigned waits;
static bool failWait;
static int failMalloc=-1,mallocCalls;
static void* allocationMalloc(size_t bytes) {
    if(mallocCalls++==failMalloc)return NULL;
    return malloc(bytes);
}
static bool waitGpuCompletion(uint32_t* cycles) { if(cycles)*cycles=0;waits++;return !failWait; }
static bool submitGpuCommandsAsync(void) { g_gpu_sequence++;g_gpu_write_buffer^=1u;return true; }
static void gpuSurfaceCullOpaquePrefix(uint32_t start,uint32_t w,uint32_t h) {(void)start;(void)w;(void)h;}
bool MisterGpu_hasReplaceRect(void) { return true; }
static uint32_t uploadDynamicTexture(const void*,size_t,bool,uint32_t);
static uint32_t uploadSparseDynamicTexture(const void*,size_t,size_t,uint32_t);
#define malloc allocationMalloc
#include "allocator_production.inc"
#undef malloc
#if HAS_TEST_MANAGER
#include "allocator_manager.inc"
#else
bool MisterGpu_flushNoPresent(void) {return true;}
#endif

static void resetFixture(void) {
    // Independent test processes share one RAM backing for convenience; no
    // production reset/reclamation behavior is inferred from this cleanup.
    for(unsigned i=0;i<g_gpu_texture_record_count;i++) {
        free(g_gpu_texture_records[i].shadow);free(g_gpu_texture_records[i].shadow_alt);
        free(g_gpu_texture_records[i].opaque_pixels);
    }
    memset(g_gpu_texture_records,0,sizeof(g_gpu_texture_records));
    g_gpu_texture_record_count=g_gpu_texture_generation=g_gpu_frame_serial=0;
    g_gpu_texture_offset=0;g_gpu_command_count=g_gpu_sequence=g_gpu_write_buffer=0;
#if HAS_REUSABLE_ARENA
    gpuArenaReset();
#endif
#if HAS_TEST_MANAGER
    memset(g_gpu_surfaces,0,sizeof(g_gpu_surfaces));
    memset(g_gpu_surface_packet_banks,0,sizeof(g_gpu_surface_packet_banks));
    g_gpu_surface_batch_count=g_gpu_surface_selected=g_gpu_surface_present=g_gpu_surface_resident=0;
    g_gpu_surface_snapshot=g_gpu_surface_snapshot_capacity=0;
    g_gpu_surface_resident_dirty=g_gpu_surface_failed=g_gpu_unified_enabled=false;
#endif
    g_gpu_frame_open=g_gpu_presented=g_gpu_software_frame=false;
    g_gpu_offscreen_index=UINT32_MAX;failWait=false;failMalloc=-1;mallocCalls=0;
    g_gpu_deferred_export_count=0;waits=0;
}

static unsigned liveLegacy(void) {
    unsigned n=0;for(unsigned i=0;i<g_gpu_texture_record_count;i++)n+=!g_gpu_texture_records[i].released;
    return n;
}
static void legacyStableControl(uint8_t* pixels,bool dynamic) {
    resetFixture();size_t bytes=4096;
    for(unsigned i=0;i<1000;i++) {
        g_gpu_frame_serial++;
        uint32_t address=dynamic?uploadDynamicTexture(pixels,bytes,true,i):MisterGpu_uploadTexture(pixels,bytes);
        assert(address&&liveLegacy()==1&&g_gpu_texture_record_count==1);
        assert(g_gpu_texture_offset==bytes*(dynamic?2u:1u));
        MisterGpu_releaseTexture(pixels);assert(liveLegacy()==0);
    }
    printf("legacy %s control: 1000 same-size generations, records1, reserved%zu, live0\n",
           dynamic?"dynamic":"immutable",g_gpu_texture_offset);
}
static void legacyDistinctSizes(uint8_t* pixels,bool dynamic,bool poolFirst) {
    resetFixture();unsigned completed=0;size_t largest=0;
    const unsigned attempts=HAS_REUSABLE_ARENA?4096:GPU_TEXTURE_RECORDS+1u;
    for(unsigned i=0;i<attempts;i++) {
        size_t bytes=(poolFirst?1024u*1024u:4096u)+i*128u;
        g_gpu_frame_serial++;
        uint32_t address=dynamic?uploadDynamicTexture(pixels,bytes,true,i):MisterGpu_uploadTexture(pixels,bytes);
        if(!address)break;
        completed++;largest=bytes*(dynamic?2u:1u);assert(liveLegacy()==1);
        MisterGpu_releaseTexture(pixels);assert(liveLegacy()==0);
    }
    assert(completed&&liveLegacy()==0);
    if(HAS_REUSABLE_ARENA)assert(completed==attempts);
    else if(poolFirst)assert(g_gpu_texture_record_count<GPU_TEXTURE_RECORDS);
    else assert(g_gpu_texture_record_count==GPU_TEXTURE_RECORDS&&completed==GPU_TEXTURE_RECORDS);
    printf("legacy %s %s: completed%u released generations, records%u, reserved%zu, peaklive%zu, live0\n",
           dynamic?"dynamic":"immutable",poolFirst?"pool churn":"record churn",completed,
           g_gpu_texture_record_count,g_gpu_texture_offset,largest);
}
static void partialAllocationFailure(uint8_t* pixels) {
    for(unsigned kind=0;kind<4;kind++) {
        resetFixture();assert(reserveGpuTexture(GPU_TEXTURE_BYTES-128u));
        size_t before=g_gpu_texture_offset;uint32_t address;
        if(kind==0)address=uploadDynamicTexture(pixels,128,true,1);
        else if(kind==1)address=uploadSparseDynamicTexture(pixels,128,128,1);
        else if(kind==2)address=MisterGpu_uploadSparseDynamicTextureCropRevision(pixels,128,128,1,1);
        else address=MisterGpu_prepareDynamicTextureGpuWrite(pixels,128,1);
        assert(!address&&g_gpu_texture_record_count==0);
        assert(g_gpu_texture_offset==before+(EXPECT_LEGACY_ALLOCATION_LEAKS?128u:0u));
    }
    printf("four two-buffer constructors: allocation failure consumes %u unowned bytes each\n",
           EXPECT_LEGACY_ALLOCATION_LEAKS?128u:0u);
}
static void legacyModeConversionChurn(uint8_t* pixels,bool gpuWrite) {
    resetFixture();const size_t bytes=4096;
    for(unsigned i=0;i<1000;i++) {
        g_gpu_frame_serial++;
        uint32_t first=MisterGpu_uploadTexture(pixels,bytes);assert(first);
        uint8_t old=g_gpu_textures[first-GPU_TEXTURE_PHYS];
        pixels[0]^=1u; // Force the dynamic mutation, not the unchanged shortcut.
        uint32_t second=gpuWrite?MisterGpu_prepareDynamicTextureGpuWrite(pixels,bytes,i):
                                 uploadDynamicTexture(pixels,bytes,true,i);
        assert(second&&second!=first);
        assert(g_gpu_textures[first-GPU_TEXTURE_PHYS]==old); // Preserve immutable version.
        if(!gpuWrite)assert(memcmp(g_gpu_textures+second-GPU_TEXTURE_PHYS,pixels,bytes)==0);
        assert(g_gpu_texture_record_count==1&&liveLegacy()==1);
        assert(g_gpu_texture_offset==(EXPECT_LEGACY_ALLOCATION_LEAKS?i+2u:2u)*bytes);
        MisterGpu_releaseTexture(pixels);
    }
    printf("legacy immutable-to-%s reuse: 1000 same-size generations, records1, reserved%zu, owned%zu, retired-unowned%zu, live0\n",
           gpuWrite?"GPU-write":"dynamic",g_gpu_texture_offset,2u*bytes,g_gpu_texture_offset-2u*bytes);
}
#if HAS_ATOMIC_TEXTURE_PAIR
static void atomicPairContract(void) {
    const size_t bytes[]={1,127,128,129,255,256,257};
    for(unsigned i=0;i<sizeof(bytes)/sizeof(bytes[0]);i++) {
        resetFixture();uint32_t a=17,b=19;size_t capacity=(bytes[i]+127u)&~(size_t)127u;
        assert(reserveGpuTexturePair(bytes[i],&a,&b));
        assert(a==GPU_TEXTURE_PHYS&&b==a+capacity&&g_gpu_texture_offset==2u*capacity);
        assert(reserveGpuTexture(GPU_TEXTURE_BYTES-3u*capacity));
        a=17;b=19;assert(!reserveGpuTexturePair(bytes[i],&a,&b));
        assert(a==17&&b==19&&g_gpu_texture_offset==GPU_TEXTURE_BYTES-capacity);
    }
    resetFixture();uint32_t a=17,b=19;
    assert(!reserveGpuTexturePair(0,&a,&b));
    assert(!reserveGpuTexturePair(SIZE_MAX,&a,&b));
    assert(!reserveGpuTexturePair(SIZE_MAX/2u+1u,&a,&b));
    assert(a==17&&b==19&&g_gpu_texture_offset==0);
    puts("Atomic pair contract: alignment, cache padding, overflow and failure outputs preserved");
}
#endif
static void cropRestoreProof(uint8_t* pixels) {
    resetFixture();g_gpu_frame_serial=1;
    for(unsigned i=0;i<4096;i++)pixels[i]=(uint8_t)(i*17u+31u);
    uint32_t first=MisterGpu_uploadSparseDynamicTextureCropRevision(pixels,128,128,32,1);assert(first);
    uint32_t second=g_gpu_texture_records[0].physical[1];assert(second);
    MisterGpu_releaseTexture(pixels);
    memset(g_gpu_textures+first-GPU_TEXTURE_PHYS,0xa5,4096);
    memset(g_gpu_textures+second-GPU_TEXTURE_PHYS,0x5a,4096);
#if HAS_RESTORE_INVALIDATE
    gpuInvalidateReleasedTextureShadows();
#endif
    g_gpu_frame_serial++;
    assert(MisterGpu_uploadSparseDynamicTextureCropRevision(pixels,128,128,32,1)==first);
    if(HAS_RESTORE_INVALIDATE) {
        assert(!memcmp(g_gpu_textures+first-GPU_TEXTURE_PHYS,pixels,4096));
        assert(!memcmp(g_gpu_textures+second-GPU_TEXTURE_PHYS,pixels,4096));
    } else {
        assert(g_gpu_textures[first-GPU_TEXTURE_PHYS]==0xa5&&g_gpu_textures[second-GPU_TEXTURE_PHYS]==0x5a);
    }
    printf("Released crop checkpoint DDR poison: %s\n",HAS_RESTORE_INVALIDATE?"both copies restored exactly":"baseline stale-shadow defect reproduced");
}
static void cropLogicalRestoreProof(uint8_t* pixels) {
    resetFixture();g_gpu_frame_serial=1;memset(pixels,0x31,4096);
    assert(MisterGpu_uploadSparseDynamicTextureCropRevision(pixels,128,128,32,9));
    // .fast releases/recreates CPU game surfaces in this same process. Unlike
    // DMTCP, DDR and allocator/shadow metadata are not independently replaced.
    MisterGpu_releaseTexture(pixels);
    for(unsigned i=0;i<4096;i++)pixels[i]=(uint8_t)(i*29u+7u);
    g_gpu_frame_serial++;
    uint32_t restored=MisterGpu_uploadSparseDynamicTextureCropRevision(pixels,128,128,32,9);assert(restored);
    assert(!memcmp(g_gpu_textures+restored-GPU_TEXTURE_PHYS,pixels,4096));
    g_gpu_frame_serial++;pixels[31]^=0xff;
    restored=MisterGpu_uploadSparseDynamicTextureCropRevision(pixels,128,128,32,10);assert(restored);
    assert(!memcmp(g_gpu_textures+restored-GPU_TEXTURE_PHYS,pixels,4096));
    puts("Logical .fast backend lifecycle: released/recreated saved pixels and next alternate-buffer mutation exact");
}
#if HAS_REUSABLE_ARENA
static void assertArena(void) {
    uint64_t total=0;
    for(unsigned i=0;i<g_gpu_arena_slots;i++) {
        GpuArenaSegment* a=&g_gpu_arena[i];if(!a->state)continue;
        assert(a->capacity&&!(a->offset&127u)&&!(a->capacity&127u));
        assert((uint64_t)a->offset+a->capacity<=GPU_TEXTURE_BYTES);total+=a->capacity;
        for(unsigned j=i+1;j<g_gpu_arena_slots;j++) {
            GpuArenaSegment* b=&g_gpu_arena[j];if(!b->state)continue;
            assert(a->offset+a->capacity<=b->offset||b->offset+b->capacity<=a->offset);
            if(a->state==1&&b->state==1)
                assert(a->offset+a->capacity!=b->offset&&b->offset+b->capacity!=a->offset);
        }
    }
    assert(total==GPU_TEXTURE_BYTES);
}
static void arenaContract(void) {
    resetFixture();uint32_t a=reserveGpuTexture(129),b=reserveGpuTexture(257),c=reserveGpuTexture(128);
    assert(a&&b&&c&&gpuArenaParent(a+255,1)==a&&!gpuArenaParent(a+255,2));
    assert(!gpuArenaRelease(a+128));assert(gpuArenaRelease(b));
    assert(!gpuArenaParent(b,1)&&!gpuArenaRelease(b));
    assert(reserveGpuTexture(300)==b); // Smallest-fitting hole, not end growth.
    assert(gpuArenaRelease(a)&&gpuArenaRelease(b)&&gpuArenaRelease(c));assertArena();
    assert(reserveGpuTexture(GPU_TEXTURE_BYTES)==GPU_TEXTURE_PHYS);assert(!reserveGpuTexture(1));
    assert(gpuArenaRelease(GPU_TEXTURE_PHYS));
    uint32_t p,q;assert(reserveGpuTexturePair(129,&p,&q));
    assert(gpuArenaParent(q,129)==p&&!gpuArenaRelease(q));assert(gpuArenaRelease(p));assertArena();

    resetFixture();uint32_t tiny[GPU_ARENA_SEGMENTS-1u];
    for(unsigned i=0;i<GPU_ARENA_SEGMENTS-1u;i++){tiny[i]=reserveGpuTexture(1);assert(tiny[i]);}
    GpuArenaSegment before[GPU_ARENA_SEGMENTS];memcpy(before,g_gpu_arena,sizeof(before));
    size_t high=g_gpu_texture_offset;assert(!reserveGpuTexture(1));
    assert(!memcmp(before,g_gpu_arena,sizeof(before))&&high==g_gpu_texture_offset);
    for(unsigned i=0;i<GPU_ARENA_SEGMENTS-1u;i++)assert(gpuArenaRelease(tiny[i]));
    assertArena();assert(reserveGpuTexture(GPU_TEXTURE_BYTES));

    resetFixture();uint32_t live[64]={0},sizes[64]={0};uint8_t colors[64]={0};uint32_t random=0x34adef31u;
    for(unsigned n=0;n<6000;n++) {
        random=random*1664525u+1013904223u;unsigned slot=(random>>24)&63u;
        if(live[slot]) {
            uint8_t* data=g_gpu_textures+live[slot]-GPU_TEXTURE_PHYS;
            for(unsigned j=0;j<sizes[slot];j++)assert(data[j]==colors[slot]);
            assert(gpuArenaRelease(live[slot]));live[slot]=0;
        }
        random=random*1664525u+1013904223u;sizes[slot]=1u+(random&32767u);
        live[slot]=reserveGpuTexture(sizes[slot]);assert(live[slot]);colors[slot]=(uint8_t)(n|1u);
        memset(g_gpu_textures+live[slot]-GPU_TEXTURE_PHYS,colors[slot],sizes[slot]);
        if(!(n&63u))assertArena();
    }
    for(unsigned i=0;i<64;i++)if(live[i])assert(gpuArenaRelease(live[i]));
    assertArena();assert(g_gpu_texture_offset<4u*1024u*1024u);
    puts("Arena: 6000 bounded-live random churn operations, nonoverlap/content, padding, best-fit, coalescing, metadata/final-pool OOM and pair ownership PASS");
}
static void retirementFailures(uint8_t* pixels) {
    resetFixture();g_gpu_frame_serial=1;uint32_t source=MisterGpu_uploadTexture(pixels,4096);assert(source);
    MisterGpu_releaseTexture(pixels);assert(reserveGpuTexture(GPU_TEXTURE_BYTES-4096));
    MisterGpuTexture record=g_gpu_texture_records[0];unsigned beforeWaits=waits;
    g_gpu_frame_open=true;assert(!gpuReserveTextureWithReclaim(4096));
    assert(waits==beforeWaits&&!memcmp(&record,&g_gpu_texture_records[0],sizeof(record)));
    g_gpu_frame_open=false;g_gpu_deferred_export_count=1;assert(!gpuReserveTextureWithReclaim(4096));
    g_gpu_deferred_export_count=0;g_gpu_offscreen_index=0;assert(!gpuReserveTextureWithReclaim(4096));
    g_gpu_offscreen_index=UINT32_MAX;g_gpu_command_count=1;assert(!gpuReserveTextureWithReclaim(4096));
    g_gpu_presented=true;failWait=true;assert(!gpuReserveTextureWithReclaim(4096));
    assert(gpuArenaParent(source,4096)==source&&!memcmp(&record,&g_gpu_texture_records[0],sizeof(record)));
    failWait=false;assert(gpuReserveTextureWithReclaim(4096)==source);
    assert(!g_gpu_texture_records[0].physical[0]&&!g_gpu_texture_records[0].shadow);
    assert(g_gpu_sequence==0); // No forced legacy partial-frame publication.
    for(unsigned kind=0;kind<4;kind++)for(int failure=0;failure<(kind==1||kind==2?2:1);failure++) {
        resetFixture();failMalloc=failure;uint32_t result;
        if(kind==0)result=uploadDynamicTexture(pixels,4096,true,1);
        else if(kind==1)result=uploadSparseDynamicTexture(pixels,4096,128,1);
        else if(kind==2)result=MisterGpu_uploadSparseDynamicTextureCropRevision(pixels,128,128,32,1);
        else result=MisterGpu_prepareDynamicTextureGpuWrite(pixels,4096,1);
        assert(!result&&!g_gpu_texture_offset&&!g_gpu_texture_record_count);
    }
    resetFixture();g_gpu_frame_serial=1;assert(uploadDynamicTexture(pixels,4096,true,1));
    MisterGpu_releaseTexture(pixels);g_gpu_frame_serial++;
    record=g_gpu_texture_records[0];size_t before=g_gpu_texture_offset;
    failMalloc=mallocCalls; // Alternate sparse shadow cannot be allocated.
    assert(reuseReleasedTexture(pixels,4096,true,true,true,2)==NULL);
    assert(!memcmp(&record,&g_gpu_texture_records[0],sizeof(record))&&before==g_gpu_texture_offset);
    puts("Retirement: legacy open/pending/deferred refusal, failed-fence ownership, no partial publication, all malloc failure points PASS");
}
#endif
#if HAS_TEST_MANAGER
static void managerStableControl(uint8_t* pixels) {
    resetFixture();assert(MisterGpu_setUnifiedEnabled(true));
    const unsigned sizes[]={128,64,256,64,128,256};size_t highWater=0;
    for(unsigned i=0;i<1000;i++) {
        uint32_t handle=MisterGpu_surfaceEnsure(pixels,sizes[i%6],512,pixels,i);assert(handle);
        assert(MisterGpu_surfaceRelease(handle));
        if(i==5)highWater=g_gpu_texture_offset;
        if(i>5)assert(g_gpu_texture_offset==highWater);
    }
    printf("manager control: 1000 bounded-size generations, reserved%zu stable after first size cycle\n",highWater);
}
static void managerIncreasingSizes(uint8_t* pixels) {
    resetFixture();assert(MisterGpu_setUnifiedEnabled(true));
    unsigned completed=0;size_t peak=0;
    for(unsigned w=64;w<=768;w++) {
        uint32_t handle=MisterGpu_surfaceEnsure(pixels,w,512,pixels,w);
        if(!handle)break;
        completed++;peak=gpuSurfaceRecord(handle)->capacity;
        assert(MisterGpu_surfaceRelease(handle));
    }
    assert((HAS_REUSABLE_ARENA?completed==705:completed<705)&&completed>1&&!MisterGpu_unifiedFailed());
    size_t retained=0;unsigned live=0;
    for(unsigned i=0;i<GPU_SURFACE_RECORDS;i++){retained+=g_gpu_surfaces[i].capacity;live+=g_gpu_surfaces[i].live;}
    assert(live==0&&retained==peak);
    printf("manager growing capacity: completed%u released generations, highwater%zu, retained%zu, highwater-minus-retained%zu, peaklive%zu, live0\n",
           completed,g_gpu_texture_offset,retained,g_gpu_texture_offset-retained,peak);
}
static void snapshotIncreasingSizes(void) {
    resetFixture();assert(MisterGpu_setUnifiedEnabled(true));
    const size_t pinned=2u*1024u*1024u;uint32_t source=reserveGpuTexture(pinned);assert(source);
    unsigned completed=0;
    for(unsigned w=64;w<=768;w++) {
        GpuSurface surface={.physical=source,.width=w,.height=512,.stride=(w*4u+7u)&~7u};
        if(!gpuSurfaceSnapshot(&surface))break;
        completed++;
    }
    assert((HAS_REUSABLE_ARENA?completed==705:completed<705)&&completed>1&&!MisterGpu_unifiedFailed());
    printf("manager snapshot growth: completed%u sizes, highwater%zu, pinnedsrc%zu, current-snapshot%u, highwater-minus-owned%zu\n",
           completed,g_gpu_texture_offset,pinned,g_gpu_surface_snapshot_capacity,
           g_gpu_texture_offset-pinned-g_gpu_surface_snapshot_capacity);
}
#if HAS_REUSABLE_ARENA
static void managerRetirementFailure(uint8_t* pixels) {
    resetFixture();assert(MisterGpu_setUnifiedEnabled(true));
    uint32_t handle=MisterGpu_surfaceEnsure(pixels,64,64,pixels,1);assert(handle);
    GpuSurface* slot=gpuSurfaceRecord(handle);assert(MisterGpu_surfaceRelease(handle));
    GpuSurface before=*slot;failWait=true;
    assert(!MisterGpu_surfaceEnsure(pixels,128,64,pixels,2));
    assert(!memcmp(&before,slot,sizeof(before))&&gpuArenaParent(before.physical,before.capacity)==before.physical);
    resetFixture();assert(MisterGpu_setUnifiedEnabled(true));
    uint32_t source=reserveGpuTexture(128u*64u*4u);assert(source);
    GpuSurface surface={.physical=source,.width=64,.height=64,.stride=256};
    assert(gpuSurfaceSnapshot(&surface));assert(MisterGpu_flushNoPresent());
    uint32_t old=g_gpu_surface_snapshot,capacity=g_gpu_surface_snapshot_capacity;
    surface.width=128;surface.stride=512;failWait=true;
    assert(!gpuSurfaceSnapshot(&surface));
    assert(g_gpu_surface_snapshot==old&&g_gpu_surface_snapshot_capacity==capacity&&gpuArenaParent(old,capacity)==old);
    puts("Manager retirement failure: target and snapshot storage remain owned when full drain fails");
}
#endif
#endif
int main(void) {
    setvbuf(stdout,NULL,_IONBF,0);
    g_gpu_textures=calloc(1,GPU_TEXTURE_BYTES);assert(g_gpu_textures);
    uint8_t* pixels=calloc(1,2u*1024u*1024u);assert(pixels);
    legacyStableControl(pixels,false);legacyStableControl(pixels,true);
    legacyDistinctSizes(pixels,false,false);legacyDistinctSizes(pixels,true,false);
    legacyDistinctSizes(pixels,true,true);partialAllocationFailure(pixels);
    legacyModeConversionChurn(pixels,false);legacyModeConversionChurn(pixels,true);
    cropRestoreProof(pixels);cropLogicalRestoreProof(pixels);
#if HAS_ATOMIC_TEXTURE_PAIR
    atomicPairContract();
#endif
#if HAS_REUSABLE_ARENA
    arenaContract();retirementFailures(pixels);
#endif
#if HAS_TEST_MANAGER
    managerStableControl(pixels);managerIncreasingSizes(pixels);snapshotIncreasingSizes();
#if HAS_REUSABLE_ARENA
    managerRetirementFailure(pixels);
#endif
#endif
    resetFixture();free(pixels);free(g_gpu_textures);
    puts("Allocation findings reproduced from production helpers; not hardware timing, pixel proof, or attribution of the user slowdown.");
    return 0;
}
