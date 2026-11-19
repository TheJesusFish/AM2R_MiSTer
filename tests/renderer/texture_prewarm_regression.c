#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>
#define nullptr NULL
typedef struct { uint32_t blobOffset, blobSize; uint8_t* blobData; } Texture;
typedef struct { struct { Texture* textures; } txtr; uint8_t* mappedFile; FILE* lazyLoadFile; } DataWin;
typedef struct { struct { DataWin* dataWin; } base; bool unifiedEnabled; uint32_t textureCount; uint32_t* gpuTextureAddresses; } SWRenderer;
static unsigned calls, failAt;
static bool noAddress;
static bool swEnsureTextureLoaded(SWRenderer* sw, uint32_t page) {
    assert(page==calls++);
    sw->gpuTextureAddresses[page] = noAddress ? 0 : 0x24000000u;
    return page!=failAt;
}
static void logInfo(const char* fmt,...) { (void)fmt; }
static void logWarn(const char* fmt,...) { (void)fmt; }
#include "texture_prewarm_actual.inc"
static void header(uint8_t* p, uint32_t w,uint32_t h) {
    memcpy(p,"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR",16);
    for(unsigned i=0;i<4;i++) { p[16+i]=w>>(24-i*8); p[20+i]=h>>(24-i*8); }
}
int main(void) {
    uint8_t png[24], mapped[128]={0}; Texture textures[33]; uint32_t addresses[33]={0};
    DataWin dw={.txtr.textures=textures};
    SWRenderer sw={.base.dataWin=&dw,.unifiedEnabled=true,.textureCount=20,.gpuTextureAddresses=addresses};
    header(png,1024,1024);
    for(unsigned i=0;i<33;i++) textures[i]=(Texture){.blobOffset=40,.blobSize=24,.blobData=png};
    failAt=UINT_MAX; swPrewarmTexturePages(&sw); assert(calls==20);
    calls=0; sw.unifiedEnabled=false; swPrewarmTexturePages(&sw); assert(calls==0); sw.unifiedEnabled=true;
    sw.textureCount=33; swPrewarmTexturePages(&sw); assert(calls==0);
    sw.textureCount=1;
    const uint32_t bad[][2]={{0,1},{1,0},{8193,1},{1,8193},{8192,8192}};
    for(unsigned i=0;i<sizeof(bad)/sizeof(bad[0]);i++) { header(png,bad[i][0],bad[i][1]); swPrewarmTexturePages(&sw); assert(calls==0); }
    header(png,8192,3072); swPrewarmTexturePages(&sw); assert(calls==1);
    calls=0; sw.textureCount=2; swPrewarmTexturePages(&sw); assert(calls==0); // all-or-nothing preflight
    sw.textureCount=1; header(png,1024,1024); png[0]=0; swPrewarmTexturePages(&sw); assert(calls==0);
    header(png,1024,1024); textures[0].blobSize=23; swPrewarmTexturePages(&sw); assert(calls==0);
    textures[0].blobSize=24; textures[0].blobOffset=0; swPrewarmTexturePages(&sw); assert(calls==0);
    textures[0].blobOffset=40; textures[0].blobData=NULL; swPrewarmTexturePages(&sw); assert(calls==0);
    dw.mappedFile=mapped; memcpy(mapped+40,png,24); swPrewarmTexturePages(&sw); assert(calls==1);
    calls=0; dw.mappedFile=NULL; dw.lazyLoadFile=tmpfile(); assert(dw.lazyLoadFile);
    assert(fwrite(mapped,1,sizeof(mapped),dw.lazyLoadFile)==sizeof(mapped)); assert(fseek(dw.lazyLoadFile,7,SEEK_SET)==0);
    swPrewarmTexturePages(&sw); assert(calls==1 && ftell(dw.lazyLoadFile)==7);
    calls=0; textures[0].blobOffset=120; swPrewarmTexturePages(&sw); assert(calls==0 && ftell(dw.lazyLoadFile)==7);
    assert(fclose(dw.lazyLoadFile)==0); dw.lazyLoadFile=NULL; textures[0].blobOffset=40; textures[0].blobData=png;
    sw.textureCount=20; failAt=3; swPrewarmTexturePages(&sw); assert(calls==4);
    calls=0; failAt=UINT_MAX; noAddress=true; swPrewarmTexturePages(&sw); assert(calls==1);
    puts("PASS bounded texture prewarm: PNG/budget guards, all-or-nothing preflight, mapped/file paths, seek preservation and decode/upload failures");
    return 0;
}
