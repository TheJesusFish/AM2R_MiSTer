#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define RENDER_TARGET_HOST_FRAMEBUFFER (-1)
#define LIMIT 65536u
typedef struct {
    const uint8_t* hostFramebuffer;
    uint32_t surfaceCount,unifiedSourceIds[16],unifiedSourceCursor;
    bool surfaceExistsFlags[LIMIT];
    const uint8_t* surfacePixels[LIMIT];
} SWRenderer;
static SWRenderer sw;
static uint8_t identity[LIMIT+1];
#include "surface_source_cache_actual.inc"

static int32_t expected(const uint8_t* source) {
    if(source==sw.hostFramebuffer)return -1;
    for(uint32_t id=1;id<sw.surfaceCount;++id)
        if(sw.surfaceExistsFlags[id] && sw.surfacePixels[id]==source)return (int32_t)id;
    return -2;
}
static void check(const uint8_t* source) {
    assert(swUnifiedSourceId(&sw,source)==expected(source));
}
static uint32_t randomState=0x712031b9u;
static uint32_t nextRandom(void) {
    randomState=randomState*1664525u+1013904223u;
    return randomState;
}

int main(void) {
    sw.hostFramebuffer=&identity[LIMIT];sw.surfaceCount=LIMIT;
    check(sw.hostFramebuffer);check(&identity[123]);
    assert(sw.unifiedSourceCursor==0); /* Host and misses do not evict entries. */

    for(uint32_t i=0;i<16;++i) {
        uint32_t id=65000u+i;
        sw.surfaceExistsFlags[id]=true;sw.surfacePixels[id]=&identity[id];
        check(&identity[id]);
    }
    assert(sw.unifiedSourceCursor==16);
    for(uint32_t repeat=0;repeat<1000;++repeat)
        for(uint32_t i=0;i<16;++i)check(&identity[65000u+i]);
    /* A cold linear search inserts and advances cursor; staying unchanged
     * proves hot lookup never falls through to that 65,536-slot scan. */
    assert(sw.unifiedSourceCursor==16);
    puts("Sixteen hot sources across 65,536 historical IDs PASS (16,000 cache hits)");

    /* A freed ID must not win, even when allocator pointer reuse gives its
     * old address to a different logical surface. */
    sw.surfaceExistsFlags[65000]=false;
    check(&identity[65000]);
    sw.surfaceExistsFlags[123]=true;sw.surfacePixels[123]=&identity[65000];
    assert(swUnifiedSourceId(&sw,&identity[65000])==123);
    /* Resize keeps the logical ID but changes the pixel allocation. */
    sw.surfacePixels[65001]=&identity[321];
    check(&identity[65001]);check(&identity[321]);

    /* Fast restore may reduce high-water and reuse IDs with different pixels.
     * Deliberately leave cache slots stale to exercise validation, not reset. */
    sw.surfaceCount=8;
    for(uint32_t i=1;i<8;++i) {
        sw.surfaceExistsFlags[i]=true;sw.surfacePixels[i]=&identity[100+i];
    }
    sw.unifiedSourceIds[0]=UINT32_MAX;sw.unifiedSourceIds[1]=0;
    check(&identity[65002]);check(&identity[105]);
    sw.surfacePixels[5]=&identity[700];
    check(&identity[105]);check(&identity[700]);
    puts("Free, physical-pointer reuse, resize, shrinking restore and stale-ID guards PASS");

    /* Unsigned replacement-cursor wrap must not index outside the 16 slots. */
    memset(sw.unifiedSourceIds,0,sizeof(sw.unifiedSourceIds));
    sw.unifiedSourceCursor=UINT32_MAX;
    check(&identity[101]);assert(sw.unifiedSourceCursor==0);
    check(&identity[102]);assert(sw.unifiedSourceCursor==1);

    memset(&sw,0,sizeof(sw));sw.hostFramebuffer=&identity[LIMIT];sw.surfaceCount=LIMIT;
    for(uint32_t i=1;i<LIMIT;++i)sw.surfacePixels[i]=&identity[i];
    for(uint32_t iteration=0;iteration<12000;++iteration) {
        uint32_t id=1u+nextRandom()%(LIMIT-1u);
        switch(nextRandom()%5u) {
        case 0:sw.surfaceExistsFlags[id]=true;break;
        case 1:sw.surfaceExistsFlags[id]=false;break;
        case 2:sw.surfaceCount=1u+nextRandom()%LIMIT;break;
        case 3:sw.unifiedSourceIds[nextRandom()&15u]=nextRandom();break;
        default:break;
        }
        check(&identity[id]);
        check(&identity[1u+nextRandom()%(LIMIT-1u)]);
    }
    puts("Independent linear-oracle comparison PASS (24,000 randomized lifetime lookups)");
    puts("Surface source-cache actual-helper regression PASS; no GPU or game execution");
    return 0;
}
