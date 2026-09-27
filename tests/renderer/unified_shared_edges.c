// Synthetic regression: actual packet setup and actual CPU triangle rasterizer,
// without any game assets or hardware claims.
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
// Keep the historical CPU reference's raster code, not the new GPU wrapper.
#undef USE_MISTER
#include "unified_triangle_cpu.inc"

static void runCase(unsigned kind,SWVertex triangles[][3],unsigned count) {
    uint32_t pixels[16*16]={0};
    SWRenderer sw={0};sw.framebuffer=(uint8_t*)pixels;
    sw.fbWidth=sw.fbHeight=sw.scissorW=sw.scissorH=16;
    sw.base.currentShader=-1;sw.blendEnable=true;sw.blendMode=bm_normal;
    sw.colorWriteR=sw.colorWriteG=sw.colorWriteB=sw.colorWriteA=true;
    printf("{\"kind\":%u,\"triangles\":[",kind);
    for(unsigned t=0;t<count;++t) {
        uint64_t packet[64]={0};int32_t box[4];bool empty;
        if(!swUnifiedBounds(&sw,triangles[t],3,false,box) ||
           !swUnifiedBuildTrianglePacket(&sw,triangles[t],box,false,0,0,packet,&empty) || empty) exit(2);
        if(t)putchar(',');printf("{\"vertices\":[");
        for(unsigned i=0;i<3;++i){if(i)putchar(',');printf("[%.9g,%.9g]",triangles[t][i].x,triangles[t][i].y);}
        printf("],\"box\":[%d,%d,%d,%d],\"packet\":[",box[0],box[1],box[2],box[3]);
        for(unsigned i=0;i<64;++i){if(i)putchar(',');printf("%llu",(unsigned long long)packet[i]);}
        printf("]}");
        rasterizeTriangle(&sw,triangles[t][0],triangles[t][1],triangles[t][2],NULL,0,0);
    }
    printf("],\"cpu_alpha\":[");
    for(unsigned i=0;i<16*16;++i){if(i)putchar(',');printf("%u",pixels[i]>>24);}
    puts("]}");
}
int main(void) {
    // Nonuniform color forces a general quad rather than the uniform affine
    // fast path. Shared diagonal (2,0)->(4,6) crosses two pixel centers.
    SWVertex v[4]={{2,0,0,0,.25f,1,1,.5f},{6,2,0,0,1,1,1,.5f},
                  {4,6,0,0,.75f,1,1,.5f},{0,4,0,0,0,1,1,.5f}};
    SWVertex quad[2][3]={{v[0],v[1],v[2]},{v[2],v[3],v[0]}};
    runCase(0,quad,2);
    for(unsigned i=0;i<2;++i){SWVertex swap=quad[i][0];quad[i][0]=quad[i][2];quad[i][2]=swap;}
    runCase(1,quad,2);
    // Four spokes meet at a pixel center. Outer top/left edges also pass
    // through pixel centers, testing outer boundary inclusion/exclusion.
    SWVertex center={4.5f,4.5f,0,0,.5f,1,1,.5f};
    SWVertex outer[4]={{.5f,.5f,0,0,.5f,1,1,.5f},{8.5f,.5f,0,0,.5f,1,1,.5f},
                      {8.5f,8.5f,0,0,.5f,1,1,.5f},{.5f,8.5f,0,0,.5f,1,1,.5f}};
    SWVertex fan[4][3];
    for(unsigned i=0;i<4;++i){fan[i][0]=center;fan[i][1]=outer[i];fan[i][2]=outer[(i+1)%4];}
    runCase(2,fan,4);
    for(unsigned i=0;i<4;++i){SWVertex swap=fan[i][0];fan[i][0]=fan[i][2];fan[i][2]=swap;}
    runCase(3,fan,4);
    SWVertex triangle[1][3]={{outer[0],outer[1],outer[3]}};
    runCase(4,triangle,1);
    return 0;
}
