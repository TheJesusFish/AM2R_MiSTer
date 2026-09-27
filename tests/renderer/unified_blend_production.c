#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "sw_renderer.h"
#include "unified_blend_production.inc"
static uint32_t rng=0x234573ab;
static uint32_t next(void){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
int main(void){
    for(unsigned i=0;i<6000;++i){
        SWRenderer sw={0};
        uint32_t source=next(),destination=next(),mode=next()%7,mask=next()%16;
        uint32_t enabled=next()%2,at=next()%2,ref=next()%256,fog=next()%2,color=next()&0xffffff;
        uint32_t factors=0;
        for(unsigned c=0;c<4;++c)factors|=(next()%11+1)<<(8*c);
        if(i<100)source&=0xffffffu;
        sw.blendMode=mode==6?bm_complex:(int32_t)mode;sw.blendEnable=enabled;
        sw.alphaTestEnable=at;sw.alphaTestRef=ref;sw.fogEnable=fog;sw.fogColor=color;
        sw.colorWriteR=mask&1;sw.colorWriteG=mask&2;sw.colorWriteB=mask&4;sw.colorWriteA=mask&8;
        sw.blendFactors=(BlendFactors){factors&255,(factors>>8)&255,(factors>>16)&255,factors>>24};
        uint8_t output[4]={destination,destination>>8,destination>>16,destination>>24};
        blendPixel(&sw,output,source,source>>8,source>>16,source>>24);
        uint32_t result=(uint32_t)output[0]|((uint32_t)output[1]<<8)|((uint32_t)output[2]<<16)|((uint32_t)output[3]<<24);
        printf("%u %u %u %u %u %u %u %u %u %u %u\n",mode,source,destination,enabled,at,ref,fog,color,mask,factors,result);
    }
    return 0;
}
