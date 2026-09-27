/* Actual production setup functions are extracted into the included file.
 * This emits only synthetic vertices/packets; no game data is read. */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sw_renderer.h"
#include "sw_raster_bounds.h"
typedef struct { float x,y,u,v,r,g,b,a; } SWVertex;
static bool MisterGpu_hasGenericPrimitive(void) { return true; }
#include "unified_packet_production.inc"

static void check(bool condition,const char* label) {
    if (!condition) { fprintf(stderr,"FAIL: %s\n",label); exit(1); }
}
static void emit(unsigned kind,const SWVertex* vertices,unsigned count,
                 const int32_t box[4],const uint64_t packet[64]) {
    printf("{\"kind\":%u,\"vertices\":[",kind);
    for (unsigned i=0;i<count;++i) {
        if(i)putchar(',');
        printf("[%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g]",
            vertices[i].x,vertices[i].y,vertices[i].u,vertices[i].v,
            vertices[i].r,vertices[i].g,vertices[i].b,vertices[i].a);
    }
    printf("],\"box\":[%d,%d,%d,%d],\"packet\":[",box[0],box[1],box[2],box[3]);
    for (unsigned i=0;i<64;++i) { if(i)putchar(',');printf("%llu",(unsigned long long)packet[i]); }
    puts("]}");
}
int main(void) {
    SWRenderer sw={0};
    sw.fbWidth=2048;sw.fbHeight=1024;sw.scissorW=2048;sw.scissorH=1024;
    sw.base.currentShader=-1;sw.blendEnable=true;sw.blendMode=bm_normal;
    sw.colorWriteR=sw.colorWriteG=sw.colorWriteB=sw.colorWriteA=true;
    sw.blendFactors=(BlendFactors){bm_src_alpha,bm_inv_src_alpha,bm_one,bm_inv_src_alpha};
    SWVertex quad[4]={
        {0,0,0,0,.25f,.5f,.75f,1}, {4,0,1,0,.5f,.25f,1,.75f},
        {4,4,1,1,.75f,1,.25f,.5f}, {0,4,0,1,1,.75f,.5f,.25f}
    };
    uint64_t packet[64]={0};int32_t box[4];
    const uint8_t source[]={255,255,255,255};
    SWVertex uv[4];memcpy(uv,quad,sizeof(uv));
    check(swUnifiedAffineUvSupported(uv,source,1,1),"bounded rectangular affine UV accepted");
    check(swUnifiedAffineUvSupported(uv,NULL,0,0),"solid affine owns a white texel");
    uv[0].u=uv[3].u=-.25f;check(!swUnifiedAffineUvSupported(uv,source,1,1),"negative affine UV uses generic clamp");
    uv[0].u=uv[3].u=0;uv[1].u=uv[2].u=1.25f;check(!swUnifiedAffineUvSupported(uv,source,1,1),"overrun affine UV uses generic clamp");
    memcpy(uv,quad,sizeof(uv));uv[2].u=.5f;check(!swUnifiedAffineUvSupported(uv,source,1,1),"deformed UV domain uses generic coverage");
    memcpy(uv,quad,sizeof(uv));uv[0].u=uv[1].u=uv[2].u=uv[3].u=.5f;check(!swUnifiedAffineUvSupported(uv,source,1,1),"constant U samples use generic coverage");
    memcpy(uv,quad,sizeof(uv));for(unsigned i=0;i<4;++i){uv[i].u=1-uv[i].u;uv[i].v=1-uv[i].v;}
    check(swUnifiedAffineUvSupported(uv,source,1,1),"mirrored bounded affine UV accepted");
    uv[1].u=NAN;check(!swUnifiedAffineUvSupported(uv,source,1,1),"nonfinite affine UV rejected");
    double fixed[]={-32768.0,32767.0,0.5};check(swUnifiedAffineFixedInputs(fixed,3),"bounded fixed conversion");
    fixed[1]=1e30;check(!swUnifiedAffineFixedInputs(fixed,3),"huge affine slope rejects before llround");
    fixed[1]=INFINITY;check(!swUnifiedAffineFixedInputs(fixed,3),"infinite affine slope rejects before llround");
    check(swUnifiedBounds(&sw,quad,4,true,box),"axis bounds");
    check(swUnifiedBuildAxisPacket(&sw,quad,box,true,4,4,packet),"axis packet");
    emit(0,quad,4,box,packet);
    SWVertex triangle[3]={quad[0],quad[1],quad[3]};bool empty=false;
    memset(packet,0,sizeof(packet));
    check(swUnifiedBounds(&sw,triangle,3,false,box),"triangle bounds");
    check(swUnifiedBuildTrianglePacket(&sw,triangle,box,true,4,4,packet,&empty)&&!empty,"triangle packet");
    emit(1,triangle,3,box,packet);
    uint64_t previous[64];memcpy(previous,packet,sizeof(previous));
    SWVertex reverse[3]={triangle[0],triangle[2],triangle[1]};
    memset(packet,0,sizeof(packet));
    check(swUnifiedBuildTrianglePacket(&sw,reverse,box,true,4,4,packet,&empty)&&!empty,"reverse winding");
    check(memcmp(previous,packet,sizeof(packet))==0,"winding normalized to identical packet");
    for(unsigned i=0;i<4;++i){quad[i].x=310.25f+(i==1||i==2?340.0f:0);quad[i].y=235.25f+(i>=2?250.0f:0);}
    sw.scissorX=320;sw.scissorY=240;sw.scissorW=317;sw.scissorH=241;
    memset(packet,0,sizeof(packet));
    check(swUnifiedBounds(&sw,quad,4,true,box),"clipped large axis");
    check(swUnifiedBuildAxisPacket(&sw,quad,box,true,19,31,packet),"large gradient packet");
    emit(2,quad,4,box,packet);
    sw.blendMode=bm_complex;sw.blendFactors=(BlendFactors){2,2,2,2};
    sw.blendEnable=false;sw.alphaTestEnable=true;sw.alphaTestRef=128;
    sw.fogEnable=true;sw.fogColor=0x563412;sw.colorWriteG=sw.colorWriteB=false;
    memset(packet,0,sizeof(packet));
    check(swUnifiedPacketState(&sw,packet,true,false,19,31),"custom state");
    check(((packet[2]>>32)&255)==6&&((packet[2]>>40)&15)==9&&((packet[2]>>48)&255)==128,"state packing");
    check((packet[2]>>56)==3&&(packet[3]>>32)==0x00563412&&(uint32_t)packet[3]==0x02020202,"state/fog/factors");
    sw.blendFactors.src=0;check(!swUnifiedPacketState(&sw,packet,true,false,19,31),"invalid factor rejects");
    sw.blendFactors.src=2;sw.base.currentShader=4;check(!swUnifiedPacketState(&sw,packet,false,false,0,0),"unsupported shader rejects");
    sw.base.currentShader=-1;
    check(!swUnifiedPlane(packet,INFINITY,0,0,0,1,1,false),"nonfinite coefficient rejects");
    check(!swUnifiedPlane(packet,.5,1e20,0,0,1,1,false),"unused X slope overflow rejects");
    check(!swUnifiedPlane(packet,.5,0,-1e20,0,1,1,false),"unused Y slope overflow rejects");
    check(!swUnifiedPlane(packet,.5,0,0,1e20,1,1,true),"unused cross slope overflow rejects");
    check(!swUnifiedPlane(packet,.5,0,0,0,0,1,false),"zero width rejects");
    check(!swUnifiedPlane(packet,.5,0,0,0,1,-1,false),"negative height rejects");
    check(!swUnifiedPlane(packet,0,1e9,1e9,0,4096,4096,false),"overflow intermediates reject");
    triangle[2]=triangle[1];
    check(swUnifiedBuildTrianglePacket(&sw,triangle,box,false,0,0,packet,&empty)&&empty,"degenerate triangle no-op");
    return 0;
}
