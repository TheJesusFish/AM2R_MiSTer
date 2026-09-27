// Production startup/restore functions with an independently controlled device.
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
#define nullptr NULL
#define safeCalloc calloc
#define MATRIX_WORLD 0
typedef struct {
    struct { uint32_t count; } txtr;
    struct { int32_t defaultWindowWidth, defaultWindowHeight; } gen8;
} DataWin;
typedef struct { DataWin* dataWin; float gmlMatrices[1][16]; } Renderer;
typedef struct {
    Renderer base;
    int am2rMapSurfaceId;
    bool offscreenEnabled, unifiedStrict, unifiedEnabled;
    uint32_t textureCount;
    uint8_t **texturePixels;
    int32_t *textureWidths, *textureHeights;
    bool *textureLoaded;
    uint32_t *gpuTextureAddresses;
    int32_t hostWidth, hostHeight;
    uint8_t *hostFramebuffer;
} SWRenderer;
static bool g_gpu_available, g_gpu_surface_targets, g_gpu_generic_primitive;
static bool g_gpu_unified_enabled, enableAllowed;
static unsigned bits, enableCalls, hostTargetCalls;
static const char *setting, *strictSetting;
static const char* testGetenv(const char* name) {
    if (!strcmp(name,"AM2R_GPU_UNIFIED")) return setting;
    if (!strcmp(name,"AM2R_GPU_UNIFIED_STRICT")) return strictSetting;
    assert(!strcmp(name,"AM2R_GPU_OFFSCREEN"));
    return NULL;
}
#define getenv testGetenv
static bool MisterGpu_hasFloorTint(void) { return g_gpu_available && (bits&1); }
static bool MisterGpu_hasSurfaceTargets(void) { return g_gpu_available && g_gpu_surface_targets; }
static bool MisterGpu_hasGenericPrimitive(void) { return g_gpu_available && g_gpu_generic_primitive; }
static bool MisterGpu_setUnifiedEnabled(bool enable) {
    assert(enable); ++enableCalls;
    if (enableAllowed) g_gpu_unified_enabled=true;
    return enableAllowed;
}
static void swUseHostTarget(SWRenderer* sw) { assert(sw->hostFramebuffer); ++hostTargetCalls; }
static void Matrix4f_identity(float (*matrix)[16]) { memset(matrix,0,16*sizeof(float)); }
static void logInfo(const char* format,...) { (void)format; }
static void logWarn(const char* format,...) { (void)format; }
static void logError(const char* format,...) {
    va_list args;va_start(args,format);vfprintf(stderr,format,args);va_end(args);
}
static void unmapGpuHardware(void) {
    fputs("RESTORE_UNMAPPED\n",stderr);
    g_gpu_available=g_gpu_surface_targets=g_gpu_generic_primitive=false;
}
#include "unified_startup_production.inc"

static void reset(unsigned capabilities,bool available) {
    bits=capabilities;g_gpu_available=available;
    g_gpu_surface_targets=(bits&7)==7;g_gpu_generic_primitive=(bits&8)!=0;
    g_gpu_unified_enabled=false;enableCalls=hostTargetCalls=0;enableAllowed=true;
    setting=strictSetting=NULL;
}
static void clean(SWRenderer* sw) {
    free(sw->texturePixels);free(sw->textureWidths);free(sw->textureHeights);
    free(sw->textureLoaded);free(sw->gpuTextureAddresses);free(sw->hostFramebuffer);
}
static void startup(void) {
    SWRenderer sw={0};DataWin data={.txtr.count=2,.gen8={320,240}};
    swInit(&sw.base,&data);
    bool requested=setting==NULL || strcmp(setting,"1")==0;
    bool capable=g_gpu_available && (bits&15)==15;
    bool expected=requested && capable && enableAllowed;
    assert(sw.unifiedEnabled==expected);
    assert(g_gpu_unified_enabled==expected);
    assert(enableCalls==(unsigned)(requested&&capable));
    assert(sw.offscreenEnabled==(!expected && g_gpu_available && (bits&1)));
    assert(hostTargetCalls==1);
    clean(&sw);
}
int main(int argc,char** argv) {
    if (argc>1) {
        unsigned caps=(unsigned)strtoul(argv[2],NULL,0);
        bool available=atoi(argv[3])!=0;
        reset(caps,available);
        if (!strcmp(argv[1],"restore")) {
            gpuRestoreUnifiedModeAfterProbe(true);
            assert(g_gpu_unified_enabled);
            puts("RETURNED_AFTER_RESTORE");return 0;
        }
        assert(!strcmp(argv[1],"strict"));strictSetting="1";
        startup();puts("RETURNED_AFTER_STARTUP");return 0;
    }
    const char* values[]={NULL,"1","0","","true","01"};
    unsigned cases=0;
    for (unsigned cap=0;cap<32;++cap)
        for (unsigned available=0;available<2;++available)
            for (unsigned allowed=0;allowed<2;++allowed)
                for (unsigned s=0;s<sizeof(values)/sizeof(values[0]);++s) {
                    reset(cap,available);setting=values[s];enableAllowed=allowed;
                    startup();++cases;
                }
    for (unsigned cap=0;cap<32;++cap)
        for (unsigned available=0;available<2;++available) {
            reset(cap,available);gpuRestoreUnifiedModeAfterProbe(false);
            assert(!g_gpu_unified_enabled);++cases;
        }
    for (unsigned cap=15;cap<=31;cap+=16) {
        reset(cap,true);gpuRestoreUnifiedModeAfterProbe(true);
        assert(g_gpu_unified_enabled);++cases;
    }
    printf("Unified startup/restore truth table PASS: %u cases\n",cases);
    return 0;
}
