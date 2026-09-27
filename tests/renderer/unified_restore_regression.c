// Execute real restore hook + surface republish code against poisoned DDR.
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#define NULLPTR ((void*)0)
#define GPU_TEXTURE_PHYS 0x24000000u
#define GPU_TEXTURE_BYTES 512u
#define GPU_SURFACE_RECORDS 3u
typedef struct {
    bool released, shadow_valid, sparse, dynamic;
    uint8_t *shadow,*shadow_alt;
    size_t bytes;
    uint32_t physical[2],active,last_upload_frame;
} MisterGpuTexture;
typedef struct {
    bool live,cpu_valid,uniform_valid,uniform_ddr_current,needs_import;
    struct { bool valid; } background,canonical_background;
    void *cpu;
    uint32_t width,height,stride,physical;
} GpuSurface;
static bool g_gpu_unified_enabled,g_gpu_available,g_gpu_surface_failed;
static bool targetCapability=true,genericCapability=true;
static bool g_savestate_had_framebuffer,g_savestate_had_gpu;
static void *g_fb,*g_joy_shm;
static int g_input_fd=1;
static bool failFramebuffer,failInit;
static uint8_t pool[GPU_TEXTURE_BYTES],surfaceCpu[GPU_SURFACE_RECORDS][16],textureCpu[8];
static uint8_t *g_gpu_textures=pool;
static GpuSurface g_gpu_surfaces[GPU_SURFACE_RECORDS];
static MisterGpuTexture g_gpu_texture_records[1];
static uint32_t g_gpu_texture_record_count=1,g_gpu_frame_serial=7;
static uint32_t control[32],*g_gpu_control=control;
static uint32_t g_gpu_command_count,g_gpu_write_buffer,g_gpu_sequence,g_gpu_pending_sequence;
static uint32_t g_gpu_deferred_export_count,g_gpu_surface_resident,g_gpu_surface_batch_count;
static bool g_gpu_pending,g_gpu_presented,g_gpu_software_frame,g_gpu_last_presented_valid;
static bool g_gpu_frame_open,g_gpu_fused_base_valid,g_gpu_fused_valid;
static bool g_gpu_map_overlay_cache_valid,g_gpu_map_base_cache_valid,g_gpu_surface_resident_dirty;
static void *g_gpu_commands,*g_gpu_command_buffers[2];
static unsigned copies,shadowInvalidations,unmaps;
static bool MisterGpu_hasSurfaceTargets(void) {return g_gpu_available&&targetCapability;}
static bool MisterGpu_hasGenericPrimitive(void) {return g_gpu_available&&genericCapability;}
static bool MisterGpu_unifiedEnabled(void) {
    return g_gpu_unified_enabled&&MisterGpu_hasSurfaceTargets()&&!g_gpu_surface_failed;
}
static bool mapFramebuffer(void) {if(!failFramebuffer)g_fb=pool;return !failFramebuffer;}
static bool initGpu(void) {
    if(failInit){g_gpu_unified_enabled=false;return false;}
    g_gpu_available=true;return true;
}
static void openVirtualInput(void) {g_input_fd=1;}
static bool openJoyShared(void) {g_joy_shm=pool;return true;}
static void gpuInvalidateReleasedTextureShadows(void) {++shadowInvalidations;}
static void logWarn(const char* format,...) {(void)format;}
static void logError(const char* format,...) {
    va_list args;va_start(args,format);vfprintf(stderr,format,args);va_end(args);
}
static void unmapGpuHardware(void) {
    ++unmaps;g_gpu_available=false;
    fprintf(stderr,"RESTORE_UNMAPPED copies=%u\n",copies);
}
static void textureCopy(void* destination,const void* source,size_t bytes) {
    assert((uint8_t*)destination>=pool && (uint8_t*)destination+bytes<=pool+sizeof(pool));
    memcpy(destination,source,bytes);++copies;
}
#define GPU_TEXTURE_COPY textureCopy
#include "unified_restore_production.inc"

int main(int argc,char**argv) {
    assert(argc>=2);
    const char* mode=argv[1];
    g_gpu_unified_enabled=strncmp(mode,"legacy",6)!=0;
    g_gpu_available=true;g_savestate_had_gpu=true;g_joy_shm=pool;
    memset(pool,0xa5,sizeof(pool));memset(textureCpu,0x72,sizeof(textureCpu));
    g_gpu_texture_records[0]=(MisterGpuTexture){.shadow_valid=true,.shadow=textureCpu,
        .bytes=8,.physical={GPU_TEXTURE_PHYS,0}};
    for(unsigned i=0;i<GPU_SURFACE_RECORDS;++i) {
        memset(surfaceCpu[i],0x30+i,16);
        g_gpu_surfaces[i]=(GpuSurface){.live=true,.cpu_valid=true,.cpu=surfaceCpu[i],
            .width=2,.height=2,.stride=8,.physical=GPU_TEXTURE_PHYS+64+i*16};
    }
    if(!strcmp(mode,"partial")) {
        assert(argc==3);unsigned which=(unsigned)atoi(argv[2]);assert(which<GPU_SURFACE_RECORDS);
        g_gpu_surfaces[which].cpu_valid=false;
    } else if(!strcmp(mode,"init-fail")||!strcmp(mode,"legacy-init-fail")) {
        failInit=true;g_gpu_available=false;
    } else if(!strcmp(mode,"framebuffer-fail")||!strcmp(mode,"legacy-framebuffer-fail")) {
        g_savestate_had_framebuffer=true;failFramebuffer=true;
    } else if(!strcmp(mode,"failed-manager")) {
        g_gpu_surface_failed=true;
    } else if(!strcmp(mode,"generic-lost")) {
        genericCapability=false;
    } else if(!strcmp(mode,"targets-lost")) {
        targetCapability=false;
    } else assert(!strcmp(mode,"success")||!strcmp(mode,"legacy-success"));
    bool result=MisterGpu_afterSaveStateRestore();
    if(!strcmp(mode,"success")) {
        assert(result&&copies==7&&!unmaps&&shadowInvalidations==1);
        assert(!memcmp(pool,textureCpu,8));
        for(unsigned i=0;i<GPU_SURFACE_RECORDS;++i) {
            assert(!memcmp(pool+64+i*16,surfaceCpu[i],16));
            assert(!g_gpu_surfaces[i].needs_import);
        }
    } else if(!strcmp(mode,"legacy-success")) assert(result&&copies==1&&!unmaps);
    else if(!strncmp(mode,"legacy",6)) assert(!result&&!unmaps);
    else assert(!"unified failure returned to ignored caller");
    printf("RETURNED_AFTER_RESTORE result=%d copies=%u\n",result,copies);
    return 0;
}
