// Compiles the production helpers extracted by test_subtractive_blend.py.
// Portable NEON lane emulation checks vector indexing/math, not performance.
// The actual ARM intrinsics are additionally checked by the ARM runner build.
#define _POSIX_C_SOURCE 200809L
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>

#ifdef TEST_NEON_MODEL
#define USE_MISTER 1
#define __ARM_NEON 1
typedef struct { uint8_t lane[8]; } uint8x8_t;
typedef struct { uint8_t lane[16]; } uint8x16_t;
typedef struct { uint16_t lane[8]; } uint16x8_t;
typedef struct { uint8x8_t val[4]; } uint8x8x4_t;
typedef struct { uint8x16_t val[4]; } uint8x16x4_t;
typedef struct { uint8x8_t val[2]; } uint8x8x2_t;
static uintptr_t sourceArenaBegin,sourceArenaEnd,sourceAllowedBegin,sourceAllowedEnd;
static unsigned packedLoads;
static void check_source_load(const uint8_t*p,size_t bytes) {
    uintptr_t address=(uintptr_t)p;
    if(address<sourceArenaEnd&&address+bytes>sourceArenaBegin&&
       (address<sourceAllowedBegin||address+bytes>sourceAllowedEnd)) {
        fprintf(stderr,"FAIL: NEON source load crossed the exact row boundary\n");abort();
    }
}
static uint8x8_t vdup_n_u8(uint8_t a) { uint8x8_t r;for(int i=0;i<8;i++)r.lane[i]=a;return r; }
static uint16x8_t vdupq_n_u16(uint16_t a) { uint16x8_t r;for(int i=0;i<8;i++)r.lane[i]=a;return r; }
static uint16x8_t vaddq_u16(uint16x8_t a,uint16x8_t b) { uint16x8_t r;for(int i=0;i<8;i++)r.lane[i]=a.lane[i]+b.lane[i];return r; }
static uint16x8_t vshrq_n_u16(uint16x8_t a,int shift) { for(int i=0;i<8;i++)a.lane[i]>>=shift;return a; }
static uint8x8_t vmovn_u16(uint16x8_t a) { uint8x8_t r;for(int i=0;i<8;i++)r.lane[i]=(uint8_t)a.lane[i];return r; }
static uint16x8_t vmull_u8(uint8x8_t a,uint8x8_t b) { uint16x8_t r;for(int i=0;i<8;i++)r.lane[i]=(uint16_t)a.lane[i]*b.lane[i];return r; }
static uint8x8_t vmvn_u8(uint8x8_t a) { for(int i=0;i<8;i++)a.lane[i]=(uint8_t)~a.lane[i];return a; }
static uint8x8_t vrev64_u8(uint8x8_t a) { uint8x8_t r;for(int i=0;i<8;i++)r.lane[i]=a.lane[7-i];return r; }
static uint8x8x4_t vld4_u8(const uint8_t*p) { check_source_load(p,32);uint8x8x4_t r;for(int i=0;i<8;i++)for(int c=0;c<4;c++)r.val[c].lane[i]=p[4*i+c];return r; }
static uint8x16x4_t vld4q_u8(const uint8_t*p) { check_source_load(p,64);++packedLoads;uint8x16x4_t r;for(int i=0;i<16;i++)for(int c=0;c<4;c++)r.val[c].lane[i]=p[4*i+c];return r; }
static uint8x8x2_t vuzp_u8(uint8x8_t a,uint8x8_t b) { uint8x8x2_t r;for(int i=0;i<4;i++)for(int c=0;c<2;c++){r.val[c].lane[i]=a.lane[2*i+c];r.val[c].lane[i+4]=b.lane[2*i+c];}return r; }
static uint8x8_t vget_low_u8(uint8x16_t a) { uint8x8_t r;for(int i=0;i<8;i++)r.lane[i]=a.lane[i];return r; }
static uint8x8_t vget_high_u8(uint8x16_t a) { uint8x8_t r;for(int i=0;i<8;i++)r.lane[i]=a.lane[i+8];return r; }
static void vst4_u8(uint8_t*p,uint8x8x4_t a) { for(int i=0;i<8;i++)for(int c=0;c<4;c++)p[4*i+c]=a.val[c].lane[i]; }
#elif defined(USE_MISTER) && defined(__ARM_NEON)
#include <arm_neon.h>
#endif

enum { bm_normal, bm_add, bm_max, bm_subtract, bm_min, bm_reverse_subtract };
enum { bm_complex=-1 };
enum { bm_zero=1,bm_one,bm_src_color,bm_inv_src_color,bm_src_alpha,bm_inv_src_alpha,
       bm_dest_alpha,bm_inv_dest_alpha,bm_dest_color,bm_inv_dest_color,bm_src_alpha_sat };
typedef struct {
    bool alphaTestEnable; uint8_t alphaTestRef; bool fogEnable; uint32_t fogColor;
    bool blendEnable; int blendMode; bool colorWriteR,colorWriteG,colorWriteB,colorWriteA;
    struct { int32_t src,dst,srcAlpha,dstAlpha; } blendFactors;
} SWRenderer;
#define BGR_R(x) ((x)&255)
#define BGR_G(x) (((x)>>8)&255)
#define BGR_B(x) (((x)>>16)&255)
#include "sw_blend_under_test.inc"
#ifdef TEST_BASELINE_BENCHMARK
#include "sw_blend_baseline.inc"
#endif

static uint32_t rng = 0x7f5a239b;
static uint32_t random32(void) { rng ^= rng << 13;rng ^= rng >> 17;rng ^= rng << 5;return rng; }
static void check(bool ok,const char* message) { if(!ok){fprintf(stderr,"FAIL: %s\n",message);exit(1);} }
static void reference_pixel(uint8_t*dst,const uint8_t*src,const uint8_t*tint) {
    for(int c=0;c<4;c++)dst[c]=(uint8_t)((uint32_t)dst[c]*(255u-(uint32_t)src[c]*tint[c]/255u)/255u);
}
static void test_solid_spans(void) {
    const uint8_t white[4]={255,255,255,255};
    // Nine pixels enter both the eight-lane vector body and scalar tail.
    // Distinct per-channel source values cover every RGBA channel pair.
    for(int d=0;d<256;d++)for(int s=0;s<256;s++) {
        uint32_t actual[12],expected[12];
        uint8_t source[4]={(uint8_t)s,(uint8_t)(s^0x55),(uint8_t)(s^0xaa),(uint8_t)(255-s)};
        memset(actual,d,sizeof(actual));memcpy(expected,actual,sizeof(actual));
        for(int i=1;i<10;i++)reference_pixel((uint8_t*)&expected[i],source,white);
        blendSubtractSolidSpan(actual+1,9,source[0],source[1],source[2],source[3]);
        check(memcmp(actual,expected,sizeof(actual))==0,"solid exhaustive RGBA / vector / tail / canary");
    }
    for(int trial=0;trial<20000;trial++) {
        uint32_t actual[528],expected[528];uint8_t source[4];
        for(int i=0;i<528;i++)actual[i]=expected[i]=random32();
        for(int c=0;c<4;c++)source[c]=(uint8_t)random32();
        if(trial%3==0)source[3]=0;
        if(trial%3==1)source[3]=255;
        int offset=random32()%8,count=random32()%514;
        for(int i=offset;i<offset+count;i++)reference_pixel((uint8_t*)&expected[i],source,white);
        blendSubtractSolidSpan(actual+offset,count,source[0],source[1],source[2],source[3]);
        check(memcmp(actual,expected,sizeof(actual))==0,"solid random span / alignment / alpha / canary");
    }
    uint32_t* mask=malloc(512u*256u*sizeof(*mask));check(mask!=NULL,"mask allocation");
    memset(mask,255,512u*256u*sizeof(*mask));
    for(int y=0;y<251;y++)blendSubtractSolidSpan(mask+y*512,331,1,1,1,255);
    for(int y=0;y<256;y++)for(int x=0;x<512;x++)
        check(mask[y*512+x]==(x<331&&y<251?0x00fefefeu:0xffffffffu),"authored mask rectangle exact bounds and alpha");
    free(mask);
}
static void check_texture_span(int width,int count,int offset,int64_t start,int64_t step,unsigned variant) {
    uint32_t arena[576],originalArena[576],actual[544],expected[544];
    uint32_t*row=arena+16;
    uint8_t tint[4];
    for(int i=0;i<576;i++)arena[i]=random32();
    for(int i=0;i<544;i++)actual[i]=expected[i]=random32();
    for(int c=0;c<4;c++)tint[c]=(uint8_t)random32();
    if(variant%4==0)tint[3]=0;
    if(variant%4==1)memset(tint,255,4);
    if(variant%4==2)for(int i=0;i<width;i++)row[i]&=0x00ffffffu;
    memcpy(originalArena,arena,sizeof(arena));
#ifdef TEST_BASELINE_BENCHMARK
    uint32_t baseline[544];memcpy(baseline,actual,sizeof(actual));
    blendSubtractTextureSpanBaseline(baseline+offset,row,width,start,step,count,tint[0],tint[1],tint[2],tint[3]);
#endif
#ifdef TEST_NEON_MODEL
    sourceArenaBegin=(uintptr_t)arena;sourceArenaEnd=(uintptr_t)(arena+576);
    sourceAllowedBegin=(uintptr_t)row;sourceAllowedEnd=(uintptr_t)(row+width);
#endif
    for(int i=0;i<count;i++) {
        int x=(int)((start+i*step)>>16);if(x<0)x=0;if(x>=width)x=width-1;
        reference_pixel((uint8_t*)&expected[offset+i],(const uint8_t*)&row[x],tint);
    }
    blendSubtractTextureSpan(actual+offset,row,width,start,step,count,tint[0],tint[1],tint[2],tint[3]);
    if(memcmp(actual,expected,sizeof(actual))) {
        fprintf(stderr,"span width=%d count=%d offset=%d start=%lld step=%lld variant=%u\n",width,count,offset,(long long)start,(long long)step,variant);
        check(false,"span exact sampling / full RGBA / destination canaries");
    }
    check(memcmp(arena,originalArena,sizeof(arena))==0,"source row and canaries unchanged");
#ifdef TEST_BASELINE_BENCHMARK
    check(memcmp(actual,baseline,sizeof(actual))==0,"candidate matches preserved production baseline");
#endif
#ifdef TEST_NEON_MODEL
    sourceArenaBegin=sourceArenaEnd=sourceAllowedBegin=sourceAllowedEnd=0;
#endif
}
static void test_texture_spans(void) {
    static const int64_t steps[]={0,1,-1,32768,-32768,65535,-65535,65536,-65536,
        65537,-65537,98304,-98304,131071,-131071,131072,-131072,131073,-131073,
        218453,-218453,262144,-262144,46811,-46811};
    static const int fractions[]={0,1,32768,65535};
    static const int counts[]={0,1,7,8,9,16,33};
    unsigned cases=0;
    for(int width=1;width<=35;width++) {
        int starts[]={-9,-1,0,1,7,width/2,width-16,width-15,width-2,width-1,width,width+1};
        for(unsigned s=0;s<sizeof(steps)/sizeof(*steps);s++)
        for(unsigned x=0;x<sizeof(starts)/sizeof(*starts);x++)
        for(unsigned f=0;f<sizeof(fractions)/sizeof(*fractions);f++)
        for(unsigned n=0;n<sizeof(counts)/sizeof(*counts);n++) {
            check_texture_span(width,counts[n],cases%8,(int64_t)starts[x]*65536+fractions[f],steps[s],cases);
            ++cases;
        }
    }
    for(int trial=0;trial<50000;trial++) {
        int width=1+random32()%513,count=random32()%514,offset=random32()%8;
        int64_t start=((int64_t)(random32()%600)-40)*65536+(random32()&65535u);
        int64_t step=steps[trial%(sizeof(steps)/sizeof(*steps))];
        // Also exercise arbitrary fractional steps in [-4,+4] texels/pixel.
        if(trial%3==0)step=(int64_t)(random32()%524289)-262144;
        check_texture_span(width,count,offset,start,step,cases++);
    }
    // Self-surface scaled draws preserve the old scalar in-place dependency.
    static const uint8_t aliasTints[][4]={{141,73,255,128},{255,255,255,255},
        {255,255,255,128},{0,0,0,0}};
    for(unsigned tintIndex=0;tintIndex<sizeof(aliasTints)/sizeof(*aliasTints);tintIndex++)
    for(int direction=-1;direction<=1;direction+=2)for(int mode=0;mode<4;mode++) {
        uint32_t actual[128],expected[128];const uint8_t*tint=aliasTints[tintIndex];
        for(int i=0;i<128;i++)actual[i]=expected[i]=random32();
        int64_t start=direction>0?32768:100*65536+32768;
        int64_t step=direction*(mode==0?131072:mode==1?262144:mode==2?32768:98304);
        for(int i=0;i<60;i++) {
            int x=(int)((start+i*step)>>16);if(x<0)x=0;if(x>=128)x=127;
            reference_pixel((uint8_t*)&expected[16+i],(const uint8_t*)&expected[x],tint);
        }
#ifdef TEST_BASELINE_BENCHMARK
        uint32_t baseline[128];memcpy(baseline,actual,sizeof(actual));
        blendSubtractTextureSpanBaseline(baseline+16,baseline,128,start,step,60,tint[0],tint[1],tint[2],tint[3]);
#endif
        blendSubtractTextureSpan(actual+16,actual,128,start,step,60,tint[0],tint[1],tint[2],tint[3]);
        check(memcmp(actual,expected,sizeof(actual))==0,"scaled self-surface preserves scalar read/write order");
#ifdef TEST_BASELINE_BENCHMARK
        check(memcmp(actual,baseline,sizeof(actual))==0,"aliased span matches preserved production baseline");
#endif
    }
#ifdef TEST_NEON_MODEL
    check(packedLoads>0,"half-scale packed NEON path exercised");
#endif
    printf("%u exact texture-span cases passed\n",cases);
}
#ifdef TEST_BASELINE_BENCHMARK
typedef void (*SpanFunction)(uint32_t*,const uint32_t*,int32_t,int64_t,int64_t,int32_t,uint8_t,uint8_t,uint8_t,uint8_t);
static uint64_t benchmark_ns(void) {
    struct timespec now;check(clock_gettime(CLOCK_MONOTONIC,&now)==0,"monotonic clock");
    return (uint64_t)now.tv_sec*1000000000u+(uint64_t)now.tv_nsec;
}
static uint64_t benchmark_span(SpanFunction span,uint32_t*dst,const uint32_t*src,int64_t start,int64_t step) {
    const uint64_t begin=benchmark_ns();
    for(int i=0;i<20000;i++)span(dst,src,1025,start,step,128,141,73,255,128);
    return benchmark_ns()-begin;
}
static void benchmark_texture_spans(void) {
    uint32_t source[1025],actual[128],baseline[128];
    for(int i=0;i<1025;i++)source[i]=random32();
    static const struct { const char*name;int64_t start,step; } cases[]={
        {"unit",16*65536+32768,65536},{"half",16*65536+32768,131072},
        {"half_mirrored",900*65536+32768,-131072},{"quarter",16*65536+32768,262144},
        {"quarter_mirrored",900*65536+32768,-262144},{"fractional",16*65536+65535,218453},
        {"fractional_mirrored",900*65536+1,-218453},{"clamped",-24*65536+1,131072},
        {"double_size",16*65536+32768,32768},
    };
    puts("case,trial,pixels,baseline_ns,candidate_ns,speedup");
    for(unsigned c=0;c<sizeof(cases)/sizeof(*cases);c++)for(int trial=0;trial<3;trial++) {
        for(int i=0;i<128;i++)actual[i]=baseline[i]=random32();
        uint64_t oldTime,newTime;
        // Alternate order to avoid giving either path all cold-cache trials.
        if(trial%2) {
            newTime=benchmark_span(blendSubtractTextureSpan,actual,source,cases[c].start,cases[c].step);
            oldTime=benchmark_span(blendSubtractTextureSpanBaseline,baseline,source,cases[c].start,cases[c].step);
        } else {
            oldTime=benchmark_span(blendSubtractTextureSpanBaseline,baseline,source,cases[c].start,cases[c].step);
            newTime=benchmark_span(blendSubtractTextureSpan,actual,source,cases[c].start,cases[c].step);
        }
        check(memcmp(actual,baseline,sizeof(actual))==0,"benchmark cumulative pixels match baseline");
        printf("%s,%d,2560000,%llu,%llu,%.3f\n",cases[c].name,trial,(unsigned long long)oldTime,(unsigned long long)newTime,(double)oldTime/newTime);
        fflush(stdout);
    }
}
#endif
int main(int argc,char**argv) {
#ifdef TEST_BASELINE_BENCHMARK
    if(argc==2&&strcmp(argv[1],"--benchmark-only")==0){benchmark_texture_spans();return 0;}
#else
    (void)argc;(void)argv;
#endif
    test_solid_spans();
    test_texture_spans();
    SWRenderer sw={.blendEnable=true,.blendMode=bm_subtract,.colorWriteR=true,.colorWriteG=true,.colorWriteB=true,.colorWriteA=true};
    uint8_t golden[4]={60,80,100,255};
    blendPixel(&sw,golden,127,127,127,0);
    check(golden[0]==30&&golden[1]==40&&golden[2]==50&&golden[3]==255,"native room159 golden / alpha-zero RGB");
    uint8_t mask[4]={255,255,255,255};
    blendPixel(&sw,mask,1,1,1,255);
    check(mask[0]==254&&mask[1]==254&&mask[2]==254&&mask[3]==0,"opaque rectangle creates alpha-zero nonblack mask");
    for(int d=0;d<256;d++)for(int s=0;s<256;s++) {
        uint8_t pixel[4]={(uint8_t)d,(uint8_t)d,(uint8_t)d,(uint8_t)d};
        blendPixel(&sw,pixel,s,s,s,s);
        uint8_t expected=(uint8_t)(d*(255-s)/255);
        check(pixel[0]==expected&&pixel[1]==expected&&pixel[2]==expected&&pixel[3]==expected,"all channel pairs");
    }
    uint8_t pixel[4]={100,110,120,130},original[4];memcpy(original,pixel,4);
    sw.alphaTestEnable=true;sw.alphaTestRef=1;
    blendPixel(&sw,pixel,200,200,200,0);
    check(memcmp(pixel,original,4)==0,"explicit alpha test still discards");
    sw.alphaTestEnable=false;sw.colorWriteG=false;sw.colorWriteA=false;
    blendPixel(&sw,pixel,255,255,255,255);
    check(pixel[0]==0&&pixel[1]==110&&pixel[2]==0&&pixel[3]==130,"channel write masks");
    puts("compiled production blend helpers passed");
#ifdef TEST_BASELINE_BENCHMARK
    if(!(argc==2&&strcmp(argv[1],"--regression-only")==0))benchmark_texture_spans();
#endif
    return 0;
}
