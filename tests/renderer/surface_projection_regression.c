/* Production functions are extracted verbatim by test_surface_projection.py.
 * Only external renderer/backend dependencies are mocked; no GPU/game runs. */
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "matrix_math.h"
#define repeat(n,it) for (__typeof__(n) it=0;it<(n);++it)
#define MAX_SURFACES 8
#define RENDER_TARGET_HOST_FRAMEBUFFER (-1)

typedef struct Renderer Renderer;
typedef struct Runner Runner;
typedef struct {
    void (*flush)(Renderer*);
    bool (*setRenderTarget)(Renderer*,int32_t,bool);
    void (*beginGUI)(Renderer*,int32_t,int32_t,int32_t,int32_t,int32_t,int32_t,int32_t);
} VTable;
struct Renderer { Runner* runner; VTable* vtable; };
struct Runner {
    Renderer* renderer;
    int32_t surfaceStack[MAX_SURFACES],applicationSurfaceId;
    bool inGuiPass;
    int32_t guiPassW,guiPassH,guiPassPortW,guiPassPortH,guiPassTarget;
};
typedef struct {
    Renderer base;
    Matrix4f worldToScreen,applicationViewTransform;
    int32_t scissorX,scissorY,scissorW,scissorH,fbWidth,fbHeight,currentSurface;
    int32_t applicationScissorX,applicationScissorY,applicationScissorW,applicationScissorH;
    bool applicationViewValid;
    uint32_t surfaceCount,surfaceCapacity,surfaceWriteSerial,surfaceRevisions[8];
    bool surfaceExistsFlags[8];
    uint8_t* surfacePixels[8];
    void* offscreenJournals[8];
    int32_t surfaceWidths[8],surfaceHeights[8];
    uint8_t* framebuffer;
    bool unifiedEnabled;
    uint32_t unifiedHostHandle,unifiedSurfaceHandles[8];
    int32_t am2rMapSurfaceId;
    bool am2rMapSurfaceRetained,am2rMapSurfaceReused,am2rMapSurfaceCacheValid;
    uint64_t am2rMapSurfaceTopologyHash,am2rMapSurfaceDiscoveryHash;
} SWRenderer;

static unsigned flushes,traceResets,orphanResets;
static void fakeFlush(Renderer* renderer) { (void)renderer; ++flushes; }
static void swUseHostTarget(SWRenderer* sw) {
    sw->currentSurface=-1; sw->fbWidth=320; sw->fbHeight=240; sw->framebuffer=NULL;
}
static void VM_surfaceTraceResetRecords(void) { ++traceResets; }
static void Am2rOrphanSurfaceReset(void) { ++orphanResets; }
static bool MisterGpu_surfaceReset(void) { assert(!"GPU must not run"); return false; }
static void swUnifiedCheck(SWRenderer* sw,bool ok,const char* why) {
    (void)sw;(void)ok;(void)why; assert(!"GPU must not run");
}
static void swOffscreenDiscard(SWRenderer* sw,int32_t id) { (void)sw;(void)id; }
static void MisterGpu_releaseTexture(const uint8_t* pixels) { (void)pixels; }
static void swResetSurfaceContentBounds(SWRenderer* sw,int32_t id,bool value) {
    (void)sw;(void)id;(void)value;
}
#include "surface_projection_actual.inc"

static void projected(SWRenderer* sw,float x,float y,float wantx,float wanty) {
    float actualx,actualy;
    worldToScreen(sw,x,y,&actualx,&actualy);
    assert(fabsf(actualx-wantx)<.0001f && fabsf(actualy-wanty)<.0001f);
}
static void sameView(SWRenderer* sw,const Matrix4f* matrix,int32_t x,int32_t y,int32_t w,int32_t h) {
    assert(memcmp(matrix,&sw->worldToScreen,sizeof(*matrix))==0);
    assert(sw->scissorX==x && sw->scissorY==y && sw->scissorW==w && sw->scissorH==h);
}
static void init(SWRenderer* sw,Runner* runner,VTable* vt) {
    memset(sw,0,sizeof(*sw)); memset(runner,0,sizeof(*runner));
    sw->base.runner=runner;sw->base.vtable=vt;runner->renderer=&sw->base;
    runner->applicationSurfaceId=2;
    for(int i=0;i<MAX_SURFACES;i++)runner->surfaceStack[i]=-1;
    sw->surfaceCount=5;sw->surfaceCapacity=8;
    for(int i=1;i<5;i++) {
        sw->surfaceExistsFlags[i]=true;
        sw->surfaceWidths[i]=i==3?160:i==4?64:320;
        sw->surfaceHeights[i]=i==3?128:i==4?64:240;
    }
    assert(swUseSurfaceTarget(sw,2));
}

int main(void) {
    VTable vt={fakeFlush,swSetRenderTarget,swBeginGUI};
    SWRenderer sw;Runner runner;
    init(&sw,&runner,&vt);

    /* No prior room view: End Step targets are local, then beginView supplies
     * this frame's actual camera. No invented prior matrix may be restored. */
    assert(!sw.applicationViewValid);
    assert(Runner_surfaceSetTarget(&runner,3));
    projected(&sw,80,80,80,80);
    assert(Runner_surfaceResetTarget(&runner));
    assert(!sw.applicationViewValid);
    swBeginView(&sw.base,320,0,320,240,0,0,320,240,0);
    projected(&sw,318,69,-2,69);
    assert(sw.applicationViewValid);
    Matrix4f saved=sw.worldToScreen;

    /* The identical target pair inside Draw must preserve the room camera. */
    for(unsigned repeatPair=0;repeatPair<4;++repeatPair) {
        assert(Runner_surfaceSetTarget(&runner,3));
        projected(&sw,80,80,80,80);
        assert(Runner_surfaceResetTarget(&runner));
        sameView(&sw,&saved,0,0,320,240);
        projected(&sw,318,69,-2,69);
    }
    puts("End Step/Draw target restoration PASS");

    /* Inner targets and an explicitly selected application surface are local.
     * Only the final implicit application target restores the normal view. */
    assert(Runner_surfaceSetTarget(&runner,3));
    assert(Runner_surfaceSetTarget(&runner,4));
    assert(Runner_surfaceResetTarget(&runner));
    projected(&sw,80,80,80,80);
    assert(sw.currentSurface==3 && sw.scissorW==160 && sw.scissorH==128);
    assert(Runner_surfaceSetTarget(&runner,2));
    projected(&sw,318,69,318,69);
    assert(Runner_surfaceResetTarget(&runner));
    assert(sw.currentSurface==3 && sw.scissorW==160 && sw.scissorH==128);
    assert(Runner_surfaceResetTarget(&runner));
    sameView(&sw,&saved,0,0,320,240);
    assert(!Runner_surfaceResetTarget(&runner));
    sameView(&sw,&saved,0,0,320,240);
    puts("Nested and explicit application targets PASS");

    /* GUI's existing Runner override stays authoritative. Both GUI setters
     * must leave the previous room-view cache unchanged. */
    runner.inGuiPass=true;
    runner.guiPassW=640;runner.guiPassH=480;
    runner.guiPassPortW=320;runner.guiPassPortH=240;runner.guiPassTarget=1;
    swBeginGUI(&sw.base,640,480,0,0,320,240,1);
    projected(&sw,100,80,50,40);
    assert(Runner_surfaceSetTarget(&runner,3));
    assert(Runner_surfaceResetTarget(&runner));
    assert(sw.currentSurface==1);
    projected(&sw,100,80,50,40);
    swSetGuiProjection(&sw.base,160,120,320,240,false);
    projected(&sw,100,80,200,160);
    /* Also test the guard against an external beginView during a GUI pass. */
    assert(swUseSurfaceTarget(&sw,2));
    swBeginView(&sw.base,0,0,640,480,0,0,320,240,0);
    runner.inGuiPass=false;
    assert(swSetRenderTarget(&sw.base,2,true));
    sameView(&sw,&saved,0,0,320,240);
    puts("GUI projection isolation PASS");

    const float angles[]={0,30,-30,90,-90,180};
    for(unsigned i=0;i<sizeof(angles)/sizeof(angles[0]);++i) {
        swBeginView(&sw.base,320+(int)i,20,320,240,7,9,300,220,angles[i]);
        saved=sw.worldToScreen;
        assert(Runner_surfaceSetTarget(&runner,3));
        /* A real view targeting an offscreen surface is not the app view. */
        swBeginView(&sw.base,40,50,160,128,3,4,150,100,15);
        assert(Runner_surfaceResetTarget(&runner));
        sameView(&sw,&saved,7,9,300,220);
    }
    /* Multiple normal views must replace, rather than retain, the old cache. */
    swBeginView(&sw.base,100,40,160,120,2,6,320,240,0);
    projected(&sw,100,40,2,6);
    saved=sw.worldToScreen;
    assert(Runner_surfaceSetTarget(&runner,4));
    assert(Runner_surfaceResetTarget(&runner));
    sameView(&sw,&saved,2,6,320,240);
    projected(&sw,110,50,22,26);
    puts("Rotated/nondefault ports and replacement views PASS");

    /* Restore resets renderer-derived view state, not logical game state. */
    sw.offscreenJournals[3]=malloc(32);
    assert(sw.offscreenJournals[3]!=NULL);
    SWRenderer_fastStateResetSurfaces(&sw.base);
    assert(!sw.applicationViewValid && sw.currentSurface==-1);
    assert(traceResets==1 && orphanResets==1);
    for(unsigned i=0;i<5;++i)assert(sw.offscreenJournals[i]==NULL);
    sw.surfaceCount=3;sw.surfaceExistsFlags[2]=true;
    sw.surfaceWidths[2]=320;sw.surfaceHeights[2]=240;
    assert(swSetRenderTarget(&sw.base,2,true));
    assert(!sw.applicationViewValid);
    projected(&sw,318,69,318,69);
    swBeginView(&sw.base,320,0,320,240,0,0,320,240,0);
    projected(&sw,318,69,-2,69);
    puts("Fast-state derived-view invalidation PASS");
    printf("Surface projection actual-helper regression PASS, flushes=%u (no GPU/game execution)\n",flushes);
    return 0;
}
