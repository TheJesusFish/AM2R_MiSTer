/* Actual production hooks and renderer structs, with independent raster sinks.
 * The sinks record geometry and copy opaque fixture pixels only; no GPU/native
 * rendering equivalence is claimed by this regression. */
#include "sw_renderer.h"
#include "sw_raster_bounds.h"
#include "runner.h"
#include "crt_ui_inset.h"
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#define STB_DS_IMPLEMENTATION
#include "stb_ds.h"

#define CHECK(x) do { if (!(x)) { fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x); exit(1); } } while(0)
typedef struct { float x,y,u,v,r,g,b,a; } SWVertex;
static uint32_t inset;
static unsigned unifiedDraws, legacyDraws, genericDraws, cpuFallbacks, cases;
static bool forceGeneric, forceCpu;
static SWVertex observed[4];
uint32_t MisterGpu_getCrtUiInset(void) { return inset; }
void logError(const char* format, ...) {
    va_list args; va_start(args,format); vfprintf(stderr,format,args); va_end(args);
}

static void observe(SWRenderer* sw, const SWVertex v[4], const uint8_t* source, int width, int height) {
    memcpy(observed,v,sizeof(observed));
    if (!source || width != 320 || height != 240) return;
    CHECK(v[0].x==0 && v[1].x==320 && v[0].y==v[1].y && v[3].y-v[0].y==240);
    CHECK(v[0].u==0 && v[0].v==0 && v[2].u==1 && v[2].v==1);
    int offset=(int)v[0].y;
    const uint32_t* pixels=(const uint32_t*)source;
    uint32_t* output=(uint32_t*)sw->framebuffer;
    for (int y=0;y<240;++y) for (int x=0;x<320;++x)
        if (y+offset>=0 && y+offset<240 && pixels[y*320+x]>>24)
            output[(y+offset)*320+x]=pixels[y*320+x];
}
static bool swUnifiedTryQuad(SWRenderer* sw,SWVertex v[4],const uint8_t* p,int w,int h) {
    if (forceGeneric || forceCpu) return false;
    ++unifiedDraws; observe(sw,v,p,w,h); return true;
}
static bool swUnifiedTryGenericQuad(SWRenderer* sw,SWVertex v[4],const uint8_t* p,int w,int h) {
    if (forceCpu) return false;
    ++genericDraws; observe(sw,v,p,w,h); return true;
}
static void swUnifiedCpuBegin(SWRenderer* sw,const uint8_t* p,const char* why) {
    (void)sw;(void)p;(void)why;++cpuFallbacks;
}
static uint8_t* swUnifiedCpuAliasSnapshot(SWRenderer* sw,const uint8_t* p,int w,int h) {
    (void)sw;(void)p;(void)w;(void)h;return NULL;
}
static void swUnifiedCpuEnd(SWRenderer* sw) { (void)sw; }
static bool rasterizeAxisAlignedQuad(SWRenderer* sw,SWVertex v[4],const uint8_t* p,int w,int h) {
    ++legacyDraws;observe(sw,v,p,w,h);return true;
}
static void rasterizeTriangle(SWRenderer* sw,SWVertex a,SWVertex b,SWVertex c,const uint8_t* p,int w,int h) {
    (void)sw;(void)a;(void)b;(void)c;(void)p;(void)w;(void)h;CHECK(false);
}
static void swGpuFallbackForApplicationSurfaceSnapshot(SWRenderer* sw) { (void)sw;CHECK(false); }
#include "crt_ui_production.inc"

static SWRenderer sw;
static Runner runner;
static VMContext vm;
static DataWin data;
static GameObject objects[737];
static Instance owner, stranger;
static IntRValueEntry ownerVariable;
static uint32_t hud[320*240], oldHud[320*240], output[320*240], expected[320*240];
static uint8_t* surfaces[8];
static int32_t widths[8], heights[8], bounds[8];
static bool exists[8];
static const int markerX[]={4,28,50,82,114,146,278,302};
static const int markerY[]={2,2,6,6,6,6,18,30};

static void resetFixture(bool unified) {
    shfree(vm.varNameMap);
    memset(&sw,0,sizeof(sw));memset(&runner,0,sizeof(runner));memset(&vm,0,sizeof(vm));
    memset(&data,0,sizeof(data));memset(objects,0,sizeof(objects));memset(&owner,0,sizeof(owner));
    memset(&stranger,0,sizeof(stranger));memset(hud,0,sizeof(hud));memset(oldHud,0,sizeof(oldHud));
    memset(output,0,sizeof(output));memset(surfaces,0,sizeof(surfaces));memset(exists,0,sizeof(exists));
    memset(widths,0,sizeof(widths));memset(heights,0,sizeof(heights));memset(bounds,0,sizeof(bounds));
    memset(observed,0,sizeof(observed));inset=6;forceGeneric=forceCpu=false;
    unifiedDraws=legacyDraws=genericDraws=cpuFallbacks=0;
    sw.base.runner=&runner;runner.renderer=&sw.base;runner.dataWin=&data;runner.vmContext=&vm;
    runner.applicationSurfaceId=1;runner.inGuiPass=true;
    vm.runner=&runner;vm.currentInstance=&owner;vm.currentEventType=EVENT_DRAW;
    vm.currentEventSubtype=DRAW_GUI;vm.currentCodeName="gml_Object_oControl_Draw_64";
    shput(vm.varNameMap,"gui_surface",17);
    owner.objectIndex=425;owner.instanceId=100001;
    ownerVariable=(IntRValueEntry){17,RValue_makeInt32(3)};
    owner.selfVars=(IntRValueHashMap){.entries=&ownerVariable,.capacity=1,.mask=0,.count=1};
    stranger.objectIndex=266;
    data.am2rLightSurfaceOwnershipVerified=true;data.objt.count=737;data.objt.objects=objects;data.code.count=8667;
    objects[425].name="oControl";
    sw.surfaceCount=sw.surfaceCapacity=8;sw.surfacePixels=surfaces;sw.surfaceWidths=widths;sw.surfaceHeights=heights;
    sw.surfaceExistsFlags=exists;sw.surfaceContentMinX=bounds;sw.surfaceContentMinY=bounds;
    sw.surfaceContentMaxX=bounds;sw.surfaceContentMaxY=bounds;
    for(int id=1;id<=4;++id){exists[id]=true;widths[id]=320;heights[id]=240;surfaces[id]=(uint8_t*)oldHud;}
    surfaces[3]=(uint8_t*)hud;
    sw.hostFramebuffer=sw.framebuffer=(uint8_t*)output;
    sw.hostWidth=sw.fbWidth=320;sw.hostHeight=sw.fbHeight=240;
    sw.currentSurface=RENDER_TARGET_HOST_FRAMEBUFFER;sw.unifiedEnabled=unified;
    sw.gpuFrameEligible=!unified;sw.gpuHostContinuation=true;
    swBeginView(&sw.base,0,0,320,240,0,0,320,240,0);
    for(unsigned i=0;i<sizeof(markerX)/sizeof(markerX[0]);++i)
        hud[markerY[i]*320+markerX[i]]=0xff010101u*(i+1);
    ++cases;
}
static void draw(int id) {
    swDrawSurfaceColor(&sw.base,id,0,0,-1,-1,0,0,1,1,0,0xffffff,0xffffff,0xffffff,0xffffff,1);
}
static void expectTranslated(int distance,const uint32_t* source) {
    memset(expected,0,sizeof(expected));
    for(int y=0;y<240;++y)for(int x=0;x<320;++x)
        if(y+distance>=0&&y+distance<240)expected[(y+distance)*320+x]=source[y*320+x];
    CHECK(!memcmp(expected,output,sizeof(output)));
}
static void expectNoInset(void) { draw(3);CHECK(observed[0].y==0); }
static SWVertex glyph[4]={{10,2,0,0,1,1,1,1},{16,2,1,0,1,1,1,1},{16,10,1,1,1,1,1,1},{10,10,0,1,1,1,1,1}};
typedef struct {
    int left,top,width,height;
    float x,y,xscale,yscale,angle;
    uint32_t colors[4];
    float alpha;
} CompositeArgs;
static int queryComposite(CompositeArgs args) {
    return swCrtUiHudCompositeOffset(&sw,3,args.left,args.top,args.width,args.height,
        args.x,args.y,args.xscale,args.yscale,args.angle,
        args.colors[0],args.colors[1],args.colors[2],args.colors[3],args.alpha);
}
static CompositeArgs ordinary={0,0,320,240,0,0,1,1,0,{0xffffff,0xffffff,0xffffff,0xffffff},1};

int main(void) {
    for(unsigned unified=0;unified<2;++unified) {
        const uint32_t settings[]={0,6,14,999};
        for(unsigned setting=0;setting<4;++setting) {
            resetFixture(unified);inset=settings[setting];
            // Production glyph hooks execute under the nested draw_gui script,
            // preserving oControl as self while it renders into gui_surface.
            sw.currentSurface=3;vm.currentEventType=EVENT_STEP;vm.currentEventSubtype=0;
            vm.currentCodeName="gml_Script_draw_gui";
            SWVertex q[4];memcpy(q,glyph,sizeof(q));swApplyCrtUiTextOffset(&sw,q);
            CHECK(!memcmp(q,glyph,sizeof(q)));
            sw.currentSurface=RENDER_TARGET_HOST_FRAMEBUFFER;vm.currentEventType=EVENT_DRAW;
            vm.currentEventSubtype=DRAW_GUI;vm.currentCodeName="gml_Object_oControl_Draw_64";
            memcpy(oldHud,hud,sizeof(hud));draw(3);
            expectTranslated(settings[setting]>60?60:settings[setting],hud);
            CHECK(!memcmp(oldHud,hud,sizeof(hud))); // no surface pixels are rewritten
            CHECK(unified ? unifiedDraws==1&&legacyDraws==0 : legacyDraws==1&&unifiedDraws==0);
        }
        resetFixture(unified);draw(3);expectTranslated(6,hud);
        // Toggle a cached, already-built HUD, then change a count. Neither can
        // leave a previous offset baked into its source surface.
        memset(output,0,sizeof(output));inset=0;draw(3);expectTranslated(0,hud);
        hud[6*320+50]=0xffabcdef;memset(output,0,sizeof(output));inset=14;draw(3);expectTranslated(14,hud);
        // A logical-state restore may replace every surface and conservatively
        // mark its content full-frame. Resolve its live owner field each time.
        resetFixture(unified);ownerVariable.value=RValue_makeReal(4);surfaces[4]=(uint8_t*)hud;surfaces[3]=(uint8_t*)oldHud;
        bounds[4]=240;draw(4);expectTranslated(6,hud);CHECK(swCrtUiHudSurface(&sw)==4);
        memset(output,0,sizeof(output));draw(3);CHECK(observed[0].y==0);
    }
    resetFixture(true);forceGeneric=true;draw(3);expectTranslated(6,hud);CHECK(genericDraws==1);
    resetFixture(true);forceCpu=true;draw(3);expectTranslated(6,hud);CHECK(cpuFallbacks==1&&legacyDraws==1);

    // Exercise the production legacy crop helper with the already-clipped
    // full surface rectangle. The former subtraction clips bottom HUD rows:
    // inset6 makes full y6..246 clip to6..240, but content must become6..38,
    // NOT6..32. Partial scissors and off-screen transparent bands also matter.
    for(int distance=0;distance<=14;distance+=2) {
        int x0=0,y0=distance,x1=320,y1=240;
        CHECK(swGpuIntersectSurfaceContentBounds(0,distance,0,0,320,32,&x0,&y0,&x1,&y1));
        CHECK(x0==0&&x1==320&&y0==distance&&y1==distance+32);++cases;
    }
    { int x0=7,y0=20,x1=160,y1=25;
      CHECK(swGpuIntersectSurfaceContentBounds(0,6,0,0,320,32,&x0,&y0,&x1,&y1));
      CHECK(x0==7&&y0==20&&x1==160&&y1==25);++cases; }
    { int x0=10,y0=6,x1=320,y1=240;
      CHECK(swGpuIntersectSurfaceContentBounds(10,6,5,2,200,30,&x0,&y0,&x1,&y1));
      CHECK(x0==15&&y0==8&&x1==210&&y1==36);++cases; }
    { int x0=0,y0=0,x1=320,y1=10;
      CHECK(swGpuIntersectSurfaceContentBounds(0,-230,0,232,320,240,&x0,&y0,&x1,&y1));
      CHECK(y0==2&&y1==10);++cases; }
    { int x0=0,y0=80,x1=320,y1=240;
      CHECK(!swGpuIntersectSurfaceContentBounds(0,6,0,0,320,32,&x0,&y0,&x1,&y1));++cases; }

    CompositeArgs args;
#define REJECT_COMPOSITE(change) do { resetFixture(true);args=ordinary;change;CHECK(queryComposite(args)==0); } while(0)
    REJECT_COMPOSITE(args.left=1);REJECT_COMPOSITE(args.top=1);
    REJECT_COMPOSITE(args.width=319);REJECT_COMPOSITE(args.height=239);
    REJECT_COMPOSITE(args.x=1);REJECT_COMPOSITE(args.y=1);
    REJECT_COMPOSITE(args.xscale=2);REJECT_COMPOSITE(args.yscale=-1);
    REJECT_COMPOSITE(args.angle=90);REJECT_COMPOSITE(args.alpha=.5f);
    for(unsigned i=0;i<4;++i){REJECT_COMPOSITE(args.colors[i]=0xff0000);}
    REJECT_COMPOSITE(sw.worldToScreen.m[1]=.1f);
    REJECT_COMPOSITE(sw.worldToScreen.m[4]=.1f);
    REJECT_COMPOSITE(sw.worldToScreen.m[5]=2);
    REJECT_COMPOSITE(sw.hostWidth=640);REJECT_COMPOSITE(sw.fbHeight=480);
    resetFixture(true);ownerVariable.value=RValue_makeInt32(4);surfaces[4]=(uint8_t*)hud;
    exists[3]=false;draw(3);CHECK(unifiedDraws==0); // freed old ID
    exists[3]=true;draw(3);CHECK(observed[0].y==0); // reused old ID belongs elsewhere
    memset(output,0,sizeof(output));draw(4);expectTranslated(6,hud);

    resetFixture(true);data.am2rLightSurfaceOwnershipVerified=false;expectNoInset();
    resetFixture(true);data.objt.count--;expectNoInset();
    resetFixture(true);data.code.count--;expectNoInset();
    resetFixture(true);objects[425].name="not-oControl";expectNoInset();
    resetFixture(true);owner.objectIndex=426;expectNoInset();
    resetFixture(true);owner.destroyed=true;expectNoInset();
    resetFixture(true);vm.currentInstance=&stranger;expectNoInset();
    resetFixture(true);vm.currentInstance=NULL;expectNoInset();
    resetFixture(true);shdel(vm.varNameMap,"gui_surface");expectNoInset();
    resetFixture(true);runner.applicationSurfaceId=3;expectNoInset();
    resetFixture(true);runner.inGuiPass=false;expectNoInset();
    resetFixture(true);vm.currentEventSubtype=DRAW_GUI_END;expectNoInset();
    resetFixture(true);vm.currentCodeName="another_draw";expectNoInset();
    resetFixture(true);sw.currentSurface=1;expectNoInset();
    resetFixture(true);exists[3]=false;CHECK(swCrtUiHudSurface(&sw)==-1);
    resetFixture(true);widths[3]=319;CHECK(swCrtUiHudSurface(&sw)==-1);
    resetFixture(true);heights[3]=239;CHECK(swCrtUiHudSurface(&sw)==-1);
    resetFixture(true);surfaces[3]=NULL;CHECK(swCrtUiHudSurface(&sw)==-1);
    const double invalid[]={0,-1,3.5,NAN,INFINITY,2147483648.0};
    for(unsigned i=0;i<sizeof(invalid)/sizeof(invalid[0]);++i){resetFixture(true);ownerVariable.value=RValue_makeReal(invalid[i]);expectNoInset();}
    resetFixture(true);ownerVariable.value=RValue_makeInt32(8);expectNoInset();
    resetFixture(true);ownerVariable.value=RValue_makeInt64(INT64_MAX);expectNoInset();
    resetFixture(true);ownerVariable.value=RValue_makeInt64(3);draw(3);expectTranslated(6,hud);
    resetFixture(true);ownerVariable.value=RValue_makeUndefined();expectNoInset();
    resetFixture(true);vm.currentInstance=&stranger;
    SWVertex q[4];memcpy(q,glyph,sizeof(q));swApplyCrtUiTextOffset(&sw,q);CHECK(q[0].y==8&&q[2].y==16);
    resetFixture(true);sw.currentSurface=2;memcpy(q,glyph,sizeof(q));swApplyCrtUiTextOffset(&sw,q);CHECK(q[0].y==8);
    resetFixture(true);inset=0;memcpy(q,glyph,sizeof(q));swApplyCrtUiTextOffset(&sw,q);CHECK(!memcmp(q,glyph,sizeof(q)));
    CHECK(CrtUi_am2rTitleBackgroundOffset(1,164,6)==6);
    CHECK(CrtUi_am2rTitleBackgroundOffset(1,165,6)==-6);
    CHECK(CrtUi_am2rTitleBackgroundOffset(2,164,6)==0);
    shfree(vm.varNameMap);
    printf("CRT UI composition PASS: %u scenarios; native HUD text, single full-layer inset, cached/restored surfaces, unified/legacy/generic/CPU dispatch and identity refusals.\n",cases);
    return 0;
}
