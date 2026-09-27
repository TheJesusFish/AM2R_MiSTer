#undef NDEBUG
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include "mister_gpu.h"
#define MISTER_WIDTH 320u
#define MISTER_HEIGHT 240u
#define GPU_COMMAND_CAPACITY 32u /* Deliberately tiny to exercise no-present drains. */
#define GPU_COMMAND_BUFFER_COUNT 2u
#define GPU_TEXTURE_RECORDS 128u
#define GPU_TEXTURE_PHYS 0x24000000u
#define TEST_DDR_BYTES (64u * 1024u * 1024u)
#define GPU_TEXTURE_BYTES TEST_DDR_BYTES
static size_t textureCopyBytes;
#define GPU_TEXTURE_COPY(d,s,n) do { textureCopyBytes+=(n); memcpy((d),(s),(n)); } while(0)
typedef struct { uint64_t word[8]; } MisterGpuCommand;
static MisterGpuCommand commands[GPU_COMMAND_CAPACITY];
static MisterGpuCommand* g_gpu_commands = commands;
static uint32_t g_gpu_command_count;
static uint32_t g_gpu_write_buffer;
static uint32_t g_gpu_sequence;
static uint32_t g_gpu_frame_serial, g_gpu_scene_start, g_gpu_deferred_export_count;
static uint32_t g_gpu_deferred_exports[GPU_TEXTURE_RECORDS];
static uint32_t g_gpu_water_table_rows, g_gpu_water_direct_rows;
static bool g_gpu_frame_open, g_gpu_presented, g_gpu_software_frame;
static const void* g_gpu_offscreen_key;
static uint8_t* g_gpu_textures;
static uint32_t allocation;
#define g_gpu_texture_offset allocation
static uint32_t bram[MISTER_WIDTH*MISTER_HEIGHT];
static bool g_gpu_available = true, g_gpu_surface_targets = true, g_gpu_unified_enabled;
static bool g_gpu_generic_primitive = true;
static bool replaceRectSupported = true;
static uint32_t g_gpu_offscreen_index = UINT32_MAX;
static bool g_gpu_offscreen_allowed, g_gpu_offscreen_needs_scene_clear;
static unsigned fences, publications, submissions, waits;
static unsigned surfaceLoads, surfaceStores;
static uint64_t surfaceStoreBytes;
static unsigned boundedClears, fullClears;
static unsigned generic_packets;
static bool fail_wait;
static bool fail_allocation;
#include "mister_gpu_arena_impl.h"
static uint32_t referenceConstantBlend(uint32_t background,uint32_t foreground,unsigned mode);

bool MisterGpu_hasFloorTint(void) { return true; }
bool MisterGpu_hasReplaceRect(void) { return replaceRectSupported; }
static void gpuSurfaceCullOpaquePrefix(uint32_t start,uint32_t width,uint32_t height) {
    (void)start;(void)width;(void)height; // Independently covered by actual-helper fixture.
}
static uint32_t reserveGpuTexture(size_t bytes) {
    return fail_allocation ? 0 : gpuArenaReserve(bytes);
}
// This fixture has no legacy texture records to reclaim. The allocator churn
// fixture separately executes the real legacy retirement/retry helpers.
static uint32_t gpuReserveTextureWithReclaim(size_t bytes) { return reserveGpuTexture(bytes); }
static uint32_t* pixel(uint32_t address) {
    assert(address >= GPU_TEXTURE_PHYS && address <= GPU_TEXTURE_PHYS + TEST_DDR_BYTES - 4u);
    assert((address & 3u) == 0);
    return (uint32_t*)(g_gpu_textures + address - GPU_TEXTURE_PHYS);
}
static bool waitGpuCompletion(uint32_t* cycles) { if (cycles) *cycles = 0; waits++; return !fail_wait; }
static uint8_t gradient(uint8_t start, int16_t delta, int32_t row) {
    int32_t value = ((int32_t)start * 256 + delta * row) >> 8;
    return value < 0 ? 0 : value > 255 ? 255 : (uint8_t)value;
}
static uint32_t tintGradient(uint32_t tint, uint64_t delta, int32_t row) {
    uint32_t result = 0;
    for (unsigned channel = 0; channel < 4; ++channel)
        result |= (uint32_t)gradient((uint8_t)(tint >> (channel*8)),
                    (int16_t)(delta >> (channel*16)), row) << (channel*8);
    return result;
}
static uint32_t modulate(uint32_t source, uint32_t tint, bool floorTint) {
    uint32_t result = 0;
    for (unsigned c = 0; c < 4; ++c) {
        unsigned product = ((source >> (c*8)) & 255u) * ((tint >> (c*8)) & 255u);
        unsigned v = floorTint ? product / 255u : (product + 127u) / 255u;
        result |= v << (c*8);
    }
    return result;
}
static bool submitGpuCommandsAsync(void) {
    submissions++;
    bool setupPending = false;
    uint32_t affineTint = UINT32_MAX;
    for (uint32_t i = 0; i < g_gpu_command_count; ++i) {
        MisterGpuCommand* c = &commands[i];
        unsigned opcode = (unsigned)c->word[0] & 255u;
        unsigned w = (uint16_t)(c->word[0] >> 16), h = (uint16_t)(c->word[0] >> 32);
        uint32_t address = (uint32_t)c->word[1], stride = (uint32_t)(c->word[1] >> 32);
        if (opcode == 12u || opcode == 0u) {
            assert(!setupPending && i + 1 == g_gpu_command_count);
            if (opcode == 12u) fences++; else publications++;
        } else if (opcode == 10u || opcode == 11u) {
            if(opcode==10u)surfaceLoads++;else surfaceStores++;
            unsigned bx=(uint16_t)c->word[2],by=(uint16_t)(c->word[2]>>16);
            assert(!setupPending && w && h && bx+w<=MISTER_WIDTH && by+h<=MISTER_HEIGHT);
            assert(!(stride & 7u) && stride >= w * 4u && !(c->word[2]>>32));
            if(opcode==11u)surfaceStoreBytes+=(uint64_t)w*h*4u;
            for (unsigned y = 0; y < h; ++y)
                if (opcode == 10u) memcpy(bram + (by+y)*MISTER_WIDTH+bx, pixel(address+y*stride), w*4u);
                else memcpy(pixel(address+y*stride), bram + (by+y)*MISTER_WIDTH+bx, w*4u);
        } else if (opcode == 14u) {
            boundedClears++;
            unsigned x=(uint16_t)c->word[2],y=(uint16_t)(c->word[2]>>16);
            assert(replaceRectSupported&&w&&h&&x+w<=MISTER_WIDTH&&y+h<=MISTER_HEIGHT);
            for(unsigned row=0;row<h;row++)for(unsigned col=0;col<w;col++)
                bram[(y+row)*MISTER_WIDTH+x+col]=address;
        } else if (opcode == 1u) {
            fullClears++;
            for (unsigned p = 0; p < MISTER_WIDTH*MISTER_HEIGHT; ++p) bram[p] = address;
        } else if (opcode == 5u) {
            assert(!setupPending);
            setupPending = true;
            affineTint = address;
        } else if (opcode == 13u) {
            uint64_t* p=(uint64_t*)pixel(address); assert(!(address&7u));
            assert((uint32_t)p[0]==0x31504741u); generic_packets++;
            int dx=(int16_t)c->word[2],dy=(int16_t)(c->word[2]>>16);
            for(unsigned y=0;y<h;y++)for(unsigned x=0;x<w;x++) {
                int px=dx+(int)x,py=dy+(int)y;
                assert(px>=0&&py>=0&&px<(int)MISTER_WIDTH&&py<(int)MISTER_HEIGHT);
                uint64_t u=(p[17]+x*p[18]+y*p[19])>>32;
                uint64_t v=(p[20]+x*p[21]+y*p[22])>>32;
                uint64_t r=(p[23]+x*p[24]+y*p[25]+x*y*p[26])>>32;
                uint64_t edge=(p[8]+x*p[9]+y*p[10])>>32;
                assert(r==3+2*u+3*v+u*v && edge==100+2*u+3*v);
                bram[(unsigned)py*MISTER_WIDTH+(unsigned)px]=0xff000000u|((uint32_t)u<<8)|(uint32_t)v;
            }
        } else {
            assert(opcode == 2u || opcode == 3u || opcode == 4u);
            int dx = (int16_t)c->word[2], dy = (int16_t)(c->word[2] >> 16);
            for (unsigned y = 0; y < h; ++y) for (unsigned x = 0; x < w; ++x) {
                int px = dx + (int)x, py = dy + (int)y;
                if (px < 0 || py < 0 || px >= (int)MISTER_WIDTH || py >= (int)MISTER_HEIGHT) continue;
                uint32_t value;
                if (opcode == 3u) value = tintGradient(address, c->word[7], (int)y);
                else {
                    int32_t u, v;
                    if (opcode == 2u) {
                        u = (int32_t)((uint32_t)c->word[4] + x*(uint32_t)c->word[5]);
                        v = (int32_t)((uint32_t)(c->word[4] >> 32) + y*(uint32_t)(c->word[5] >> 32));
                    } else {
                        assert(setupPending);
                        u = (int32_t)((uint32_t)c->word[5] + x*(uint32_t)c->word[6] + y*(uint32_t)c->word[7]);
                        v = (int32_t)((uint32_t)(c->word[5] >> 32) + x*(uint32_t)(c->word[6] >> 32) + y*(uint32_t)(c->word[7] >> 32));
                        if (u < (int32_t)c->word[3] || u >= (int32_t)(c->word[3] >> 32) ||
                            v < (int32_t)c->word[4] || v >= (int32_t)(c->word[4] >> 32)) continue;
                    }
                    assert(u >= 0 && v >= 0);
                    value = *pixel(address + (uint32_t)(v >> 16)*stride + (uint32_t)(u >> 16)*4u);
                    uint32_t tint = opcode == 4u ? affineTint : tintGradient((uint32_t)c->word[6], c->word[7], (int)y);
                    value = modulate(value, tint, (c->word[0] & (1u << 10)) != 0);
                }
                unsigned mode=(c->word[0]&0x200u)?MISTER_GPU_BLEND_SUBTRACT:
                    (c->word[0]&0x100u)?MISTER_GPU_BLEND_ADDITIVE:MISTER_GPU_BLEND_NORMAL;
                uint32_t* target=&bram[(unsigned)py*MISTER_WIDTH+(unsigned)px];
                *target=referenceConstantBlend(*target,value,mode);
            }
            if (opcode == 4u) setupPending = false;
        }
    }
    g_gpu_sequence++;
    g_gpu_write_buffer ^= 1u;
    return true;
}
#include "mister_surfaces_impl.h"
#include "unified_builders.inc"

static uint32_t* patterned(unsigned w, unsigned h) {
    uint32_t* data = malloc((size_t)w*h*4u); assert(data);
    for (unsigned y = 0; y < h; ++y) for (unsigned x = 0; x < w; ++x)
        data[y*w+x] = 0xff000000u | (((x*17u+y*29u)&255u)<<16) | ((y&255u)<<8) | (x&255u);
    return data;
}
static void assertPixels(uint32_t handle, uint32_t* actual, const uint32_t* expected, unsigned w, unsigned h) {
    assert(MisterGpu_surfaceReadback(handle, actual, w*4u));
    for (unsigned p = 0; p < w*h; ++p) {
        if (actual[p] != expected[p]) { fprintf(stderr,"pixel%u got%08x expected%08x\n",p,actual[p],expected[p]); abort(); }
    }
    assert(publications == 0);
}
static void assertGenericRejected(uint64_t packet[64]) {
    GpuSurface before=*gpuSurfaceRecord(g_gpu_surface_selected);
    uint32_t queued=g_gpu_surface_batch_count, descriptors=g_gpu_command_count, allocated=allocation;
    unsigned previousSubmissions=submissions;
    assert(!MisterGpu_addGeneric(0,0,1,1,packet));
    assert(!memcmp(&before,gpuSurfaceRecord(g_gpu_surface_selected),sizeof(before)));
    assert(g_gpu_surface_batch_count==queued&&g_gpu_command_count==descriptors&&allocation==allocated);
    assert(submissions==previousSubmissions&&!MisterGpu_unifiedFailed());
}
static unsigned dmaCount(unsigned opcode) {
    unsigned count=opcode==10u?surfaceLoads:surfaceStores;
    for(unsigned i=0;i<g_gpu_command_count;i++)
        if((commands[i].word[0]&255u)==opcode)count++;
    return count;
}
static void residentCopyRegression(void) {
    const unsigned sizes[3][2]={{1,1},{319,239},{320,240}};
    for(unsigned test=0;test<3;test++) {
        unsigned w=sizes[test][0],h=sizes[test][1];size_t bytes=(size_t)w*h*4u;
        uint32_t* src=patterned(w,h),*dst=patterned(w,h),*expected=patterned(w,h);
        for(unsigned p=0;p<w*h;p++)expected[p]=src[p]=(src[p]&0xffffffu)|((p&255u)<<24);
        uint32_t source=MisterGpu_surfaceEnsure(src,w,h,src,1);
        uint32_t destination=MisterGpu_surfaceEnsure(dst,w,h,dst,1);
        assert(source&&destination&&MisterGpu_surfaceSelect(source));
        if(test==0) {
            assert(MisterGpu_addClear(0x00123456u));expected[0]=0x00123456u;
            assert(gpuSurfaceFlushBatch());
            // Keep this a residency test: a later draw conservatively leaves
            // uniform representation, even though alpha0 changes no pixels.
            assert(MisterGpu_addFill(0,0,1,1,0x00123456u,0));
        }
        else {assert(MisterGpu_addFill(0,0,1,1,0xff123456u,0));expected[0]=0xff123456u;}
        assert(gpuSurfaceFlushBatch());
        assert(g_gpu_surface_resident==source&&g_gpu_surface_resident_dirty);
        // Match the renderer's actual app->host handoff: selected destination,
        // while the preceding source's pixels still reside in BRAM.
        assert(MisterGpu_surfaceSelect(destination));
        unsigned loads=dmaCount(10),stores=dmaCount(11);
        uint32_t sourceRevision=gpuSurfaceRecord(source)->revision;
        uint32_t destinationRevision=gpuSurfaceRecord(destination)->revision;
        assert(MisterGpu_surfaceCopy(destination,0,0,source,0,0,w,h));
        assert(dmaCount(10)==loads&&dmaCount(11)==stores+1);
        assert(g_gpu_surface_selected==destination&&g_gpu_surface_resident==destination);
        assert(g_gpu_surface_resident_dirty&&!gpuSurfaceRecord(destination)->needs_import);
        assert(gpuSurfaceRecord(source)->revision==sourceRevision);
        assert(gpuSurfaceRecord(destination)->revision==destinationRevision+1);
        if(w==320&&h==240) {
            assert(MisterGpu_surfaceSetPresent(destination)&&gpuSurfacePreparePresent());
            assert(dmaCount(10)==loads&&dmaCount(11)==stores+1);
        }
        assertPixels(source,src,expected,w,h);
        // Changing the old source must not change its already-copied target.
        memset(src,0x9a,bytes);assert(MisterGpu_surfaceCpuWritten(source,src,w*4u,100));
        assertPixels(destination,dst,expected,w,h);
        // A clean resident source transfers ownership without any DMA at all;
        // clipping equal negative origins still describes an exact full copy.
        assert(MisterGpu_surfaceSelect(destination));
        loads=dmaCount(10);stores=dmaCount(11);
        assert(MisterGpu_surfaceCopy(source,-1,-1,destination,-1,-1,w+1,h+1));
        assert(dmaCount(10)==loads&&dmaCount(11)==stores);
        assert(g_gpu_surface_selected==destination&&g_gpu_surface_resident==source);
        assertPixels(source,src,expected,w,h);
        // Full identity copy does not flush queued drawing or bump revisions.
        assert(MisterGpu_surfaceSelect(source));
        uint32_t oldLast=expected[w*h-1];
        assert(MisterGpu_addFill(w-1,h-1,1,1,0xff654321u,0));expected[w*h-1]=0xff654321u;
        uint32_t queued=g_gpu_surface_batch_count,revision=gpuSurfaceRecord(source)->revision;
        loads=dmaCount(10);stores=dmaCount(11);
        assert(MisterGpu_surfaceCopy(source,0,0,source,0,0,w,h));
        assert(queued==g_gpu_surface_batch_count&&revision==gpuSurfaceRecord(source)->revision);
        assert(dmaCount(10)==loads&&dmaCount(11)==stores);
        assertPixels(source,src,expected,w,h);
        // Destination was independently preserved before subsequent source writes.
        expected[w*h-1]=oldLast;
        assertPixels(destination,dst,expected,w,h);
        if(test==2) {
            // Partial and offset copies stay on the ordinary DMA path.
            assert(MisterGpu_surfaceSelect(source));assert(gpuSurfaceLoad(source,false));
            assert(MisterGpu_surfaceCopy(destination,1,0,source,0,0,w-1,h));
            assert(g_gpu_surface_resident==0);
            for(unsigned y=0;y<h;y++)for(unsigned x=w-1;x>0;x--)
                expected[y*w+x]=src[y*w+x-1];
            assertPixels(destination,dst,expected,w,h);
        }
        assert(MisterGpu_surfaceRelease(source)&&MisterGpu_surfaceRelease(destination));
        free(expected);free(src);free(dst);
    }
}
static uint32_t referenceConstantBlend(uint32_t background,uint32_t foreground,unsigned mode) {
    unsigned alpha=foreground>>24;uint32_t result=0;
    for(unsigned channel=0;channel<4;channel++) {
        unsigned s=(foreground>>(channel*8))&255u,d=(background>>(channel*8))&255u,value;
        switch(mode) {
            case MISTER_GPU_BLEND_SUBTRACT:value=(unsigned)((uint64_t)d*(255u-s)/255u);break;
            case MISTER_GPU_BLEND_ADDITIVE:
                value=d+(channel==3?alpha:(unsigned)((uint64_t)s*alpha/255u));
                if(value>255)value=255;break;
            default:
                value=channel==3?alpha+(unsigned)((uint64_t)d*(255u-alpha)/255u):
                    (unsigned)(((uint64_t)s*alpha+(uint64_t)d*(255u-alpha))/255u);break;
        }
        result|=value<<(channel*8);
    }
    return result;
}
static void clearFoldRegression(void) {
    uint32_t pixels[35]={0},expected[35];
    uint32_t handle=MisterGpu_surfaceEnsure(pixels,7,5,pixels,1);assert(handle);
    assert(MisterGpu_surfaceSelect(handle));
    const uint32_t backgrounds[3]={0x00123456u,0x804321afu,0xffeeddccu};
    const unsigned alphas[6]={0,1,127,128,254,255};
    for(unsigned cap=0;cap<2;cap++) {
        replaceRectSupported=cap!=0;
        unsigned previousBounded=boundedClears,previousFull=fullClears;
        for(unsigned mode=0;mode<3;mode++)for(unsigned b=0;b<3;b++)for(unsigned a=0;a<6;a++) {
            uint32_t source=0x00d13791u|(alphas[a]<<24),background=backgrounds[b];
            assert(MisterGpu_addClear(background));
            uint32_t revision=gpuSurfaceRecord(handle)->revision;
            assert(MisterGpu_addFill(-1,-1,8,6,source,mode));
            uint32_t result=referenceConstantBlend(background,source,mode);
            assert(g_gpu_surface_batch_count==1&&g_gpu_surface_batch[0].command.word[0]==1);
            assert(g_gpu_surface_batch[0].command.word[1]==result);
            if(getenv("AM2R_TEST_FOLD_VECTORS"))
                printf("FOLD %u %u %u %u\n",mode,source,background,(uint32_t)g_gpu_surface_batch[0].command.word[1]);
            assert(gpuSurfaceRecord(handle)->revision==revision+1);
            for(unsigned p=0;p<35;p++)expected[p]=result;
            assertPixels(handle,pixels,expected,7,5);
        }
        if(cap)assert(boundedClears==previousBounded+54&&fullClears==previousFull);
        else assert(fullClears==previousFull+54&&boundedClears==previousBounded);
    }
    // Coverage, nonuniform destination and row gradients must remain unfused.
    assert(MisterGpu_addClear(0));assert(MisterGpu_addFill(0,0,6,5,UINT32_MAX,0));
    assert(g_gpu_surface_batch_count==2);
    assert(MisterGpu_addFill(0,0,7,5,0xff123456u,0));assert(g_gpu_surface_batch_count==3);
    assert(MisterGpu_addClear(0));
    assert(MisterGpu_addFillVGradient(0,0,7,5,UINT32_MAX,1,0,0,0,0));assert(g_gpu_surface_batch_count==2);
    assert(MisterGpu_addClear(0));assert(MisterGpu_surfaceRelease(handle));
}
static void tiledClearFoldRegression(void) {
    const unsigned sizes[5][4]={{512,256,331,251},{321,241,320,240},
        {639,479,639,240},{319,479,319,240},{640,480,640,480}};
    for(unsigned cap=0;cap<2;cap++)for(unsigned test=0;test<5;test++) {
        replaceRectSupported=cap!=0;
        unsigned w=sizes[test][0],h=sizes[test][1],fw=sizes[test][2],fh=sizes[test][3];
        uint32_t* pixels=patterned(w,h),*expected=patterned(w,h);
        uint32_t handle=MisterGpu_surfaceEnsure(pixels,w,h,pixels,1);assert(handle);
        assert(MisterGpu_surfaceSelect(handle)&&MisterGpu_addClear(UINT32_MAX));
        assert(MisterGpu_addFill(0,0,fw,fh,0xff010101u,MISTER_GPU_BLEND_SUBTRACT));
        // A later draw proves prefix folding does not discard future work.
        assert(MisterGpu_addFill(w-1,h-1,1,1,0xffabcdefu,MISTER_GPU_BLEND_NORMAL));
        for(unsigned y=0;y<h;y++)for(unsigned x=0;x<w;x++)
            expected[y*w+x]=(x<fw&&y<fh)?0x00fefefeu:UINT32_MAX;
        expected[w*h-1]=0xffabcdefu;
        assert(gpuSurfaceFlushBatch());
        if(test==0) {
            unsigned subtracts=0,fullFolded=0;
            for(unsigned i=0;i<g_gpu_command_count;i++) {
                uint64_t* q=commands[i].word;
                if((q[0]&255u)==3u&&(q[0]&0x200u))subtracts++;
                if((q[0]&255u)==1u&&q[1]==0x00fefefeu)fullFolded++;
            }
            assert(subtracts==3&&fullFolded==1); // Only complete320x240 tile folds.
        }
        assertPixels(handle,pixels,expected,w,h);
        assert(MisterGpu_surfaceRelease(handle));free(pixels);free(expected);
    }
    replaceRectSupported=true;
}
static void orderedSurfaceFlushRegression(void) {
    uint32_t* a=patterned(320,240),*b=patterned(64,64),*c=patterned(64,64);
    uint32_t* expected=patterned(320,240),*expectedC=patterned(64,64);
    uint32_t ah=MisterGpu_surfaceEnsure(a,320,240,a,1);
    uint32_t bh=MisterGpu_surfaceEnsure(b,64,64,b,1);
    uint32_t ch=MisterGpu_surfaceEnsure(c,64,64,c,1);assert(ah&&bh&&ch);
    assert(MisterGpu_surfaceSelect(bh)&&MisterGpu_addClear(0xff112233u));
    assert(MisterGpu_surfaceSelect(ah));
    unsigned beforeWaits=waits,beforeSubmissions=submissions,stores=dmaCount(11),loads=dmaCount(10);
    uint32_t stride,address=MisterGpu_surfaceTexture(bh,&stride);assert(address&&stride==256);
    assert(dmaCount(11)==stores+1&&dmaCount(10)==loads); // Only sourceB spill.
    assert(MisterGpu_addClear(0xffaabbccu));
    assert(MisterGpu_addBlit(address,stride,0,0,64,64,0,0,65536,65536,UINT32_MAX,0));
    uint32_t queued=g_gpu_surface_batch_count,descriptors=g_gpu_command_count;
    stores=dmaCount(11);
    assert(MisterGpu_surfaceTexture(bh,&stride)==address);
    assert(queued==g_gpu_surface_batch_count&&descriptors==g_gpu_command_count&&stores==dmaCount(11));
    assert(MisterGpu_addBlit(address,stride,64,0,64,64,0,0,65536,65536,UINT32_MAX,0));
    assert(MisterGpu_surfaceFlush());
    assert(g_gpu_surface_resident==ah&&g_gpu_surface_resident_dirty);
    stores=dmaCount(11);descriptors=g_gpu_command_count;
    assert(MisterGpu_surfaceTexture(bh,&stride)==address&&MisterGpu_surfaceFlush());
    assert(stores==dmaCount(11)&&descriptors==g_gpu_command_count); // Do not spill unrelatedA.
    assert(waits==beforeWaits&&submissions==beforeSubmissions); // Ordering is not a CPU fence.
    // Later GPU mutation of sharedB retires preceding consumers in command
    // order; subsequent sampling sees the new revision without host readback.
    assert(MisterGpu_surfaceSelect(bh)&&MisterGpu_addClear(0xff445566u));
    assert(MisterGpu_surfaceSelect(ah));address=MisterGpu_surfaceTexture(bh,&stride);assert(address);
    assert(MisterGpu_addBlit(address,stride,128,0,64,64,0,0,65536,65536,UINT32_MAX,0));
    assert(MisterGpu_surfaceSelect(ch)&&MisterGpu_addClear(0));
    assert(MisterGpu_addBlit(address,stride,0,0,64,64,0,0,65536,65536,UINT32_MAX,0));
    assert(MisterGpu_surfaceFlush());
    assert(waits==beforeWaits&&submissions==beforeSubmissions);
    for(unsigned y=0;y<240;y++)for(unsigned x=0;x<320;x++)
        expected[y*320+x]=y<64&&x<128?0xff112233u:y<64&&x<192?0xff445566u:0xffaabbccu;
    for(unsigned p=0;p<4096;p++)expectedC[p]=0xff445566u;
    // A genuine CPU write remains a barrier for both queued readers ofB.
    for(unsigned p=0;p<4096;p++)b[p]=0xff778899u;
    assert(MisterGpu_surfaceCpuWritten(bh,b,256,9));
    assert(waits>beforeWaits&&submissions>beforeSubmissions);
    assertPixels(ah,a,expected,320,240);assertPixels(ch,c,expectedC,64,64);
    assert(MisterGpu_surfaceSelect(ah));address=MisterGpu_surfaceTexture(bh,&stride);assert(address);
    assert(MisterGpu_addBlit(address,stride,192,0,64,64,0,0,65536,65536,UINT32_MAX,0));
    for(unsigned y=0;y<64;y++)for(unsigned x=192;x<256;x++)expected[y*320+x]=0xff778899u;
    // Self-source retains the full snapshot/fence path and survives a later
    // clear of the destination before sampling the old pixels again.
    beforeSubmissions=submissions;address=MisterGpu_surfaceTexture(ah,&stride);
    assert(address&&address!=gpuSurfaceRecord(ah)->physical&&submissions>beforeSubmissions);
    assert(MisterGpu_addClear(0xff000000u));
    assert(MisterGpu_addBlit(address,stride,0,0,320,240,0,0,65536,65536,UINT32_MAX,0));
    assertPixels(ah,a,expected,320,240);
    assert(MisterGpu_surfaceRelease(ah)&&MisterGpu_surfaceRelease(bh)&&MisterGpu_surfaceRelease(ch));
    free(a);free(b);free(c);free(expected);free(expectedC);
}

static void orphanReleaseBarrierRegression(void) {
    uint32_t* source=patterned(512,256),*destination=patterned(320,240),*expected=patterned(320,240);
    for(unsigned y=0;y<256;y++)for(unsigned x=0;x<512;x++)
        source[y*512+x]=(source[y*512+x]&0x00ffffffu)|(((x+y)&255u)<<24);
    for(unsigned y=0;y<240;y++)for(unsigned x=0;x<320;x++)expected[y*320+x]=source[y*512+x];
    uint32_t sh=MisterGpu_surfaceEnsure(source,512,256,source,1);
    uint32_t dh=MisterGpu_surfaceEnsure(destination,320,240,destination,1);assert(sh&&dh);
    assert(MisterGpu_surfaceCopy(dh,0,0,sh,0,0,320,240));
    assert(g_gpu_command_count>0 || g_gpu_surface_batch_count>0);
    uint32_t physical=gpuSurfaceRecord(sh)->physical,capacity=gpuSurfaceRecord(sh)->capacity;
    unsigned beforeWaits=waits;
    assert(MisterGpu_surfaceRelease(sh));assert(waits>beforeWaits&&!gpuSurfaceRecord(sh));
    // Once release returns, neither the CPU image nor retired canonical source
    // bytes can still be needed by an accepted consumer, including alpha0 RGB.
    memset(pixel(physical),0xa5,capacity);memset(source,0xcc,512u*256u*4u);
    assertPixels(dh,destination,expected,320,240);
    assert(MisterGpu_surfaceRelease(dh));free(source);free(destination);free(expected);
    puts("Orphan source release: queued raw-RGBA consumer completed before source/DDR poison");
}

static void lazyUniformRegression(void) {
    const unsigned sizes[][2]={{1,1},{319,239},{320,240},{321,241},{512,256},{639,479}};
    const unsigned alphas[]={0,1,127,254,255};
    assert(MisterGpu_flushNoPresent());
    for(unsigned cap=0;cap<2;cap++)for(unsigned size=0;size<6;size++) {
        replaceRectSupported=cap!=0;
        unsigned w=sizes[size][0],h=sizes[size][1];
        uint32_t* pixels=patterned(w,h),*expected=patterned(w,h);
        uint32_t handle=MisterGpu_surfaceEnsure(pixels,w,h,pixels,1);assert(handle);
        assert(MisterGpu_surfaceSelect(handle));
        for(unsigned a=0;a<5;a++)for(unsigned mode=0;mode<3;mode++) {
            uint32_t base=0x007b31adu|(alphas[a]<<24);
            size_t copies=textureCopyBytes;
            unsigned loads=dmaCount(10),stores=dmaCount(11),oldWaits=waits;
            uint32_t commandsBefore=g_gpu_command_count;
            assert(MisterGpu_addClear(base)&&MisterGpu_surfaceFlush());
            assert(gpuSurfaceRecord(handle)->uniform_valid&&!gpuSurfaceRecord(handle)->uniform_ddr_current);
            assert(gpuSurfaceRecord(handle)->uniform_rgba==base&&!gpuSurfaceRecord(handle)->cpu_valid);
            assert(g_gpu_command_count==commandsBefore&&dmaCount(10)==loads&&dmaCount(11)==stores);
            assert(textureCopyBytes==copies&&waits==oldWaits);
            // The first partial draw must seed untouched tiles too. The full
            // quad alternative exercises the same seed with no clear packet
            // in its logical batch (so it cannot use the old prefix fold).
            bool full=(a==4);
            unsigned dw=full?w:1,dh=full?h:1;
            uint32_t foreground=0x7193a72du;
            assert(MisterGpu_addFill(0,0,dw,dh,foreground,mode));
            for(unsigned y=0;y<h;y++)for(unsigned x=0;x<w;x++)
                expected[y*w+x]=x<dw&&y<dh?referenceConstantBlend(base,foreground,mode):base;
            assertPixels(handle,pixels,expected,w,h);
            assert(!gpuSurfaceRecord(handle)->uniform_valid);
            assert(dmaCount(10)==loads&&textureCopyBytes==copies);
        }
        // A clear-only readback materializes the exact raw alpha, including
        // nonzero RGB underneath alpha0. Repeated canonical lookup is free.
        assert(MisterGpu_addClear(0x00123456u)&&MisterGpu_surfaceFlush());
        for(unsigned p=0;p<w*h;p++)expected[p]=0x00123456u;
        assertPixels(handle,pixels,expected,w,h);
        assert(gpuSurfaceRecord(handle)->uniform_valid&&gpuSurfaceRecord(handle)->uniform_ddr_current);
        unsigned stores=dmaCount(11),loads=dmaCount(10),oldWaits=waits;
        assert(gpuSurfaceCanonical(gpuSurfaceRecord(handle)));
        assert(dmaCount(11)==stores&&dmaCount(10)==loads&&waits==oldWaits);
        // Once canonical bytes are current, a tiny write need not re-export
        // every untouched tile merely because the prior content was uniform.
        assert(MisterGpu_addFill(w-1,h-1,1,1,0xff765432u,0));
        expected[w*h-1]=0xff765432u;
        assertPixels(handle,pixels,expected,w,h);
        assert(dmaCount(11)==stores+1&&dmaCount(10)==loads);
        // CPU edit and generation reuse must not keep the stale constant fact.
        pixels[0]=expected[0]=0xffaabbccu;
        assert(MisterGpu_surfaceCpuWritten(handle,pixels,w*4u,300));
        assert(!gpuSurfaceRecord(handle)->uniform_valid);
        assertPixels(handle,pixels,expected,w,h);
        assert(MisterGpu_surfaceRelease(handle));
        uint32_t reused=MisterGpu_surfaceEnsure(pixels,w,h,pixels,301);assert(reused&&reused!=handle);
        assert(!gpuSurfaceRecord(reused)->uniform_valid);
        assert(MisterGpu_surfaceRelease(reused));
        free(pixels);free(expected);
    }
    replaceRectSupported=true;

    uint32_t* host=patterned(320,240),*other=patterned(320,240),*expected=patterned(320,240);
    uint32_t hh=MisterGpu_surfaceEnsure(host,320,240,host,1);
    uint32_t oh=MisterGpu_surfaceEnsure(other,320,240,other,1);assert(hh&&oh);
    assert(MisterGpu_surfaceSelect(oh)&&MisterGpu_addClear(0xff010203u));
    assert(MisterGpu_addFill(0,0,1,1,0xffabcdefu,0)&&MisterGpu_surfaceFlush());
    assert(g_gpu_surface_resident==oh&&g_gpu_surface_resident_dirty);
    unsigned stores=dmaCount(11),loads=dmaCount(10),oldWaits=waits;
    uint32_t commandsBefore=g_gpu_command_count;
    assert(MisterGpu_surfaceSelect(hh)&&MisterGpu_addClear(0xff000000u));
    assert(MisterGpu_surfaceFlush());
    assert(g_gpu_surface_resident==oh&&g_gpu_surface_resident_dirty);
    assert(g_gpu_command_count==commandsBefore&&dmaCount(11)==stores&&dmaCount(10)==loads&&waits==oldWaits);
    assert(MisterGpu_addFill(0,0,320,240,0x80808080u,0)&&MisterGpu_surfaceFlush());
    assert(dmaCount(11)==stores+1&&dmaCount(10)==loads); // Preserve other, no black-host round trip.
    for(unsigned p=0;p<320u*240u;p++)expected[p]=0xff404040u;
    assertPixels(hh,host,expected,320,240);

    // Canonical source is versioned through queue ordering, not CPU fills.
    // 700 draws exceed both the small descriptor bank and the logical batch.
    assert(MisterGpu_surfaceSelect(oh)&&MisterGpu_addClear(0xff102030u));
    assert(MisterGpu_surfaceSelect(hh)&&MisterGpu_addClear(0));
    uint32_t stride,address=MisterGpu_surfaceTexture(oh,&stride);assert(address);
    for(unsigned p=0;p<700;p++)assert(MisterGpu_addBlit(address,stride,p%320,p/320,1,1,0,0,0,0,UINT32_MAX,0));
    assert(MisterGpu_surfaceSelect(oh)&&MisterGpu_addClear(0xff405060u));
    assert(MisterGpu_surfaceSelect(hh));address=MisterGpu_surfaceTexture(oh,&stride);assert(address);
    assert(MisterGpu_addBlit(address,stride,0,3,320,1,0,0,0,0,UINT32_MAX,0));
    for(unsigned p=0;p<320u*240u;p++)expected[p]=p<700?0xff102030u:p>=960&&p<1280?0xff405060u:0;
    assertPixels(hh,host,expected,320,240);
    // The current source also survives save-style readback/restore as normal
    // canonical bytes, without serializing any new metadata into state files.
    assert(MisterGpu_surfaceSelect(oh)&&MisterGpu_addClear(0x00345678u));
    assert(gpuSurfacePrepareCheckpoint());
    memset(pixel(gpuSurfaceRecord(oh)->physical),0xa5,
           gpuSurfaceRecord(oh)->stride*gpuSurfaceRecord(oh)->height);
    assert(gpuSurfaceRestoreCheckpoint());
    assert(!gpuSurfaceRecord(oh)->uniform_valid);
    for(unsigned p=0;p<320u*240u;p++)expected[p]=0x00345678u;
    assertPixels(oh,other,expected,320,240);
    assert(MisterGpu_surfaceSelect(oh)&&MisterGpu_addClear(0x00785634u));
    assert(MisterGpu_surfaceSetPresent(oh)&&gpuSurfacePreparePresent());
    assert(g_gpu_surface_resident==oh);
    assert(gpuSurfaceSubmitFence());
    for(unsigned p=0;p<320u*240u;p++)assert(bram[p]==0x00785634u);
    assert(MisterGpu_surfaceRelease(hh)&&MisterGpu_surfaceRelease(oh));
    free(host);free(other);free(expected);

    // Partial raw-copy must preserve both targets' transparent constant
    // backgrounds, not read stale pre-clear DDR outside the copied rectangle.
    uint32_t* src=patterned(513,257),*dst=patterned(321,241),*out=patterned(321,241);
    uint32_t sh=MisterGpu_surfaceEnsure(src,513,257,src,1);
    uint32_t dh=MisterGpu_surfaceEnsure(dst,321,241,dst,1);assert(sh&&dh);
    assert(MisterGpu_surfaceSelect(sh)&&MisterGpu_addClear(0x00123456u));
    assert(MisterGpu_surfaceSelect(dh)&&MisterGpu_addClear(0x80987654u));
    assert(MisterGpu_surfaceCopy(dh,7,9,sh,3,4,100,80));
    for(unsigned y=0;y<241;y++)for(unsigned x=0;x<321;x++)
        out[y*321+x]=x>=7&&x<107&&y>=9&&y<89?0x00123456u:0x80987654u;
    assertPixels(dh,dst,out,321,241);
    const int offsets[4][2]={{1,1},{-1,1},{1,-1},{-1,-1}};
    for(unsigned direction=0;direction<4;direction++) {
        assert(MisterGpu_surfaceSelect(dh)&&MisterGpu_addClear(0x00112233u));
        assert(MisterGpu_surfaceCopy(dh,offsets[direction][0]>0,offsets[direction][1]>0,
            dh,offsets[direction][0]<0,offsets[direction][1]<0,320,240));
        for(unsigned p=0;p<321u*241u;p++)out[p]=0x00112233u;
        assertPixels(dh,dst,out,321,241);
    }
    // Generic packet state is opaque to the manager. Its first draw over a
    // deferred clear must inherit the same raw seed and tile-local planes.
    uint64_t packet[64]={0};packet[0]=0x31504741u;
    packet[8]=100ull<<32;packet[9]=2ull<<32;packet[10]=3ull<<32;
    packet[18]=1ull<<32;packet[22]=1ull<<32;
    packet[23]=3ull<<32;packet[24]=2ull<<32;packet[25]=3ull<<32;packet[26]=1ull<<32;
    assert(MisterGpu_addClear(0x00112233u)&&MisterGpu_surfaceFlush());
    assert(MisterGpu_addGeneric(318,238,3,3,packet));
    for(unsigned y=238;y<241;y++)for(unsigned x=318;x<321;x++)
        out[y*321+x]=0xff000000u|((x-318)<<8)|(y-238);
    assertPixels(dh,dst,out,321,241);
    assert(MisterGpu_surfaceRelease(sh)&&MisterGpu_surfaceRelease(dh));
    free(src);free(dst);free(out);
}

static void lazyUniformFailureRegression(void) {
    // Fail each CLEAR and each STORE across every one of four tiles.
    // Failed ordinary-RAM test state is explicitly reset between independent
    // fault injections; production never attempts recovery from this failure.
    uint32_t* pixels=patterned(639,479);
    for(unsigned command=0;command<8;command++) {
        uint32_t handle=MisterGpu_surfaceEnsure(pixels,639,479,pixels,1);assert(handle);
        assert(MisterGpu_surfaceSelect(handle)&&MisterGpu_addClear(0x00123456u));
        assert(MisterGpu_surfaceFlush()&&MisterGpu_flushNoPresent());
        MisterGpuCommand filler={{1u,0xff000000u}};
        unsigned count=GPU_COMMAND_CAPACITY-1u-command;
        while(g_gpu_command_count<count)assert(gpuSurfaceRaw(&filler));
        uint32_t revision=gpuSurfaceRecord(handle)->revision;
        fail_wait=true;
        assert(!gpuSurfaceCanonical(gpuSurfaceRecord(handle)));
        assert(MisterGpu_unifiedFailed()&&!gpuSurfaceRecord(handle)->uniform_ddr_current);
        assert(gpuSurfaceRecord(handle)->uniform_valid&&gpuSurfaceRecord(handle)->revision==revision);
        fail_wait=false;g_gpu_surface_failed=false;g_gpu_available=true;g_gpu_command_count=0;
        g_gpu_surface_resident=0;g_gpu_surface_resident_dirty=false;
        assert(MisterGpu_surfaceRelease(handle));
    }
    free(pixels);
}

static void expectClear(uint32_t* expected,unsigned w,unsigned h,uint32_t color) {
    for(unsigned p=0;p<w*h;p++)expected[p]=color;
}
static void expectFill(uint32_t* expected,unsigned w,unsigned h,
        int x,int y,int width,int height,uint32_t color,unsigned mode) {
    for(int row=y;row<y+height;row++)for(int col=x;col<x+width;col++)
        if(row>=0&&col>=0&&row<(int)h&&col<(int)w)
            expected[(unsigned)row*w+(unsigned)col]=referenceConstantBlend(
                expected[(unsigned)row*w+(unsigned)col],color,mode);
}
static void backgroundExportRegression(void) {
    const unsigned sizes[][2]={{320,240},{319,239},{331,251},{639,479}};
    const uint32_t colors[]={0x00123456u,0x01765432u,0x80765432u,0xff000000u};
    assert(MisterGpu_flushNoPresent());
    for(unsigned cap=0;cap<2;cap++)for(unsigned test=0;test<4;test++) {
        replaceRectSupported=cap!=0;
        unsigned w=sizes[test][0],h=sizes[test][1];
        uint32_t base=colors[test],foreground=0x7fab193du;
        uint32_t* pixels=patterned(w,h),*expected=patterned(w,h);
        uint32_t handle=MisterGpu_surfaceEnsure(pixels,w,h,pixels,1);assert(handle);
        assert(MisterGpu_surfaceSelect(handle));
        uint64_t before=surfaceStoreBytes;
        assert(MisterGpu_addClear(base)&&MisterGpu_addFill(3,5,17,11,foreground,0));
        expectClear(expected,w,h,base);expectFill(expected,w,h,3,5,17,11,foreground,0);
        assertPixels(handle,pixels,expected,w,h);
        assert(surfaceStoreBytes-before==(uint64_t)w*h*4u); // Unknown canonical bytes.
        assert(gpuSurfaceRecord(handle)->canonical_background.valid);
        before=surfaceStoreBytes;
        assert(MisterGpu_addClear(base)&&MisterGpu_addFill(3,5,17,11,foreground,0));
        assertPixels(handle,pixels,expected,w,h);
        assert(surfaceStoreBytes-before==17u*11u*4u);

        // Erase the old rectangle and write a new one at the opposite edge.
        // Large cases cross both tile boundaries and use odd local origins.
        unsigned nx=w-17,ny=h-13;
        before=surfaceStoreBytes;
        assert(MisterGpu_addClear(base)&&MisterGpu_addFill(nx,ny,11,7,0x9f34a971u,2));
        expectClear(expected,w,h,base);expectFill(expected,w,h,nx,ny,11,7,0x9f34a971u,2);
        assertPixels(handle,pixels,expected,w,h);
        assert(surfaceStoreBytes-before==(uint64_t)(nx+11-3)*(ny+7-5)*4u);
        // Clear-only materialization must erase the disappeared draw, not just
        // declare the untouched background current. Repeating it then costs0.
        before=surfaceStoreBytes;
        assert(MisterGpu_addClear(base));expectClear(expected,w,h,base);
        assertPixels(handle,pixels,expected,w,h);
        assert(surfaceStoreBytes-before==11u*7u*4u);
        before=surfaceStoreBytes;
        assert(MisterGpu_addClear(base));assertPixels(handle,pixels,expected,w,h);
        assert(surfaceStoreBytes==before);
        // Alpha-only background changes are not visually-equivalent shortcuts.
        base^=0x01000000u;before=surfaceStoreBytes;
        assert(MisterGpu_addClear(base));expectClear(expected,w,h,base);
        assertPixels(handle,pixels,expected,w,h);
        assert(surfaceStoreBytes-before==(uint64_t)w*h*4u);
        // RGB-only changes under zero alpha also require all raw pixels.
        base=0x00345678u;assert(MisterGpu_addClear(base));expectClear(expected,w,h,base);
        assertPixels(handle,pixels,expected,w,h);before=surfaceStoreBytes;base^=0x00010000u;
        assert(MisterGpu_addClear(base));expectClear(expected,w,h,base);
        assertPixels(handle,pixels,expected,w,h);
        assert(surfaceStoreBytes-before==(uint64_t)w*h*4u);

        // CPU writes, checkpoint restore and generation reuse invalidate the
        // proof. The next full clear must establish every canonical byte again.
        pixels[0]^=0x10203040u;assert(MisterGpu_surfaceCpuWritten(handle,pixels,w*4u,700));
        assert(!gpuSurfaceRecord(handle)->background.valid&&!gpuSurfaceRecord(handle)->canonical_background.valid);
        before=surfaceStoreBytes;
        assert(MisterGpu_addClear(base)&&MisterGpu_addFill(3,5,17,11,foreground,0));
        expectClear(expected,w,h,base);expectFill(expected,w,h,3,5,17,11,foreground,0);
        assertPixels(handle,pixels,expected,w,h);
        assert(surfaceStoreBytes-before==(uint64_t)w*h*4u);
        assert(gpuSurfacePrepareCheckpoint());
        memset(pixel(gpuSurfaceRecord(handle)->physical),0xa5,gpuSurfaceRecord(handle)->stride*h);
        assert(gpuSurfaceRestoreCheckpoint());
        assert(!gpuSurfaceRecord(handle)->background.valid&&!gpuSurfaceRecord(handle)->canonical_background.valid);
        assertPixels(handle,pixels,expected,w,h);
        before=surfaceStoreBytes;
        assert(MisterGpu_addClear(base)&&MisterGpu_addFill(3,5,17,11,foreground,0));
        assertPixels(handle,pixels,expected,w,h);
        assert(surfaceStoreBytes-before==(uint64_t)w*h*4u);
        assert(MisterGpu_surfaceRelease(handle));
        uint32_t reused=MisterGpu_surfaceEnsure(pixels,w,h,pixels,701);assert(reused&&reused!=handle);
        assert(!gpuSurfaceRecord(reused)->background.valid&&!gpuSurfaceRecord(reused)->canonical_background.valid);
        assert(MisterGpu_surfaceRelease(reused));free(pixels);free(expected);
    }
    replaceRectSupported=true;

    // General 320x240 target with draws confined to its first32 rows, as in
    // the captured workload. No room/effect recognition exists in the manager.
    uint32_t* pixels=patterned(320,240),*expected=patterned(320,240);
    uint32_t handle=MisterGpu_surfaceEnsure(pixels,320,240,pixels,1);assert(handle);
    assert(MisterGpu_surfaceSelect(handle));
    for(unsigned frame=0;frame<2;frame++) {
        uint64_t before=surfaceStoreBytes;
        assert(MisterGpu_addClear(0x00123456u));expectClear(expected,320,240,0x00123456u);
        assert(MisterGpu_addFill(0,0,320,32,0x80876543u,0));
        expectFill(expected,320,240,0,0,320,32,0x80876543u,0);
        assertPixels(handle,pixels,expected,320,240);
        uint64_t bytes=surfaceStoreBytes-before;
        assert(bytes==(frame?40960u:307200u));
        if(frame)printf("Conservative background exports: 307200 -> %llu bytes; %llu bytes eliminated (ordinary-RAM fixture, not hardware timing)\n",
                        (unsigned long long)bytes,(unsigned long long)(307200u-bytes));
    }
    // Draws outside the target are empty; negative-origin clipping is exact.
    uint64_t before=surfaceStoreBytes;
    assert(MisterGpu_addClear(0x00123456u));expectClear(expected,320,240,0x00123456u);
    assert(MisterGpu_addFill(-7,-9,11,13,0xffabcdefu,0));expectFill(expected,320,240,-7,-9,11,13,0xffabcdefu,0);
    assert(MisterGpu_addFill(321,241,9,9,0xffabcdefu,0));
    assertPixels(handle,pixels,expected,320,240);assert(surfaceStoreBytes-before==40960u);
    // Multiple logical/descriptor flushes must retain the old base proof and
    // never omit a written pixel. The bounding rectangle can safely grow full.
    assert(MisterGpu_addClear(0x00123456u));expectClear(expected,320,240,0x00123456u);
    for(unsigned i=0;i<700;i++) {
        unsigned x=(i*17u)%320u,y=(i*23u)%240u;
        assert(MisterGpu_addFill(x,y,1,1,0xff000000u|i,0));expected[y*320+x]=0xff000000u|i;
    }
    assertPixels(handle,pixels,expected,320,240);
    assert(MisterGpu_surfaceRelease(handle));free(pixels);free(expected);
}

static void backgroundSourceRegression(void) {
    uint32_t* a=patterned(32,24),*b=patterned(64,24),*expected=patterned(64,24);
    uint32_t ah=MisterGpu_surfaceEnsure(a,32,24,a,1),bh=MisterGpu_surfaceEnsure(b,64,24,b,1);
    assert(ah&&bh&&MisterGpu_surfaceSelect(ah));
    assert(MisterGpu_addClear(0xff010203u)&&MisterGpu_addFill(3,4,5,7,0xff765432u,0));
    assert(MisterGpu_surfaceSelect(bh)&&MisterGpu_addClear(0x00887766u));
    uint32_t stride,address=MisterGpu_surfaceTexture(ah,&stride);assert(address);
    assert(MisterGpu_addBlit(address,stride,0,0,32,24,0,0,65536,65536,UINT32_MAX,0));
    expectClear(expected,64,24,0x00887766u);
    for(unsigned y=0;y<24;y++)for(unsigned x=0;x<32;x++)
        expected[y*64+x]=x>=3&&x<8&&y>=4&&y<11?0xff765432u:0xff010203u;
    // Replacing the source's small dirty region must retire the old consumer
    // before the same canonical address is updated to the new version.
    assert(MisterGpu_surfaceSelect(ah));
    assert(MisterGpu_addClear(0xff010203u)&&MisterGpu_addFill(19,13,9,5,0xffabcdefu,0));
    assert(MisterGpu_surfaceSelect(bh));address=MisterGpu_surfaceTexture(ah,&stride);assert(address);
    assert(MisterGpu_addBlit(address,stride,32,0,32,24,0,0,65536,65536,UINT32_MAX,0));
    for(unsigned y=0;y<24;y++)for(unsigned x=0;x<32;x++)
        expected[y*64+32+x]=x>=19&&x<28&&y>=13&&y<18?0xffabcdefu:0xff010203u;
    assertPixels(bh,b,expected,64,24);
    // Self-source immutable snapshot plus subsequent clear cannot alias the
    // partial canonical stores. Copy paths deliberately discard the proof.
    assert(MisterGpu_surfaceSelect(bh));address=MisterGpu_surfaceTexture(bh,&stride);assert(address);
    assert(MisterGpu_addClear(0x00887766u));
    assert(MisterGpu_addBlit(address,stride,0,0,64,24,0,0,65536,65536,UINT32_MAX,0));
    assertPixels(bh,b,expected,64,24);
    assert(MisterGpu_surfaceCopy(bh,1,1,bh,0,0,63,23));
    for(unsigned y=23;y>0;y--)for(unsigned x=63;x>0;x--)expected[y*64+x]=expected[(y-1)*64+x-1];
    assert(!gpuSurfaceRecord(bh)->background.valid&&!gpuSurfaceRecord(bh)->canonical_background.valid);
    assertPixels(bh,b,expected,64,24);
    assert(MisterGpu_surfaceRelease(ah)&&MisterGpu_surfaceRelease(bh));free(a);free(b);free(expected);
}

static void backgroundFailureRegression(void) {
    uint32_t* pixels=patterned(320,240),*expected=patterned(320,240);
    uint32_t handle=MisterGpu_surfaceEnsure(pixels,320,240,pixels,1);assert(handle);
    assert(MisterGpu_surfaceSelect(handle)&&MisterGpu_addClear(0x00123456u));
    assert(MisterGpu_addFill(5,7,9,11,0xffabcdefu,0));
    expectClear(expected,320,240,0x00123456u);expectFill(expected,320,240,5,7,9,11,0xffabcdefu,0);
    assertPixels(handle,pixels,expected,320,240);
    GpuSurfaceBackground canonical=gpuSurfaceRecord(handle)->canonical_background;
    assert(MisterGpu_addClear(0x00123456u)&&MisterGpu_addFill(19,23,13,17,0xff987654u,0));
    assert(MisterGpu_surfaceFlush());
    MisterGpuCommand filler={{1u,0xff000000u}};
    while(g_gpu_command_count<GPU_COMMAND_CAPACITY-1u)assert(gpuSurfaceRaw(&filler));
    fail_wait=true;
    assert(!gpuSurfaceSpill()&&MisterGpu_unifiedFailed());
    assert(g_gpu_surface_resident_dirty&&!memcmp(&canonical,&gpuSurfaceRecord(handle)->canonical_background,sizeof(canonical)));
    // Only the isolated ordinary-RAM fixture recovers this injected fatal job.
    fail_wait=false;g_gpu_surface_failed=false;g_gpu_command_count=0;
    g_gpu_surface_resident=0;g_gpu_surface_resident_dirty=false;
    assert(MisterGpu_surfaceRelease(handle));free(pixels);free(expected);

    // Large batches must not publish the next whole canonical fact after only
    // some tile STORE descriptors have been accepted. Fail at each tile STORE.
    pixels=patterned(639,479);expected=patterned(639,479);
    for(unsigned tile=0;tile<4;tile++) {
        handle=MisterGpu_surfaceEnsure(pixels,639,479,pixels,1);assert(handle);
        assert(MisterGpu_surfaceSelect(handle)&&MisterGpu_addClear(0x00123456u));
        assert(MisterGpu_addFill(5,7,9,11,0xffabcdefu,0));
        expectClear(expected,639,479,0x00123456u);expectFill(expected,639,479,5,7,9,11,0xffabcdefu,0);
        assertPixels(handle,pixels,expected,639,479);
        canonical=gpuSurfaceRecord(handle)->canonical_background;
        assert(MisterGpu_addClear(0x00123456u)&&MisterGpu_addFill(1,1,637,477,0x80402010u,0));
        unsigned count=GPU_COMMAND_CAPACITY-1u-(tile*3u+2u);
        while(g_gpu_command_count<count)assert(gpuSurfaceRaw(&filler));
        fail_wait=true;
        assert(!gpuSurfaceFlushBatch()&&MisterGpu_unifiedFailed());
        assert(!memcmp(&canonical,&gpuSurfaceRecord(handle)->canonical_background,sizeof(canonical)));
        fail_wait=false;g_gpu_surface_failed=false;g_gpu_command_count=0;g_gpu_surface_batch_count=0;
        g_gpu_surface_resident=0;g_gpu_surface_resident_dirty=false;
        assert(MisterGpu_surfaceRelease(handle));
    }
    free(pixels);free(expected);
}

static unsigned liveSurfaces(void) {
    unsigned count=0;
    for(unsigned i=0;i<GPU_SURFACE_RECORDS;i++)count+=g_gpu_surfaces[i].live;
    return count;
}
static void backgroundLifecycleRegression(void) {
    const unsigned sizes[][2]={{17,13},{319,239},{331,251},{639,479},{331,251},{17,13}};
    assert(MisterGpu_flushNoPresent());
    unsigned baselineLive=liveSurfaces(),highWater=0;
    for(unsigned round=0;round<30;round++) {
        for(unsigned shape=0;shape<6;shape++) {
            unsigned w=sizes[shape][0],h=sizes[shape][1];
            uint32_t* a=patterned(w,h),*b=patterned(w,h),*expected=patterned(w,h);
            uint32_t ah=MisterGpu_surfaceEnsure(a,w,h,a,1),bh=MisterGpu_surfaceEnsure(b,w,h,b,1);
            assert(ah&&bh&&liveSurfaces()==baselineLive+2);
            uint32_t color=0x00123456u|(round<<24);
            assert(MisterGpu_surfaceSelect(ah)&&MisterGpu_addClear(color));
            assert(MisterGpu_addFill(3,5,7,5,0xffabcdefu,0));
            assert(MisterGpu_surfaceSelect(bh)&&MisterGpu_addClear(0xff987654u));
            assert(MisterGpu_surfaceCopy(bh,0,0,ah,0,0,w,h));
            expectClear(expected,w,h,color);expectFill(expected,w,h,3,5,7,5,0xffabcdefu,0);
            assertPixels(bh,b,expected,w,h);
            assert(MisterGpu_surfaceCopy(bh,1,1,bh,0,0,w-1,h-1));
            for(unsigned y=h-1;y>0;y--)for(unsigned x=w-1;x>0;x--)
                expected[y*w+x]=expected[(y-1)*w+x-1];
            assertPixels(bh,b,expected,w,h);
            if((round*6+shape)%11==0) {
                assert(gpuSurfacePrepareCheckpoint());
                GpuArenaSegment arenaBefore[GPU_ARENA_SEGMENTS];
                memcpy(arenaBefore,g_gpu_arena,sizeof(arenaBefore));
                memset(pixel(gpuSurfaceRecord(bh)->physical),0xa5,gpuSurfaceRecord(bh)->stride*h);
                assert(gpuSurfaceRestoreCheckpoint());assertPixels(bh,b,expected,w,h);
                assert(!memcmp(arenaBefore,g_gpu_arena,sizeof(arenaBefore)));
            }
            assert(MisterGpu_surfaceRelease(ah)&&MisterGpu_surfaceRelease(bh));
            assert(!gpuSurfaceRecord(ah)&&!gpuSurfaceRecord(bh));
            assert(liveSurfaces()==baselineLive&&g_gpu_surface_batch_count==0&&g_gpu_command_count==0);
            free(a);free(b);free(expected);
        }
        // Repeated bounded dimensions/copy snapshots reuse capacity after the
        // first shape cycle. Increasing-size arena churn is independently
        // covered by the production allocator fixture.
        if(round==0)highWater=allocation;else assert(allocation==highWater);
    }
    printf("Surface lifecycle: 180 create/copy/overlap/release cycles and17 checkpoint restores; pool stable at %u bytes after first shape cycle\n",highWater);
}
static void logicalRestoreLifecycleRegression(void) {
    const unsigned w=41,h=31;uint32_t* pixels=patterned(w,h),*saved=patterned(w,h);
    uint32_t handle=MisterGpu_surfaceEnsure(pixels,w,h,pixels,9);assert(handle);
    assert(MisterGpu_surfaceSelect(handle)&&MisterGpu_addClear(0x00123456u));
    assert(MisterGpu_addFill(3,5,7,9,0xffabcdefu,0));
    assert(MisterGpu_surfaceReadback(handle,saved,w*4u));
    assert(MisterGpu_addClear(0xff554433u)); // Later state may still be queued.
    assert(MisterGpu_surfaceReset());assert(!gpuSurfaceRecord(handle));
    memcpy(pixels,saved,w*h*4u); // SW fast restore creates surfaces from saved RGBA.
    uint32_t restored=MisterGpu_surfaceEnsure(pixels,w,h,pixels,9);assert(restored&&restored!=handle);
    assertPixels(restored,pixels,saved,w,h);
    assert(MisterGpu_surfaceRelease(restored));free(pixels);free(saved);
    puts("Logical .fast manager lifecycle: queued later state retired; recreated saved raw RGBA and new generation exact");
}
int main(void) {
    gpuArenaReset();
    g_gpu_textures = calloc(1, TEST_DDR_BYTES); assert(g_gpu_textures);
    assert(MisterGpu_setUnifiedEnabled(true));
    uint32_t* a = patterned(321,241), *expect = patterned(321,241);
    uint32_t ah = MisterGpu_surfaceEnsure(a,321,241,a,1); assert(ah);
    assert(textureCopyBytes==0); // Allocation does not import initial CPU zeros/pixels.
    assert(MisterGpu_surfaceSelect(ah));
    assert(MisterGpu_addClear(0x37112233));
    // Step-event commands already queued before Draw begin must survive.
    assert(g_gpu_surface_batch_count==1);MisterGpu_beginFrame();assert(g_gpu_surface_batch_count==1);
    for (unsigned p=0;p<321u*241u;p++) expect[p]=0x37112233;
    assert(MisterGpu_addFill(310,230,11,11,0xfffedcba,0));
    for(unsigned y=230;y<241;y++)for(unsigned x=310;x<321;x++)expect[y*321+x]=0xfffedcba;
    assertPixels(ah,a,expect,321,241);
    assert(textureCopyBytes==0); // A replacing clear is entirely FPGA-owned.
    uint32_t* partial=patterned(321,241);uint32_t ph=MisterGpu_surfaceEnsure(partial,321,241,partial,1);assert(ph);
    assert(MisterGpu_surfaceSelect(ph));assert(MisterGpu_addFill(0,0,1,1,0xff123456,0));
    partial[0]=0xff123456; // Expected initial pixels are preserved outside this write.
    assert(MisterGpu_flushNoPresent());assert(textureCopyBytes==321u*241u*4u);
    uint32_t* partialResult=calloc(321u*241u,4u);assertPixels(ph,partialResult,partial,321,241);
    assert(MisterGpu_surfaceRelease(ph));free(partialResult);free(partial);
    assert(MisterGpu_surfaceSelect(ah));

    // Generic packets are value snapshots, not retained CPU pointers. Verify
    // all tiled edge/UV/bilinear origin and derivative transforms algebraically.
    uint64_t packet[64]={0};packet[0]=0x31504741u;
    packet[8]=100ull<<32;packet[9]=2ull<<32;packet[10]=3ull<<32;
    packet[18]=1ull<<32;packet[22]=1ull<<32;
    packet[23]=3ull<<32;packet[24]=2ull<<32;packet[25]=3ull<<32;packet[26]=1ull<<32;
    assert(gpuSurfaceGenericPacketValid(packet));
    for(unsigned q=4;q<64;q++)if(q<8||q>=39) {
        packet[q]=1;assertGenericRejected(packet);packet[q]=0;
    }
    packet[0]|=1ull<<34;assertGenericRejected(packet);packet[0]&=~(1ull<<34);
    packet[2]=1ull<<44;assertGenericRejected(packet);
    packet[2]=1ull<<59;assertGenericRejected(packet);
    packet[2]=7ull<<32;assertGenericRejected(packet);
    packet[2]=6ull<<32;packet[3]=0x02020202u;assert(gpuSurfaceGenericPacketValid(packet));
    for(unsigned channel=0;channel<4;channel++)for(unsigned bad=0;bad<=12;bad+=12) {
        packet[3]=(0x02020202u&~(255ull<<(channel*8)))|((uint64_t)bad<<(channel*8));
        assertGenericRejected(packet);
    }
    packet[3]=0;packet[0]|=1ull<<32;packet[2]=2u|(2ull<<16);
    packet[1]=(GPU_TEXTURE_PHYS+allocation-16u)|(8ull<<32);
    assert(gpuSurfaceGenericPacketValid(packet));
    packet[1]=(GPU_TEXTURE_PHYS+allocation-8u)|(8ull<<32);assertGenericRejected(packet);
    packet[1]=(GPU_TEXTURE_PHYS-4u)|(8ull<<32);assertGenericRejected(packet);
    packet[1]=GPU_TEXTURE_PHYS|(4ull<<32);assertGenericRejected(packet);
    packet[1]=(GPU_TEXTURE_PHYS+1u)|(8ull<<32);assertGenericRejected(packet);
    packet[1]=GPU_TEXTURE_PHYS|(10ull<<32);assertGenericRejected(packet);
    packet[2]=2u|(65535ull<<16);packet[1]=GPU_TEXTURE_PHYS|(0xfffffffcull<<32);assertGenericRejected(packet);
    uint32_t freedHole=reserveGpuTexture(256);assert(freedHole);
    packet[2]=2u|(2ull<<16);packet[1]=freedHole|(8ull<<32);
    assert(gpuSurfaceGenericPacketValid(packet));
    assert(gpuArenaRelease(freedHole));assertGenericRejected(packet);
    packet[2]=0;packet[1]=0;assertGenericRejected(packet);
    packet[0]&=~(1ull<<32);
    for(unsigned n=0;n<40;n++)assert(MisterGpu_addGeneric(0,0,321,241,packet));
    memset(packet,0xee,sizeof(packet));
    for(unsigned y=0;y<241;y++)for(unsigned x=0;x<321;x++)expect[y*321+x]=0xff000000u|(x<<8)|y;
    assertPixels(ah,a,expect,321,241);assert(generic_packets==160);

    // Exact RGBA and untouched borders survive all directions of overlapping,
    // cross-tile self-copy. Reuse of snapshot storage fences prior consumers.
    const int offsets[4][2]={{7,9},{-7,9},{7,-9},{-7,-9}};
    uint32_t* initial=patterned(321,241), *snapshot=patterned(321,241);
    for(unsigned p=0;p<321u*241u;p++) initial[p]=(initial[p]&0xffffffu)|((p&255u)<<24);
    for(unsigned n=0;n<4;n++) {
        memcpy(a,initial,321u*241u*4u); memcpy(expect,initial,321u*241u*4u);
        assert(MisterGpu_surfaceCpuWritten(ah,a,321*4u,n+2));
        int sx=offsets[n][0]<0?7:0,sy=offsets[n][1]<0?9:0;
        int dx=offsets[n][0]>0?7:0,dy=offsets[n][1]>0?9:0;
        memcpy(snapshot,expect,321u*241u*4u);
        for(int y=0;y<230;y++)for(int x=0;x<310;x++)expect[(dy+y)*321+dx+x]=snapshot[(sy+y)*321+sx+x];
        assert(MisterGpu_surfaceCopy(ah,dx,dy,ah,sx,sy,310,230));
        assertPixels(ah,a,expect,321,241);
    }
    uint32_t* b=patterned(513,257);uint32_t bh=MisterGpu_surfaceEnsure(b,513,257,b,1);assert(bh);
    assert(MisterGpu_surfaceSelect(ah));
    uint32_t stride=0,source=MisterGpu_surfaceTexture(bh,&stride);assert(source && stride==2056);
    assert(MisterGpu_addBlit(source,stride,0,0,321,241,0,0,65536,65536,UINT32_MAX,0));
    for(unsigned y=0;y<241;y++)memcpy(expect+y*321,b+y*513,321*4u);
    // Modifying the source must retire its earlier queued reader first.
    assert(MisterGpu_surfaceReadback(bh,b,513*4u));
    memset(b,0,513u*257u*4u); assert(MisterGpu_surfaceCpuWritten(bh,b,513*4u,2));
    assertPixels(ah,a,expect,321,241);

    // Setup and affine draw must remain adjacent across a bounded job flush;
    // translated starts must sample the same pattern beyond both tile edges.
    for(unsigned y=0;y<257;y++)for(unsigned x=0;x<513;x++) b[y*513+x]=0xff000000u|(x<<8)|y;
    assert(MisterGpu_surfaceCpuWritten(bh,b,513*4u,3));
    source=MisterGpu_surfaceTexture(bh,&stride);assert(source);
    for(unsigned n=0;n<47;n++)assert(MisterGpu_addFill(0,0,1,1,0xffabcdefu,0));
    assert(MisterGpu_addAffineBlit(source,stride,0,0,321,241,0,513<<16,0,257<<16,
                                  0,0,65536,0,0,65536,UINT32_MAX,0));
    for(unsigned y=0;y<241;y++)for(unsigned x=0;x<321;x++)expect[y*321+x]=b[y*513+x];
    assertPixels(ah,a,expect,321,241);

    // Thousands of accepted draws exceed both descriptor capacity and the
    // logical batch bound. No-present drains may not drop or duplicate work.
    unsigned before=submissions;
    for(unsigned n=0;n<1600;n++) {
        unsigned x=(n*71u)%321u,y=(n*43u)%241u;
        uint32_t color=0xff000000u|n;
        assert(MisterGpu_addFill((int16_t)x,(int16_t)y,1,1,color,0)); expect[y*321+x]=color;
    }
    assertPixels(ah,a,expect,321,241); assert(submissions>before+2);

    // Fractional vertical gradients keep their original row origin at y=240.
    assert(MisterGpu_addFillVGradient(0,0,321,241,0xff102030,13,17,19,0,0));
    for(unsigned y=0;y<241;y++)for(unsigned x=0;x<321;x++)
        expect[y*321+x]=tintGradient(0xff102030,13u|(17ull<<16)|(19ull<<32),(int)y);
    assertPixels(ah,a,expect,321,241);

    // Unsupported operations are rejected before any accepted work changes.
    uint64_t unsupported[8]={99};uint32_t queued=g_gpu_surface_batch_count;
    assert(!MisterGpu_surfacePush(unsupported,NULL) && queued==g_gpu_surface_batch_count);
    GpuSurface beforeGradient=*gpuSurfaceRecord(ah);
    unsigned beforeGradientSubmissions=submissions;
    assert(!MisterGpu_addFillVGradient(0,INT16_MIN,1,33009,UINT32_MAX,1,0,0,0,0));
    assert(!MisterGpu_addBlitVGradient(source,stride,0,INT16_MIN,1,33009,
                                      0,0,0,0,UINT32_MAX,1,0,0,0,0));
    uint64_t badGradient[8]={3u|(1ull<<16)|(33009ull<<32),UINT32_MAX,
                            (uint64_t)(uint16_t)INT16_MIN<<16,0,0,0,0,1};
    assert(!MisterGpu_surfacePush(badGradient,NULL));
    assert(queued==g_gpu_surface_batch_count&&submissions==beforeGradientSubmissions);
    assert(!memcmp(&beforeGradient,gpuSurfaceRecord(ah),sizeof(beforeGradient)));
    badGradient[2]=(uint64_t)(uint16_t)(INT16_MIN+240)<<16;
    assert(gpuSurfaceGradientFits(gpuSurfaceRecord(ah),badGradient));
    unsigned allocatedBefore=allocation;fail_allocation=true;
    assert(!MisterGpu_surfaceEnsure(expect,4096,4096,expect,1));
    assert(!MisterGpu_unifiedFailed() && queued==g_gpu_surface_batch_count);
    assert(allocation==allocatedBefore);fail_allocation=false;
    uint32_t old=ah;assert(MisterGpu_surfaceRelease(ah));
    ah=MisterGpu_surfaceEnsure(a,321,241,a,99);assert(ah && ah!=old);
    assert(!MisterGpu_surfaceSelect(old));assert(MisterGpu_surfaceSelect(ah));
    clearFoldRegression();
    tiledClearFoldRegression();
    orderedSurfaceFlushRegression();
    orphanReleaseBarrierRegression();
    residentCopyRegression();
    lazyUniformRegression();
    lazyUniformFailureRegression();
    backgroundExportRegression();
    backgroundSourceRegression();
    backgroundFailureRegression();
    backgroundLifecycleRegression();
    logicalRestoreLifecycleRegression();
    uint32_t* main=patterned(320,240);uint32_t mh=MisterGpu_surfaceEnsure(main,320,240,main,1);assert(mh);
    assert(MisterGpu_surfaceSetPresent(mh)); assert(gpuSurfacePreparePresent());
    assert(gpuSurfacePrepareCheckpoint());assert(gpuSurfaceRestoreCheckpoint());
    // A presentation requires an explicit END, unlike all prior readbacks.
    assert(publications==0);assert(gpuSurfacePreparePresent());
    MisterGpuCommand presentEnd={{0}};assert(gpuSurfaceRaw(&presentEnd));
    assert(submitGpuCommandsAsync());g_gpu_command_count=0;assert(publications==1);
    assert(MisterGpu_surfaceSelect(mh)&&MisterGpu_addClear(0x00123456u));
    assert(MisterGpu_surfaceFlush()&&gpuSurfaceRecord(mh)->uniform_valid);
    assert(MisterGpu_setUnifiedEnabled(false));assert(!gpuSurfaceRecord(ah));
    for(unsigned p=0;p<320u*240u;p++)assert(main[p]==0x00123456u);
    assert(MisterGpu_setUnifiedEnabled(true));
    mh=MisterGpu_surfaceEnsure(main,320,240,main,1);assert(mh);assert(MisterGpu_surfaceSelect(mh));
    assert(MisterGpu_addClear(0xff000000));assert(gpuSurfaceFlushBatch());
    assert(MisterGpu_addFill(0,0,1,1,0xff000000,0));assert(gpuSurfaceFlushBatch());
    uint32_t* failureTarget=patterned(320,240);
    uint32_t failureHandle=MisterGpu_surfaceEnsure(failureTarget,320,240,failureTarget,1);assert(failureHandle);
    GpuSurface beforeFailure=*gpuSurfaceRecord(failureHandle);
    MisterGpuCommand filler={{1u,0xff000000u}};
    while(g_gpu_command_count<GPU_COMMAND_CAPACITY-1u)assert(gpuSurfaceRaw(&filler));
    fail_wait=true;
    assert(!MisterGpu_surfaceCopy(failureHandle,0,0,mh,0,0,320,240)&&MisterGpu_unifiedFailed());
    assert(g_gpu_surface_resident==mh&&g_gpu_surface_resident_dirty);
    assert(!memcmp(&beforeFailure,gpuSurfaceRecord(failureHandle),sizeof(beforeFailure)));
    uint32_t failedStride=0;
    assert(!MisterGpu_surfaceFlush()&&!MisterGpu_surfaceTexture(failureHandle,&failedStride));
    assert(!MisterGpu_setUnifiedEnabled(false)); // Stale CPU pixels cannot rescue a lost GPU job.
    assert(!MisterGpu_surfaceRelease(failureHandle)&&gpuSurfaceRecord(failureHandle));
    printf("Unified surface backend passed: odd strides, full RGBA, overlap snapshots, source mutation, Step queue, bounded batches, gradients, %u immutable generic packet tile transforms, lazy uniform seeds/materialization/versions, lifecycle, checkpoints, clean allocation failure, fatal fences (%u submissions/%u fences/%u waits, %u explicit publication)\n",generic_packets,submissions,fences,waits,publications);
    free(failureTarget);free(main);free(snapshot);free(initial);free(expect);free(a);free(b);free(g_gpu_textures);
    return 0;
}
