/* Compile actual unified draw selection/setup with an independently observed
 * device sink. No game data, framebuffer emulation or native-pixel claim. */
#include "sw_renderer.h"
#include "sw_raster_bounds.h"
#include "mister_offscreen.h"
#include "backends/mister_gpu.h"
#include <math.h>
#include <stdint.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct { float x,y,u,v,r,g,b,a; } SWVertex;
static unsigned blits,fills,affines,generic,triangles,sources,selections;
static bool missingSource, floorTint=true;
static uint64_t submitted[2][64];
static void check(bool ok,const char* label) { if(!ok){fprintf(stderr,"FAIL: %s\n",label);exit(1);} }
void logError(const char* format,...) { va_list args;va_start(args,format);vfprintf(stderr,format,args);va_end(args); }
static void reset_counts(void) { blits=fills=affines=generic=triangles=sources=selections=0;missingSource=false;floorTint=true; }
bool MisterGpu_hasGenericPrimitive(void) { return true; }
bool MisterGpu_hasFloorTint(void) { return floorTint; }
bool MisterGpu_unifiedEnabled(void) { return true; }
bool MisterGpu_unifiedFailed(void) { return false; }
static void swUnifiedFatal(const char* reason) { fprintf(stderr,"unexpected fatal: %s\n",reason);exit(2); }
static void swUnifiedCheck(SWRenderer* sw,bool ok,const char* reason) { (void)sw;if(!ok)swUnifiedFatal(reason); }
static bool swUnifiedSelect(SWRenderer* sw) { (void)sw;++selections;return true; }
static void swMarkSurfaceWrite(SWRenderer* sw,int32_t id) { (void)sw;(void)id; }
static void swExtendSurfaceContentBounds(SWRenderer* sw,int32_t x,int32_t y,int32_t x1,int32_t y1) { (void)sw;(void)x;(void)y;(void)x1;(void)y1; }
static uint32_t swUnifiedSource(SWRenderer* sw,const uint8_t* source,int32_t w,int32_t h,uint32_t* stride) {
    (void)sw;(void)source;(void)h;++sources;*stride=w*4;return missingSource?0:0x12340000;
}
static bool swGpuTryAffineQuad(SWRenderer* sw,SWVertex v[4],const uint8_t* source,int32_t w,int32_t h) {
    (void)sw;(void)v;(void)source;(void)w;(void)h;++affines;return true;
}
bool MisterGpu_addFill(int16_t x,int16_t y,uint16_t w,uint16_t h,uint32_t tint,uint8_t blend) {
    (void)x;(void)y;(void)w;(void)h;(void)tint;(void)blend;++fills;return true;
}
bool MisterGpu_addBlitFloorTint(uint32_t source,uint32_t stride,int16_t x,int16_t y,uint16_t w,uint16_t h,
        int32_t u,int32_t v,int32_t du,int32_t dv,uint32_t tint,uint8_t blend) {
    (void)source;(void)stride;(void)x;(void)y;(void)w;(void)h;(void)u;(void)v;(void)du;(void)dv;(void)tint;(void)blend;++blits;return true;
}
bool MisterGpu_addGeneric(int16_t x,int16_t y,uint16_t w,uint16_t h,const uint64_t packet[64]) {
    (void)x;(void)y;check(w&&h,"nonempty generic bounds");check(generic<2,"at most two constituent triangles");
    memcpy(submitted[generic++],packet,512);triangles+=(packet[0]>>33)&1;return true;
}
#include "unified_dispatch_production.inc"

static SWVertex original[4]={
    {0,0,0,0,1,1,1,1},{8,0,1,0,1,1,1,1},
    {8,8,1,1,1,1,1,1},{0,8,0,1,1,1,1,1}
};
static SWRenderer initial={.base={.currentShader=-1},.unifiedEnabled=true,.fbWidth=320,.fbHeight=240,
    .scissorW=320,.scissorH=240,.blendEnable=true,.blendMode=bm_normal,
    .colorWriteR=true,.colorWriteG=true,.colorWriteB=true,.colorWriteA=true,
    .blendFactors={bm_src_alpha,bm_inv_src_alpha,bm_one,bm_inv_src_alpha}};
static const uint8_t pixels[64]={0};
static bool dispatch(SWRenderer* sw,SWVertex v[4],const uint8_t* source,int w,int h) {
    return swUnifiedTryQuad(sw,v,source,w,h)||swUnifiedTryGenericQuad(sw,v,source,w,h);
}
int main(void) {
    SWRenderer sw;SWVertex q[4];
#define RESET() do { sw=initial;memcpy(q,original,sizeof(q));reset_counts(); } while(0)
#define GENERAL_AXIS(label) check(dispatch(&sw,q,pixels,4,4)&&generic==1&&triangles==0&&!affines&&!blits,label)
#define GENERAL_TRIANGLES(label) check(dispatch(&sw,q,pixels,4,4)&&generic==2&&triangles==2&&!affines&&!blits,label)
    RESET();check(dispatch(&sw,q,pixels,4,4)&&blits&&!generic&&!affines,"exact axis rectangle uses proven planner");
    RESET();q[2].u=.25f;GENERAL_TRIANGLES("deformed bottom U cannot enter either axis path");
    check(sources==1&&submitted[0][1]==submitted[1][1],"two triangles acquire source snapshot once");
    RESET();q[1].v=.25f;GENERAL_TRIANGLES("deformed top V cannot enter either axis path");
    RESET();q[2].r=.5f;q[2].u=.25f;GENERAL_TRIANGLES("gradient plus arbitrary UV retains both triangle attributes");
    RESET();q[2].r=.5f;GENERAL_AXIS("rectangle four-corner gradient retains bilinear axis packet");
    RESET();q[0].u=q[3].u=-.5f;GENERAL_AXIS("out-of-range separable UV uses dimensioned generic clamp");
    RESET();sw.blendMode=bm_complex;sw.blendFactors=(BlendFactors){2,2,2,2};
    q[0].u=q[1].u=q[2].u=q[3].u=.5f;GENERAL_AXIS("constant U stays valid in generic axis");
    RESET();sw.alphaTestEnable=true;GENERAL_AXIS("alpha-test state routes generic");
    RESET();sw.fogEnable=true;GENERAL_AXIS("fog state routes generic");
    RESET();sw.blendEnable=false;GENERAL_AXIS("replacement state routes generic");
    RESET();sw.colorWriteA=false;GENERAL_AXIS("write-mask state routes generic");
    RESET();floorTint=false;GENERAL_AXIS("absent floor-tint capability uses general opcode before publication");
    RESET();for(unsigned i=0;i<4;++i)q[i].a=-.5f;GENERAL_AXIS("negative alpha is clamped by generic without legacy byte conversion");
    RESET();for(unsigned i=0;i<4;++i)q[i].r=1e7f;GENERAL_AXIS("large finite color avoids lroundf overflow and uses generic clamp");
    RESET();for(unsigned i=0;i<4;++i){q[i].u=1-q[i].u;q[i].v=1-q[i].v;}
    check(dispatch(&sw,q,pixels,4,4)&&blits&&!generic,"mirrored rectangle preserves fast planner");
    RESET();q[1].y=.005f;q[2].y+=.005f;
    check(dispatch(&sw,q,pixels,4,4)&&affines==1&&!blits&&!generic,"near-axis parallelogram is not silently snapped");
    RESET();q[2].x+=.25f;GENERAL_TRIANGLES("nonparallelogram geometry uses general triangles");
    RESET();q[2].x+=.005f;sw.alphaTestEnable=true;GENERAL_TRIANGLES("near-axis nonrectangle cannot enter generic axis");
    RESET();q[2].u=.25f;check(dispatch(&sw,q,NULL,0,0)&&fills==1&&!generic,"untextured quad ignores unused UV");
    RESET();q[2].u=NAN;check(!dispatch(&sw,q,pixels,4,4)&&!generic&&!blits&&!affines,"nonfinite UV rejects before device publication");
    RESET();check(!dispatch(&sw,q,pixels,0,4)&&!generic&&!blits&&!affines,"invalid texture dimensions reject before publication");
    RESET();sw.base.currentShader=1;check(!dispatch(&sw,q,pixels,4,4)&&!generic&&!blits,"unsupported shader does not silently render");
    RESET();sw.blendMode=bm_complex;sw.blendFactors.src=0;check(!dispatch(&sw,q,pixels,4,4)&&!generic&&!blits,"invalid custom state rejects");
    RESET();sw.scissorX=20;sw.scissorY=20;check(dispatch(&sw,q,pixels,4,4)&&!generic&&!blits&&!sources,"empty clipped draw touches no source");
    RESET();missingSource=true;check(!dispatch(&sw,q,pixels,4,4)&&!generic&&!blits,"missing source rejects transactionally");
    puts("Unified actual-dispatch regression passed: 25 geometry/UV/state/resource cases; axis planner, affine sink and general packets observed independently.");
    return 0;
}
