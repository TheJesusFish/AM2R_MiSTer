/* Independent observation of the actual unified legacy-affine host helper. */
#include "sw_renderer.h"
#include "sw_raster_bounds.h"
#include "backends/mister_gpu.h"
#include "mister_offscreen.h"
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef struct { float x,y,u,v,r,g,b,a; } SWVertex;
void logError(const char* format,...) {(void)format;abort();}
static unsigned genericCount;
static bool swGpuTryAffineQuad(SWRenderer*,SWVertex[4],const uint8_t*,int32_t,int32_t);
static void swUnifiedFatal(const char* reason) {(void)reason;abort();}
static bool swUnifiedSelect(SWRenderer* sw) {(void)sw;return true;}
static void swUnifiedCheck(SWRenderer* sw,bool ok,const char* reason) {(void)sw;if(!ok)swUnifiedFatal(reason);}
static void swMarkSurfaceWrite(SWRenderer* sw,int32_t id) {(void)sw;(void)id;}
static void swExtendSurfaceContentBounds(SWRenderer* sw,int32_t x,int32_t y,int32_t x1,int32_t y1) {(void)sw;(void)x;(void)y;(void)x1;(void)y1;}
bool MisterGpu_hasGenericPrimitive(void) {return true;}
bool MisterGpu_hasFloorTint(void) {return true;}
bool MisterGpu_unifiedEnabled(void) {return true;}
bool MisterGpu_unifiedFailed(void) {return false;}
static void swOffscreenMaterializeSource(SWRenderer* sw,const uint8_t* pixels) {(void)sw;(void)pixels;}
static bool swGpuIsOutputTarget(SWRenderer* sw) {(void)sw;return true;}
static uint32_t swGpuQuarterTurnTexture(SWRenderer* sw,...) {(void)sw;abort();}
static uint32_t swGpuTextureAddress(SWRenderer* sw,const uint8_t* p,int32_t w,int32_t h) {(void)sw;(void)p;(void)w;(void)h;return 0x24000000;}
static uint32_t swUnifiedSource(SWRenderer* sw,const uint8_t* p,int32_t w,int32_t h,uint32_t* stride) {(void)sw;(void)p;(void)h;*stride=w*4;return 0x24000000;}
bool MisterGpu_addBlit(uint32_t a,uint32_t b,int16_t c,int16_t d,uint16_t e,uint16_t f,int32_t g,int32_t h,int32_t i,int32_t j,uint32_t k,uint8_t l) {
    (void)a;(void)b;(void)c;(void)d;(void)e;(void)f;(void)g;(void)h;(void)i;(void)j;(void)k;(void)l;abort();
}
bool MisterGpu_addBlitFloorTint(uint32_t a,uint32_t b,int16_t c,int16_t d,uint16_t e,uint16_t f,int32_t g,int32_t h,int32_t i,int32_t j,uint32_t k,uint8_t l) {
    return MisterGpu_addBlit(a,b,c,d,e,f,g,h,i,j,k,l);
}
bool MisterGpu_addFill(int16_t a,int16_t b,uint16_t c,uint16_t d,uint32_t e,uint8_t f) {
    (void)a;(void)b;(void)c;(void)d;(void)e;(void)f;abort();
}
bool MisterGpu_addGeneric(int16_t x,int16_t y,uint16_t width,uint16_t height,const uint64_t packet[64]) {
    uint64_t words[8]={13u|((uint64_t)width<<16)|((uint64_t)height<<32),
        0x26000000+512u*genericCount,(uint16_t)x|((uint64_t)(uint16_t)y<<16),0,0,0,0,0};
    ++genericCount;puts("GENERIC");
    for(unsigned i=0;i<8;++i)printf("%016llx\n",(unsigned long long)words[i]);
    for(unsigned i=0;i<64;++i)printf("%016llx\n",(unsigned long long)packet[i]);
    return true;
}
bool MisterGpu_addAffineBlit(uint32_t source,uint32_t stride,int16_t x,int16_t y,uint16_t width,uint16_t height,
        int32_t uMin,int32_t uMax,int32_t vMin,int32_t vMax,int32_t uStart,int32_t vStart,
        int32_t uDx,int32_t vDx,int32_t uDy,int32_t vDy,uint32_t tint,uint8_t blend) {
    uint64_t words[16]={0};
    words[0]=5u|((uint64_t)blend<<8);words[1]=tint;
    words[8]=4u|((uint64_t)width<<16)|((uint64_t)height<<32);
    words[9]=source|((uint64_t)stride<<32);
    words[10]=(uint16_t)x|((uint64_t)(uint16_t)y<<16);
    words[11]=(uint32_t)uMin|((uint64_t)(uint32_t)uMax<<32);
    words[12]=(uint32_t)vMin|((uint64_t)(uint32_t)vMax<<32);
    words[13]=(uint32_t)uStart|((uint64_t)(uint32_t)vStart<<32);
    words[14]=(uint32_t)uDx|((uint64_t)(uint32_t)vDx<<32);
    words[15]=(uint32_t)uDy|((uint64_t)(uint32_t)vDy<<32);
    puts("AFFINE");
    for(unsigned i=0;i<16;++i) printf("%016llx\n",(unsigned long long)words[i]);
    return true;
}
#include "unified_affine_bounds_production.inc"
int main(int argc,char** argv) {
    SWRenderer sw={.base={.currentShader=-1},.unifiedEnabled=true,.fbWidth=320,.fbHeight=240,
        .scissorW=320,.scissorH=240,.blendEnable=true,.blendMode=bm_normal,
        .colorWriteR=true,.colorWriteG=true,.colorWriteB=true,.colorWriteA=true,
        .gpuFrameEligible=true,.gpuSawClear=true};
    bool legacy=argc>1&&!strcmp(argv[1],"legacy");
    bool ordinary=argc>2&&!strcmp(argv[2],"ordinary");
    if(legacy)sw.unifiedEnabled=false;
    SWVertex v[4]={{0,-.000244140625f,0,0,1,1,1,1},{100,99.999755859375f,1,0,1,1,1,1},
                  {200,200.000732421875f,1,1,1,1,1,1},{100,100.000732421875f,0,1,1,1,1,1}};
    if(ordinary){v[0].x=100;v[0].y=50;v[1].x=164;v[1].y=66;v[2].x=156;v[2].y=98;v[3].x=92;v[3].y=82;}
    uint8_t pixels[64];memset(pixels,255,sizeof(pixels));
    bool accepted=swGpuTryAffineQuad(&sw,v,pixels,4,4);
    fprintf(stderr,"actual_affine_helper_accepted=%d\n",accepted);
    if(!accepted&&!legacy) {
        bool routed=swUnifiedTryQuad(&sw,v,pixels,4,4)||swUnifiedTryGenericQuad(&sw,v,pixels,4,4);
        if(!routed||genericCount!=2||sw.unifiedCpuDraws)abort();
    }
    if(!swUnifiedAffineAccumulationFits(INT32_MAX,0,0,1,1)||
       !swUnifiedAffineAccumulationFits(0,INT32_MAX,INT32_MIN,2,2)||
       swUnifiedAffineAccumulationFits(INT32_MAX,1,0,2,1)||
       swUnifiedAffineAccumulationFits(INT32_MIN,0,-1,1,2)||
       swUnifiedAffineAccumulationFits(0,1,1,0,1))abort();
    return 0;
}
