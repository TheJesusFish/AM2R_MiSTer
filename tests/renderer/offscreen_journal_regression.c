// Extracted production journal + subtraction spans + copy/readback barriers.
// GPU stubs deliberately do not pretend to validate actual hardware pixels.
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#if defined(USE_MISTER) && defined(__ARM_NEON)
#include <arm_neon.h>
#endif
#include "mister_offscreen.h"

enum { bm_normal, bm_add, bm_max, bm_subtract };
enum { MISTER_GPU_BLEND_NORMAL, MISTER_GPU_BLEND_ADDITIVE, MISTER_GPU_BLEND_SUBTRACT };
enum { RENDER_TARGET_HOST_FRAMEBUFFER = -1, SURFACES = 4, W = 512, H = 256, TW = 64, TH = 64 };
typedef struct SWOffscreenJournal SWOffscreenJournal;
typedef struct { int32_t applicationSurfaceId, frameCount; } Runner;
typedef struct { Runner* runner; } Renderer;
typedef struct { float x,y,u,v,r,g,b,a; } SWVertex;
typedef struct {
    Renderer base;
    uint8_t* framebuffer;
    int32_t currentSurface, fbWidth, fbHeight;
    uint32_t surfaceCount, surfaceWriteSerial;
    uint8_t** surfacePixels;
    int32_t *surfaceWidths, *surfaceHeights;
    bool* surfaceExistsFlags;
    uint32_t* surfaceRevisions;
    SWOffscreenJournal** offscreenJournals;
    bool offscreenEnabled;
    bool unifiedEnabled;
    uint32_t unifiedHostHandle;
    uint32_t* unifiedSurfaceHandles;
    uint64_t unifiedGpuDraws, unifiedCpuDraws, unifiedReadbackBytes;
    uint32_t offscreenLogCount;
    uint32_t textureCount;
    uint8_t** texturePixels;
    uint32_t* gpuTextureAddresses;
    bool blendEnable;
    int32_t blendMode;
    bool gpuFrameEligible, gpuSawClear, gpuHasSkippedDraws, gpuHostContinuation;
    int32_t *surfaceContentMinX, *surfaceContentMinY, *surfaceContentMaxX, *surfaceContentMaxY;
} SWRenderer;

static unsigned releases, gpuBlits, gpuFloorBlits;
static bool gpuAvailable = true, gpuBeginOkay = true, gpuWriteOkay = true;
static bool gpuFloorTint;
static const void* gpuKey;
static uint32_t gpuRevision, gpuAddress, gpuClearColor;
static void* safeCalloc(size_t count,size_t size) { void*p=calloc(count,size);if(!p)abort();return p; }
static void* safeMalloc(size_t size) { void*p=malloc(size);if(!p)abort();return p; }
static bool MisterGpu_isAvailable(void) { return gpuAvailable; }
static bool MisterGpu_hasFloorTint(void) { return gpuAvailable && gpuFloorTint; }
static void MisterGpu_releaseOffscreen(const void* key) { ++releases;if(key==gpuKey){gpuKey=NULL;gpuAddress=0;} }
static bool MisterGpu_beginOffscreen(const void* key,uint32_t revision) { if(!gpuBeginOkay)return false;gpuKey=key;gpuRevision=revision;return true; }
static bool MisterGpu_addClear(uint32_t rgba) { gpuClearColor=rgba;return gpuWriteOkay; }
static bool MisterGpu_addBlit(uint32_t address,uint32_t stride,int16_t x,int16_t y,uint16_t width,uint16_t height,int32_t u,int32_t v,int32_t du,int32_t dv,uint32_t tint,uint8_t blend) {
    (void)address;(void)stride;(void)x;(void)y;(void)width;(void)height;(void)u;(void)v;(void)du;(void)dv;(void)tint;(void)blend;++gpuBlits;return gpuWriteOkay;
}
static bool MisterGpu_addBlitFloorTint(uint32_t address,uint32_t stride,int16_t x,int16_t y,uint16_t width,uint16_t height,int32_t u,int32_t v,int32_t du,int32_t dv,uint32_t tint,uint8_t blend) {
    (void)address;(void)stride;(void)x;(void)y;(void)width;(void)height;(void)u;(void)v;(void)du;(void)dv;(void)tint;(void)blend;
    ++gpuFloorBlits;return gpuAvailable && gpuFloorTint && gpuWriteOkay;
}
static bool MisterGpu_addFill(int16_t x,int16_t y,uint16_t width,uint16_t height,uint32_t tint,uint8_t blend) {
    (void)x;(void)y;(void)width;(void)height;(void)tint;(void)blend;return gpuWriteOkay;
}
static uint32_t MisterGpu_commitOffscreen(void) { gpuAddress=0x12340000;return gpuAddress; }
static void MisterGpu_abortOffscreen(void) { gpuKey=NULL;gpuAddress=0; }
static uint32_t MisterGpu_findOffscreen(const void*key,uint32_t revision) { return key==gpuKey&&revision==gpuRevision?gpuAddress:0; }
static bool swGpuIsOutputTarget(const SWRenderer*sw) { return sw->currentSurface==sw->base.runner->applicationSurfaceId; }
static uint32_t MisterGpu_prepareDynamicTextureGpuWrite(const void*p,size_t n,uint32_t r) { (void)p;(void)n;(void)r;return 0; }
static bool MisterGpu_addFramebufferExport(uint32_t p) { (void)p;return false; }
static void swGpuFallback(SWRenderer*sw,const char*reason) { (void)reason;sw->gpuFrameEligible=false; }
static bool swGpuReadbackCheckpoint(SWRenderer*sw) { (void)sw;return false; }
static void swExtendSurfaceContentBounds(SWRenderer*sw,int32_t x,int32_t y,int32_t x1,int32_t y1) { (void)sw;(void)x;(void)y;(void)x1;(void)y1; }
// This suite executes the preserved legacy path with unifiedEnabled=false.
// Any accidental use of a new GPU hook must fail, not silently satisfy it.
static void swUnifiedReadTarget(SWRenderer*sw,int32_t id) { (void)sw;(void)id;abort(); }
static uint32_t swUnifiedHandle(SWRenderer*sw,int32_t id) { (void)sw;(void)id;abort(); }
static void swUnifiedCheck(SWRenderer*sw,bool ok,const char*why) { (void)sw;(void)ok;(void)why;abort(); }
static bool MisterGpu_surfaceCopy(uint32_t d,int32_t dx,int32_t dy,uint32_t s,int32_t sx,int32_t sy,int32_t w,int32_t h) { (void)d;(void)dx;(void)dy;(void)s;(void)sx;(void)sy;(void)w;(void)h;abort(); }
static bool MisterGpu_surfaceReadback(uint32_t id,void*p,size_t stride) { (void)id;(void)p;(void)stride;abort(); }
static void swResetSurfaceContentBounds(SWRenderer*sw,int32_t id,bool full) { (void)sw;(void)id;(void)full;abort(); }

#include "offscreen_journal_production.inc"

static Runner runner={.applicationSurfaceId=1};
static uint8_t* surfacePixels[SURFACES];
static int32_t widths[SURFACES],heights[SURFACES],bounds[SURFACES];
static bool exists[SURFACES];
static uint32_t revisions[SURFACES];
static SWOffscreenJournal* journals[SURFACES];
static uint32_t atlas[TW*TH];
static uint8_t* textures[]={(uint8_t*)atlas};
static uint32_t addresses[]={0x11110000};
static uint32_t expected[W*H], readback[W*H];
static SWRenderer sw;
static uint32_t randomState=0x23bad789;
static uint32_t random32(void) { randomState^=randomState<<13;randomState^=randomState>>17;randomState^=randomState<<5;return randomState; }
static void check(bool condition,const char*message) { if(!condition){fprintf(stderr,"FAIL: %s\n",message);exit(1);} }
static void equal_pixels(const uint32_t* a,const uint32_t* b,const char*name) {
    for(size_t i=0;i<W*H;++i)if(a[i]!=b[i]){fprintf(stderr,"FAIL %s at %zu,%zu: %08x != %08x\n",name,i%W,i/W,a[i],b[i]);exit(1);}
}
static uint32_t inverse_pixel(uint32_t dst,uint32_t source,uint32_t tint) {
    uint32_t out=0;
    for(unsigned c=0;c<4;++c) {
        uint32_t s=((source>>(8*c))&255u)*((tint>>(8*c))&255u)/255u;
        out|=(((dst>>(8*c))&255u)*(255u-s)/255u)<<(8*c);
    }
    return out;
}
static int32_t floor_fixed(int64_t value) { return value>=0?(int32_t)(value/65536):-(int32_t)((-value+65535)/65536); }
static void select_surface(int id) { sw.currentSurface=id;sw.framebuffer=surfacePixels[id];sw.fbWidth=W;sw.fbHeight=H; }
static void initialize(void) {
    for(int i=0;i<SURFACES;++i){surfacePixels[i]=safeCalloc(W*H,4);widths[i]=W;heights[i]=H;exists[i]=i>0;}
    sw=(SWRenderer){.base={&runner},.surfaceCount=SURFACES,.surfacePixels=surfacePixels,.surfaceWidths=widths,.surfaceHeights=heights,.surfaceExistsFlags=exists,.surfaceRevisions=revisions,.offscreenJournals=journals,.offscreenEnabled=true,.textureCount=1,.texturePixels=textures,.gpuTextureAddresses=addresses,.blendEnable=true,.blendMode=bm_subtract,.surfaceContentMinX=bounds,.surfaceContentMinY=bounds,.surfaceContentMaxX=bounds,.surfaceContentMaxY=bounds};
    select_surface(2);
    for(size_t i=0;i<TW*TH;++i)atlas[i]=random32();
}
static void test_default_selection(void) {
    const char* disabled[]={"0", "", "true", "01", "1 ", "2"};
    for(unsigned capability=0;capability<2;++capability) {
        check(swOffscreenEnabledFromSetting(NULL,capability!=0)==(capability!=0),
              "unset offscreen setting follows the floor-tint capability");
        check(swOffscreenEnabledFromSetting("1",capability!=0),
              "explicit opt-in retains old-RBF fallback testing");
        for(size_t i=0;i<sizeof(disabled)/sizeof(disabled[0]);++i)
            check(!swOffscreenEnabledFromSetting(disabled[i],capability!=0),
                  "explicit disable or malformed setting never enables journals");
    }
    const char* settings[]={NULL,"0"};
    for(unsigned i=0;i<2;++i) {
        sw.offscreenEnabled=swOffscreenEnabledFromSetting(settings[i],i!=0);
        uint32_t serial=sw.surfaceWriteSerial,revision=revisions[2];
        unsigned released=releases;
        check(!swOffscreenClear(&sw,0xff123456u),"old default or forced-off clear stays on CPU");
        swOffscreenEmitPending(&sw);
        check(!journals[2]&&!gpuKey&&!gpuAddress,"disabled mode allocates no journal or GPU export");
        check(releases==released&&sw.surfaceWriteSerial==serial&&revisions[2]==revision,
              "disabled journal hooks do not modify ownership or revisions");
    }
    sw.offscreenEnabled=true;
}
static void clear_reference(uint32_t rgba) { for(size_t i=0;i<W*H;++i)expected[i]=rgba; }
static void add_operation(bool texture,int x,int y,int width,int height,uint32_t tint,bool subtract,double stepX,double stepY) {
    SWVertex v[4]={0};
    v[0].x=(float)x-.75f;v[0].y=(float)y-.25f;
    v[1].x=(float)(x+width)+.25f;v[3].y=(float)(y+height)+.75f;
    int sx=(int)(random32()%100)-20,sy=(int)(random32()%100)-20;
    v[0].u=(float)sx/TW;v[0].v=(float)sy/TH;
    v[1].u=(float)(sx+(v[1].x-v[0].x)*stepX)/TW;
    v[3].v=(float)(sy+(v[3].y-v[0].y)*stepY)/TH;
    sw.blendMode=subtract?bm_subtract:bm_normal;
    check(swOffscreenTryAxis(&sw,v,texture?(uint8_t*)atlas:NULL,TW,TH,x,y,x+width,y+height,true,tint),"record exact supported operation");
    double dx=v[1].x-v[0].x,dy=v[3].y-v[0].y;
    double sourceX=(double)v[0].u*TW,sourceY=(double)v[0].v*TH;
    double du=(double)(v[1].u-v[0].u)*TW/dx,dv=(double)(v[3].v-v[0].v)*TH/dy;
    int64_t start=(int64_t)((sourceX+(((double)x+.5)-v[0].x)*du)*65536.0);
    int64_t step=(int64_t)(du*65536.0);
    for(int yy=y;yy<y+height;++yy){
        int syi=(int)(sourceY+(((double)yy+.5)-v[0].y)*dv);
        if(syi<0)syi=0;if(syi>=TH)syi=TH-1;
        for(int xx=x;xx<x+width;++xx){
            size_t at=(size_t)yy*W+xx;
            if(texture){int sxi=floor_fixed(start+(xx-x)*step);if(sxi<0)sxi=0;if(sxi>=TW)sxi=TW-1;expected[at]=inverse_pixel(expected[at],atlas[syi*TW+sxi],tint);}
            else expected[at]=subtract?inverse_pixel(expected[at],tint,0xffffffffu):tint;
        }
    }
}
static void test_full_replay(void) {
    const double steps[]={-2.0,-1.0,-.5,.25,.5,1.0,2.0,3.125};
    for(int trial=0;trial<120;++trial){
        uint32_t clear=random32();check(swOffscreenClear(&sw,clear),"begin journal");clear_reference(clear);
        for(int n=0;n<24;++n){int x=random32()%W,y=random32()%H,w=1+random32()%70,h=1+random32()%30;if(x+w>W)w=W-x;if(y+h>H)h=H-y;bool texture=n%3!=0;uint32_t tint=random32();if(!texture)tint|=0xff000000u;add_operation(texture,x,y,w,h,tint,texture||n%2,steps[random32()%8],steps[random32()%8]);}
        add_operation(false,350,245,70,10,0xffab129cu,false,1,1);
        uint32_t revision=revisions[2],serial=sw.surfaceWriteSerial;
        swOffscreenMaterialize(&sw,2);
        equal_pixels((uint32_t*)surfacePixels[2],expected,"complete logical surface");
        check(revisions[2]==revision&&sw.surfaceWriteSerial==serial,"replay preserves logical revision");
        unsigned released=releases;swOffscreenMaterialize(&sw,2);check(releases==released,"double materialization is a no-op");
    }
}
static void test_replacement_rejection_capacity(void) {
    check(swOffscreenClear(&sw,0xff123456u),"replacement first clear");
    add_operation(false,350,245,40,10,0xffffffffu,false,1,1);
    check(swOffscreenClear(&sw,0xffabcdefu),"replacement second clear");clear_reference(0xffabcdefu);
    SWVertex v[4]={{.x=0,.y=0},{.x=1},{0},{.y=1}};
    sw.blendMode=bm_subtract;
    check(!swOffscreenTryAxis(&sw,v,surfacePixels[3],W,H,0,0,1,1,true,0xffffffffu),"mutable surface source rejected");
    check(!swOffscreenTryAxis(&sw,v,(uint8_t*)expected,W,H,0,0,1,1,true,0xffffffffu),"unknown source rejected");
    check(journals[2]->count==0,"rejections do not append");
    for(unsigned i=0;i<SW_OFFSCREEN_MAX_OPS;++i)add_operation(false,i%W,240+i%16,1,1,0xff010203u,true,1,1);
    check(!swOffscreenTryAxis(&sw,v,NULL,0,0,0,0,1,1,true,0xffffffffu),"bounded journal overflow rejected");
    swOffscreenMaterialize(&sw,2);equal_pixels((uint32_t*)surfacePixels[2],expected,"overflow replay keeps prior operations");
    check(swOffscreenClear(&sw,0xff102030u),"new journal after overflow");
    swOffscreenDiscard(&sw,2);check(!journals[2]->active&&journals[2]->count==0,"discard clears reuse state");
    check(swOffscreenClear(&sw,0xff456789u),"same identity reused");clear_reference(0xff456789u);
    swOffscreenMaterializeSource(&sw,surfacePixels[2]+(245*W+400)*4);equal_pixels((uint32_t*)surfacePixels[2],expected,"interior source pointer materializes whole surface");
}
static void test_copy_read_save_barriers(void) {
    select_surface(2);check(swOffscreenClear(&sw,0xff765432u),"copy source clear");
    select_surface(3);check(swOffscreenClear(&sw,0xffabcdefu),"copy destination clear");
    swSurfaceCopy((Renderer*)&sw,3,400,248,2,390,246,20,6,true);
    clear_reference(0xffabcdefu);for(int y=248;y<254;++y)for(int x=400;x<420;++x)expected[y*W+x]=0xff765432u;
    equal_pixels((uint32_t*)surfacePixels[3],expected,"actual partial-copy source and destination barriers");
    check(!journals[2]->active&&!journals[3]->active,"copy materializes both journals");
    select_surface(2);check(swOffscreenClear(&sw,0xffdeadbeu),"getPixels source clear");
    check(swSurfaceGetPixels((Renderer*)&sw,2,(uint8_t*)readback),"actual getPixels succeeds");clear_reference(0xffdeadbeu);equal_pixels(readback,expected,"actual readback barrier");
    check(swOffscreenClear(&sw,0xff192837u),"save surface two");select_surface(3);check(swOffscreenClear(&sw,0xff564738u),"save surface three");
    uint32_t serial=sw.surfaceWriteSerial;int current=sw.currentSurface;
    SWRenderer_materializeSurfaces((Renderer*)&sw);
    check(sw.surfaceWriteSerial==serial&&sw.currentSurface==current,"save replay preserves current target and revisions");
    clear_reference(0xff192837u);equal_pixels((uint32_t*)surfacePixels[2],expected,"save all first surface");
    clear_reference(0xff564738u);equal_pixels((uint32_t*)surfacePixels[3],expected,"save all second surface");
    select_surface(2);
}
static void test_emit_and_consume(void) {
    check(swOffscreenClear(&sw,0xffbbaa99u),"GPU journal clear");clear_reference(0xffbbaa99u);
    // A guaranteed unclamped atlas interval; randomized CPU cases above may
    // legitimately be rejected by the conservative GPU planner.
    SWVertex sourceVertices[4]={{.x=0,.y=0,.u=0,.v=0},{.x=32,.u=.5f},{.x=32,.y=32,.u=.5f,.v=.5f},{.y=32,.v=.5f}};
    sw.blendMode=bm_subtract;
    check(swOffscreenTryAxis(&sw,sourceVertices,(uint8_t*)atlas,TW,TH,0,0,32,32,true,0xffffffffu),"unclamped texture journal");
    for(int y=0;y<32;++y)for(int x=0;x<32;++x)expected[y*W+x]=inverse_pixel(expected[y*W+x],atlas[y*TW+x],0xffffffffu);
    swOffscreenEmitPending(&sw);check(journals[2]->active&&gpuAddress,"successful export retains full replay journal");
    SWVertex v[4]={{.x=0,.y=0,.u=0,.v=0},{.x=512,.u=1},{.x=512,.y=256,.u=1,.v=1},{.y=256,.v=1}};
    sw.currentSurface=1;sw.gpuFrameEligible=true;sw.gpuSawClear=true;
    check(swOffscreenTryConsume(&sw,v,surfacePixels[2],W,H,0,0,320,240,true,0xffffffffu,MISTER_GPU_BLEND_NORMAL),"exact crop consumes exported cache");
    check(journals[2]->active,"GPU consumption does not erase replay journal");
    v[1].x=511;check(!swOffscreenTryConsume(&sw,v,surfacePixels[2],W,H,0,0,320,240,true,0xffffffffu,0),"transformed sample rejects viewport cache");
    swOffscreenMaterializeSource(&sw,surfacePixels[2]);equal_pixels((uint32_t*)surfacePixels[2],expected,"CPU access after GPU consume retains exact pixels");
    select_surface(2);sw.gpuFrameEligible=false;
    check(swOffscreenClear(&sw,0xff246813u),"failed transaction journal");clear_reference(0xff246813u);
    gpuBeginOkay=false;swOffscreenEmitPending(&sw);gpuBeginOkay=true;
    equal_pixels((uint32_t*)surfacePixels[2],expected,"failed transaction exact CPU fallback");
    check(!journals[2]->active,"failed transaction removes deferred state");
}
static void test_fractional_tint_capability(void) {
    const uint32_t tint = 0x7f83bd6fu, clear = 0xbdf1e395u;
    SWVertex v[4]={{.x=0,.y=0,.u=0,.v=0},{.x=32,.u=.5f},{.x=32,.y=32,.u=.5f,.v=.5f},{.y=32,.v=.5f}};
    for (unsigned capability = 0; capability < 2; ++capability) {
        select_surface(2);gpuFloorTint = capability != 0;
        check(swOffscreenClear(&sw,clear),"fractional-tint journal starts");
        clear_reference(clear);sw.blendMode=bm_subtract;
        check(swOffscreenTryAxis(&sw,v,(uint8_t*)atlas,TW,TH,0,0,32,32,true,tint),"fractional-tint operation records");
        for(int y=0;y<32;++y)for(int x=0;x<32;++x)
            expected[y*W+x]=inverse_pixel(expected[y*W+x],atlas[y*TW+x],tint);
        unsigned previousNormal=gpuBlits,previousFloor=gpuFloorBlits;
        uint32_t revision=revisions[2];
        swOffscreenEmitPending(&sw);
        check(gpuBlits==previousNormal,"fractional tint never uses round-tint descriptor");
        if (capability) {
            check(journals[2]->active&&gpuAddress,"floor-capable export succeeds");
            check(gpuFloorBlits>previousFloor,"floor-capable export uses floor descriptor");
        } else {
            check(!journals[2]->active&&!gpuAddress,"old RBF materializes unsupported tint");
            check(gpuFloorBlits==previousFloor,"old RBF emits no floor descriptor");
        }
        SWRenderer_materializeSurfaces((Renderer*)&sw);
        check(revisions[2]==revision,"capability fallback/replay preserves revision");
        equal_pixels((uint32_t*)surfacePixels[2],expected,"fractional-tint full CPU pixels after export or rejection");
    }
    gpuFloorTint=false;
}
static void test_constant_prefix(void) {
    select_surface(2);sw.blendMode=bm_subtract;sw.blendEnable=true;
    const uint32_t clear=0xe3f7bd83u, tint=0xff234567u;
    check(swOffscreenClear(&sw,clear),"constant prefix clear");
    SWVertex v[4]={{.x=0,.y=0},{.x=331},{.x=331,.y=251},{.y=251}};
    check(swOffscreenTryAxis(&sw,v,NULL,0,0,0,0,331,251,true,tint),"constant full crop subtract");
    swOffscreenEmitPending(&sw);
    check(gpuClearColor==inverse_pixel(clear,tint,0xffffffffu),"constant fold matches exact per-channel CPU blend");
    swOffscreenMaterialize(&sw,2);
    check(((uint32_t*)surfacePixels[2])[239*W+319]==gpuClearColor,"folded crop agrees with logical replay");
    check(((uint32_t*)surfacePixels[2])[255*W+511]==clear,"constant fold preserves logical outside-crop pixels");
    check(swOffscreenClear(&sw,clear),"partial prefix clear");
    check(swOffscreenTryAxis(&sw,v,NULL,0,0,1,0,331,251,true,tint),"partial prefix subtract");
    swOffscreenEmitPending(&sw);
    check(gpuClearColor==clear,"partial coverage must not fold into whole clear");
    swOffscreenMaterialize(&sw,2);
}
int main(void) {
    initialize();test_default_selection();test_full_replay();test_replacement_rejection_capacity();test_copy_read_save_barriers();test_emit_and_consume();test_fractional_tint_capability();test_constant_prefix();
    for(int id=0;id<SURFACES;++id){free(journals[id]);free(surfacePixels[id]);}
    printf("Offscreen actual-journal CPU regression passed: capability-gated default/overrides and disabled no-journal path, 120 full512x256 randomized replays, outside-crop pixels, copy/read/save barriers, atlas-only sources, replacement/capacity/reuse, revisions, stub export/consume/failure lifecycle and fractional-tint capability/replay. No hardware claim.\n");
    return 0;
}
