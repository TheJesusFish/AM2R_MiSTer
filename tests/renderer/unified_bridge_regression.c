// Actual extracted SW lifecycle glue; fake device deliberately maintains
// separate CPU and GPU pixels. This is not a model of DDR scheduling or RTL.
#include "sw_renderer.h"
#include "runner.h"
#include "backends/mister_gpu.h"
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

enum { SLOTS=4, WIDTH=7, HEIGHT=5, BYTES=WIDTH*HEIGHT*4, RECORDS=32 };
typedef struct {
    bool alive, writeReady;
    const void* key;
    uint8_t* cpu;
    uint8_t* gpu;
    uint32_t width, height;
} MockRecord;
static MockRecord records[RECORDS];
static unsigned nextHandle=1, selected, reads, writes, copies, releases, snapshots;
static unsigned queueFlushes, completionBarriers;
static bool enabled=true, failAllocation, deviceFailure, softwareFrame;
static uint8_t snapshotPixels[BYTES];
static void check(bool condition,const char* message) {
    if (!condition) { fprintf(stderr,"FAIL: %s\n",message);exit(2); }
}
static MockRecord* record(uint32_t handle) {
    check(handle && handle<nextHandle && records[handle].alive,"valid live generation handle");
    return records+handle;
}
void logError(const char* format,...) { va_list args;va_start(args,format);vfprintf(stderr,format,args);va_end(args); }
void logWarn(const char* format,...) { (void)format; }
bool MisterGpu_unifiedEnabled(void) { return enabled && !deviceFailure; }
bool MisterGpu_unifiedFailed(void) { return deviceFailure; }
bool MisterGpu_surfaceFlush(void) { ++queueFlushes; return !deviceFailure; }
bool MisterGpu_flushNoPresent(void) { ++completionBarriers; return !deviceFailure; }
bool MisterGpu_setUnifiedEnabled(bool value) {
    if (deviceFailure) return false;
    if (!value) for(unsigned h=1;h<nextHandle;++h) if(records[h].alive) {
        memcpy(records[h].cpu,records[h].gpu,(size_t)records[h].width*records[h].height*4);
        records[h].alive=false;
    }
    enabled=value;return true;
}
void MisterGpu_useSoftwareFrame(void) { softwareFrame=true; }
uint32_t MisterGpu_surfaceEnsure(const void* key,uint32_t width,uint32_t height,
        void* cpu,uint32_t revision) {
    (void)revision;
    if(failAllocation)return 0;
    check(nextHandle<RECORDS,"mock record capacity");
    unsigned handle=nextHandle++;
    records[handle]=(MockRecord){.alive=true,.key=key,.cpu=cpu,.width=width,.height=height};
    records[handle].gpu=safeMalloc((size_t)width*height*4);
    memcpy(records[handle].gpu,cpu,(size_t)width*height*4);
    return handle;
}
bool MisterGpu_surfaceSelect(uint32_t handle) { record(handle);selected=handle;return !deviceFailure; }
bool MisterGpu_surfaceReadback(uint32_t handle,void* output,size_t rowBytes) {
    MockRecord* r=record(handle);check(rowBytes>=r->width*4,"read row stride");
    for(unsigned y=0;y<r->height;++y)memcpy((uint8_t*)output+y*rowBytes,r->gpu+(size_t)y*r->width*4,r->width*4);
    memcpy(r->cpu,r->gpu,(size_t)r->width*r->height*4);
    r->writeReady=true;++reads;return !deviceFailure;
}
bool MisterGpu_surfaceCpuWritten(uint32_t handle,const void* pixels,size_t rowBytes,uint32_t revision) {
    (void)revision;MockRecord* r=record(handle);check(r->writeReady,"CPU publish follows read fence");
    for(unsigned y=0;y<r->height;++y)memcpy(r->gpu+(size_t)y*r->width*4,(const uint8_t*)pixels+y*rowBytes,r->width*4);
    r->writeReady=false;++writes;return !deviceFailure;
}
bool MisterGpu_surfaceRelease(uint32_t handle) { record(handle)->alive=false;++releases;return !deviceFailure; }
uint32_t MisterGpu_surfaceTexture(uint32_t handle,uint32_t* stride) {
    MockRecord* r=record(handle);*stride=r->width*4;
    if(handle==selected) {check((size_t)r->width*r->height*4<=sizeof(snapshotPixels),"snapshot size");
        memcpy(snapshotPixels,r->gpu,(size_t)r->width*r->height*4);++snapshots;return 0x90000000;}
    return 0x80000000+handle*4096;
}
bool MisterGpu_addClear(uint32_t rgba) {
    MockRecord* r=record(selected);
    for(unsigned p=0;p<r->width*r->height;++p)memcpy(r->gpu+4*p,&rgba,4);
    r->writeReady=false;return !deviceFailure;
}
bool MisterGpu_surfaceCopy(uint32_t dst,int32_t dx,int32_t dy,uint32_t src,int32_t sx,int32_t sy,int32_t w,int32_t h) {
    MockRecord* d=record(dst);MockRecord* s=record(src);
    check(dx>=0&&dy>=0&&sx>=0&&sy>=0&&dx+w<=(int)d->width&&dy+h<=(int)d->height&&sx+w<=(int)s->width&&sy+h<=(int)s->height,"bridge clips raw copy");
    uint8_t* copy=safeMalloc((size_t)w*h*4);
    for(int y=0;y<h;++y)memcpy(copy+(size_t)y*w*4,s->gpu+((size_t)(sy+y)*s->width+sx)*4,(size_t)w*4);
    for(int y=0;y<h;++y)memcpy(d->gpu+((size_t)(dy+y)*d->width+dx)*4,copy+(size_t)y*w*4,(size_t)w*4);
    free(copy);d->writeReady=false;++copies;return !deviceFailure;
}

// Legacy paths are inert after a deliberate whole-renderer disable. While
// unified ownership is active, any legacy access is a test failure.
static void swOffscreenMaterialize(SWRenderer* sw,int32_t id) { (void)id;check(!sw->unifiedEnabled,"no legacy materialization while GPU owns pixels"); }
static bool swOffscreenClear(SWRenderer* sw,uint32_t rgba) { (void)rgba;check(!sw->unifiedEnabled,"no legacy clear while unified");return false; }
static void swOffscreenDiscard(SWRenderer* sw,int32_t id) { (void)sw;(void)id; }
static void swOffscreenEmitPending(SWRenderer* sw) { (void)sw;check(false,"no old journal emission"); }
static bool swGpuIsOutputTarget(const SWRenderer* sw) { return sw->currentSurface==sw->base.runner->applicationSurfaceId; }
static void swGpuFallback(SWRenderer* sw,const char* reason) { (void)reason;check(!sw->unifiedEnabled,"no old fallback while unified"); }
static void swGpuFallbackIfOutput(SWRenderer* sw,const char* reason) { swGpuFallback(sw,reason); }
static bool swGpuReadbackCheckpoint(SWRenderer* sw) { check(!sw->unifiedEnabled,"no old checkpoint while unified");return true; }
static void swResetSurfaceContentBounds(SWRenderer* sw,int32_t id,bool full) { (void)sw;(void)id;(void)full; }
static void swExtendSurfaceContentBounds(SWRenderer* sw,int32_t x,int32_t y,int32_t x1,int32_t y1) { (void)sw;(void)x;(void)y;(void)x1;(void)y1; }
uint32_t MisterGpu_prepareDynamicTextureGpuWrite(const void* key,size_t bytes,uint32_t revision) { (void)key;(void)bytes;(void)revision;check(false,"no dynamic texture legacy upload");return 0; }
bool MisterGpu_addFramebufferExport(uint32_t address) { (void)address;check(false,"no old framebuffer export");return false; }

#include "unified_bridge_production.inc"

static SWRenderer sw;
static Runner runner;
static uint8_t pixels[SLOTS][BYTES],host[BYTES];
static uint8_t* pixelPointers[SLOTS];
static int32_t widths[SLOTS],heights[SLOTS];
static bool exists[SLOTS];
static uint32_t revisions[SLOTS],handles[SLOTS];
static void initialize(void) {
    runner.applicationSurfaceId=1;
    sw=(SWRenderer){.base={.runner=&runner},.unifiedEnabled=true,.surfaceCount=SLOTS,.surfaceCapacity=SLOTS,
        .hostFramebuffer=host,.hostWidth=WIDTH,.hostHeight=HEIGHT,.surfacePixels=pixelPointers,
        .surfaceWidths=widths,.surfaceHeights=heights,.surfaceExistsFlags=exists,
        .surfaceRevisions=revisions,.unifiedSurfaceHandles=handles};
    for(unsigned i=0;i<SLOTS;++i){pixelPointers[i]=pixels[i];widths[i]=WIDTH;heights[i]=HEIGHT;exists[i]=i!=0;memset(pixels[i],0x11+i,BYTES);}
    memset(host,0x21,sizeof(host));
    swUseSurfaceTarget(&sw,1);
}
static uint32_t gpuPixel(unsigned handle,int x,int y) { uint32_t out;memcpy(&out,record(handle)->gpu+((size_t)y*WIDTH+x)*4,4);return out; }
static uint32_t cpuPixel(int id,int x,int y) {uint32_t out;memcpy(&out,pixels[id]+((size_t)y*WIDTH+x)*4,4);return out;}
static void test_clear_copy_readback(void) {
    swClearScreen((Renderer*)&sw,0x123456,.5f);
    check(gpuPixel(handles[1],0,0)==0x80123456,"clear writes selected GPU target");
    check(cpuPixel(1,0,0)==0x12121212,"GPU clear leaves CPU shadow stale");
    swUseSurfaceTarget(&sw,2);swClearScreen((Renderer*)&sw,0xabcdef,1);
    swSurfaceCopy((Renderer*)&sw,2,2,1,1,1,0,4,3,true);
    check(copies==1 && gpuPixel(handles[2],2,1)==0x80123456,"copy reads canonical GPU source");
    check(gpuPixel(handles[2],0,0)==0xffabcdef,"partial copy preserves GPU destination");
    check(cpuPixel(2,2,1)==0x13131313,"copy performs no CPU raster");
    uint8_t output[BYTES];check(swSurfaceGetPixels((Renderer*)&sw,2,output),"readback accepts surface");
    check(!memcmp(output,pixels[2],BYTES),"readback refreshes output and CPU shadow");
    check(cpuPixel(2,2,1)==0x80123456,"readback sees GPU copy");
    unsigned oldCopies=copies;
    swSurfaceCopy((Renderer*)&sw,2,-1,4,1,0,0,WIDTH,HEIGHT,true);
    check(copies==oldCopies+1 && gpuPixel(handles[2],0,4)==0x80123456,"negative destination and bottom edge clipped");
}
static void test_canonical_sources_and_fallback(void) {
    uint32_t stride=0;unsigned oldReads=reads;
    check(swUnifiedSource(&sw,pixels[1],WIDTH,HEIGHT,&stride)==0x80000000+handles[1]*4096 && stride==WIDTH*4,"surface texture uses canonical GPU allocation");
    check(reads==oldReads,"GPU source needs no CPU readback");
    check(swUnifiedSource(&sw,pixels[2],WIDTH,HEIGHT,&stride)==0x90000000 && snapshots==1,"self source obtains GPU snapshot");
    uint32_t prior=gpuPixel(handles[2],0,0);MisterGpu_addClear(0);
    uint32_t saved;memcpy(&saved,snapshotPixels,4);check(saved==prior,"self snapshot remains independent of destination writes");
    swUseSurfaceTarget(&sw,3);swClearScreen((Renderer*)&sw,0x334455,1);swUseSurfaceTarget(&sw,2);
    oldReads=reads;swUnifiedCpuBegin(&sw,pixels[3],"mock unsupported state");
    check(reads==oldReads+2,"CPU fallback fences both distinct source and target");
    check(cpuPixel(3,0,0)==0xff334455 && cpuPixel(2,0,0)==0,"fallback receives current pixels");
    memcpy(pixels[2],pixels[3],BYTES);swUnifiedCpuEnd(&sw);
    check(writes==1 && gpuPixel(handles[2],0,0)==0xff334455,"fallback publishes changed pixels");
    check(sw.unifiedCpuDraws==1 && sw.unifiedUploadBytes==BYTES,"explicit fallback traffic counted");
    oldReads=reads;sw.unifiedCpuDepth=1;swUnifiedCpuBegin(&sw,pixels[3],"nested");swUnifiedCpuEnd(&sw);sw.unifiedCpuDepth=0;
    check(reads==oldReads && writes==1,"recursive CPU path has one outer ownership transfer");
    swUnifiedCpuBegin(&sw,pixels[2],"self fallback");
    uint8_t* copy=swUnifiedCpuAliasSnapshot(&sw,pixels[2],WIDTH,HEIGHT);
    check(copy!=NULL && !memcmp(copy,pixels[2],BYTES),"CPU fallback takes self-source snapshot");
    pixels[2][0]^=255;check(copy[0]!=pixels[2][0],"CPU alias snapshot survives target mutation");free(copy);swUnifiedCpuEnd(&sw);
}
static void test_save_release_disable(void) {
    swUseHostTarget(&sw);MisterGpu_addClear(0x12345678);
    swUseSurfaceTarget(&sw,1);MisterGpu_addClear(0xdeadbeef);
    swUseSurfaceTarget(&sw,2);MisterGpu_addClear(0x76543210);
    unsigned oldReads=reads,oldSelected=selected;
    SWRenderer_materializeSurfaces((Renderer*)&sw);
    check(reads==oldReads+4 && selected==oldSelected && sw.currentSurface==2,"save materializes every registered surface plus host without changing SW target");
    uint32_t value;memcpy(&value,host,4);check(value==0x12345678 && cpuPixel(1,0,0)==0xdeadbeef && cpuPixel(2,0,0)==0x76543210,"save receives all current GPU pixels");
    unsigned oldHandle=handles[3];swUnifiedRelease(&sw,3);check(!handles[3] && releases==1 && !records[oldHandle].alive,"release invalidates generation");
    memset(pixels[3],0x99,BYTES);swUseSurfaceTarget(&sw,3);check(handles[3]!=oldHandle && gpuPixel(handles[3],0,0)==0x99999999,"reused CPU allocation receives fresh generation and pixels");
    swUnifiedRelease(&sw,3);failAllocation=true;swUseSurfaceTarget(&sw,3);
    check(!sw.unifiedEnabled && !enabled && softwareFrame,"allocation failure switches whole renderer coherently");
    check(sw.unifiedHostHandle==0 && !handles[1] && !handles[2] && !handles[3],"disable invalidates all SW handles");
    check(cpuPixel(1,0,0)==0xdeadbeef,"disable preserves other GPU-owned images");
    check(sw.unifiedInitialShadowBytes==5*BYTES,"registration counts are not labeled upload traffic");
}
int main(int argc,char** argv) {
    initialize();
    if(argc>1) {
        if(!strcmp(argv[1],"strict-raster")){sw.unifiedStrict=true;swUnifiedCpuBegin(&sw,NULL,"strict raster");}
        if(!strcmp(argv[1],"strict-allocation")){sw.unifiedStrict=true;failAllocation=true;swUseSurfaceTarget(&sw,2);}
        if(!strcmp(argv[1],"device-failure")){deviceFailure=true;swUnifiedHandle(&sw,1);}
        if(!strcmp(argv[1],"flush-failure")){deviceFailure=true;swFlush((Renderer*)&sw);}
        return 0;
    }
    swFlush((Renderer*)&sw);
    check(queueFlushes==1 && completionBarriers==0 && reads==0,
        "renderer flush queues ordered work without CPU barrier/readback");
    test_clear_copy_readback();test_canonical_sources_and_fallback();test_save_release_disable();
    swFlush((Renderer*)&sw);
    check(queueFlushes==1 && completionBarriers==0,"legacy flush remains no-op");
    for(unsigned h=1;h<nextHandle;++h)free(records[h].gpu);
    puts("Actual unified bridge passed: independent host/targets, clear, clipped GPU copy, readback, canonical/self sources, fenced CPU fallback, save materialization, generations, coherent disable. Mock ownership proof only.");
    return 0;
}
