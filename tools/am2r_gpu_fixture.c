/* SPDX-License-Identifier: GPL-2.0-or-later
 * Bounded synthetic GPU QA executor. Never run beside the game or another GPU
 * producer. Default is validation only; --run explicitly permits reserved DDR.
 * No native framebuffer mapping, FPGA programming, or display publication.
 */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifndef _WIN32
#include <fcntl.h>
#include <signal.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
#endif

#define MAGIC 0x54473241u
#define VERSION 1u
#define HEADER 40u
#define MAX_BYTES (32u * 1024u * 1024u)
#define MAX_REGIONS 128u
#define MAX_COMMANDS 1024u
#define MAX_PIXELS (8u * 1024u * 1024u)
#define POOL_FIRST 0x24000000u
#define POOL_END 0x2c000000u
#define CONTROL 0x23ff0000u
#define COMMANDS 0x23fe0000u
#define CONTROL_MAGIC 0x50473241u
#define CAP_MAGIC 0x43473241u
#define FRAME_BYTES (320u * 240u * 4u)

typedef struct { uint32_t address, size; const uint8_t *data; } Span;
typedef struct {
    uint8_t *bytes; size_t size; const uint8_t *commands;
    uint32_t count, region_count, expected_count, capabilities, crc;
    Span regions[MAX_REGIONS], expected[MAX_REGIONS];
} Fixture;

static uint32_t u32(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1]<<8 | (uint32_t)p[2]<<16 | (uint32_t)p[3]<<24;
}
static uint64_t u64(const uint8_t *p) { return u32(p) | (uint64_t)u32(p+4)<<32; }
static uint32_t crc32(const uint8_t *p, size_t size) {
    uint32_t table[256], crc=~0u;
    for (unsigned i=0;i<256;++i) { uint32_t v=i; for(unsigned k=0;k<8;++k) v=(v>>1)^((v&1)?0xedb88320u:0); table[i]=v; }
    for(size_t i=0;i<size;++i) crc=table[(crc^p[i])&255]^(crc>>8);
    return ~crc;
}
static uint32_t fnv(const uint8_t *p,size_t size) {
    uint32_t h=2166136261u; for(size_t i=0;i<size;++i) h=(h^p[i])*16777619u; return h;
}
static int bad(const char *reason) { fprintf(stderr,"REJECT: %s\n",reason); return 0; }
static int pool(uint64_t address,uint64_t size) {
    return size && address>=POOL_FIRST && address<POOL_END && size<=POOL_END-address;
}
static const uint8_t *region(const Fixture *f,uint64_t address,uint64_t size) {
    if(!pool(address,size)) return NULL;
    for(uint32_t i=0;i<f->region_count;++i) {
        const Span *s=&f->regions[i];
        if(address>=s->address && address-s->address<=s->size && size<=s->size-(address-s->address))
            return s->data+(size_t)(address-s->address);
    }
    return NULL;
}
static int cached(const Fixture *f,uint64_t address) {
    return address<=UINT32_MAX && !(address&3) && region(f,address&~127ull,128)!=NULL;
}
static int zeros(const uint64_t *q,unsigned from,unsigned to) {
    for(unsigned i=from;i<to;++i) if(q[i]) return 0; return 1;
}
static int overlap(uint64_t a,uint64_t as,uint64_t b,uint64_t bs) {
    return a<b+bs && b<a+as;
}
/* Packet/table contents must stay immutable throughout the job. Otherwise a
 * previously validated descriptor could read GPU-modified pointer metadata. */
static int immutable(const Fixture *f,uint64_t address,uint64_t size) {
    for(uint32_t i=0;i<f->count;++i) {
        const uint8_t *d=f->commands+64u*i;
        uint64_t w0=u64(d), w1=u64(d+8); unsigned op=w0&255;
        uint64_t base=(uint32_t)w1, stride=w1>>32;
        if(op==6 && overlap(address,size,base,FRAME_BYTES)) return 0;
        if(op==11) {
            unsigned width=(w0>>16)&65535,height=(w0>>32)&65535;
            if(width>320 || height>240) return 0;
            for(unsigned y=0;y<height;++y)
                if(overlap(address,size,base+y*stride,width*4ull)) return 0;
        }
    }
    return 1;
}
static uint64_t magnitude(int64_t v) { return v<0 ? (uint64_t)(-(v+1))+1 : (uint64_t)v; }
static int plane(const uint64_t *q,unsigned n,unsigned width,unsigned height,int bilinear) {
    const uint64_t limit=1000000000ull<<32;
    uint64_t terms[4]={1,width-1,height-1,(uint64_t)(width-1)*(height-1)}, sum=0;
    for(unsigned i=0;i<(bilinear?4u:3u);++i) {
        uint64_t m=magnitude((int64_t)q[n+i]);
        if(m>limit || (terms[i] && m>(limit-sum)/terms[i])) return 0;
        sum+=m*terms[i];
    }
    return 1;
}
static int generic(const Fixture *f,uint64_t base,unsigned width,unsigned height) {
    if((base&7) || !immutable(f,base,512)) return bad("mutable/unaligned generic packet");
    const uint8_t *p=region(f,base,512); if(!p) return bad("generic packet not declared");
    uint64_t q[64]; for(unsigned i=0;i<64;++i) q[i]=u64(p+i*8);
    if((uint32_t)q[0]!=0x31504741u || q[0]>>34 || !zeros(q,4,8) || !zeros(q,39,64))
        return bad("generic magic/reserved fields");
    uint64_t state=q[2]; unsigned tw=state&65535, th=(state>>16)&65535, mode=(state>>32)&255;
    if(mode>6 || state>>59 || ((state>>44)&15) || q[3]>>56) return bad("generic state");
    for(unsigned i=0;i<4;++i) { unsigned factor=(q[3]>>(i*8))&255; if(factor<1 || factor>11) return bad("generic factor"); }
    if(q[0]&(1ull<<32)) {
        uint64_t texture=(uint32_t)q[1], stride=q[1]>>32;
        if(!tw || !th || tw>4096 || th>4096 || (texture&3) || (stride&7) || stride<tw*4u)
            return bad("generic texture shape");
        uint64_t end=texture+(th-1)*stride+tw*4ull;
        if(end<=texture || !region(f,texture&~127ull,((end+127)&~127ull)-(texture&~127ull)))
            return bad("generic texture/cache span");
    }
    for(unsigned i=8;i<23;i+=3) if(!plane(q,i,width,height,0)) return bad("generic Q32 plane overflow");
    for(unsigned i=23;i<39;i+=4) if(!plane(q,i,width,height,1)) return bad("generic Q32 color overflow");
    return 1;
}
static int descriptors(Fixture *f) {
    uint64_t work=0; int initialized=0, affine_pending=0; unsigned stores=0;
    for(uint32_t n=0;n<f->count;++n) {
        uint64_t q[8]; for(unsigned k=0;k<8;++k) q[k]=u64(f->commands+64u*n+8u*k);
        unsigned op=q[0]&255, flags=(q[0]>>8)&255;
        unsigned w=(q[0]>>16)&65535,h=(q[0]>>32)&65535;
        uint64_t base=(uint32_t)q[1],stride=q[1]>>32;
        int x=(int16_t)q[2],y=(int16_t)(q[2]>>16);
        if(q[0]>>48) return bad("descriptor high reserved bits");
        /* Opcodes5 and4 are separate complete64-byte descriptors, not an
         * opaque extension pair. This synthetic subset requires adjacency so
         * no one-shot state can leak across other commands or completion. */
        if(affine_pending && op!=4) return bad("affine setup must immediately precede draw");
        if(op==12) {
            if(n+1!=f->count || q[0]!=12 || !zeros(q,1,8) || !initialized || !stores) return bad("completion placement/initialization");
            continue;
        }
        if(n+1==f->count) return bad("missing END_NO_PRESENT");
        if(op==1) {
            if(q[0]!=1 || q[1]>>32 || !zeros(q,2,8)) return bad("clear descriptor");
            initialized=1; work+=320u*240u; continue;
        }
        if(op==14) {
            if(flags || q[1]>>32 || q[2]>>32 || !zeros(q,3,8)) return bad("replace descriptor");
            if(!w || !h) continue;
            if(x<0 || y<0 || x+w>320 || y+h>240) return bad("replace bounds");
            if(!x && !y && w==320 && h==240) initialized=1;
            if(!initialized) return bad("partial initial framebuffer");
            work+=(uint64_t)w*h; continue;
        }
        if(op==6) {
            if(q[0]!=6 || q[1]>>32 || !zeros(q,2,8) || (base&7) || !initialized || !region(f,base,FRAME_BYTES))
                return bad("raw export descriptor/span");
            ++stores; continue;
        }
        if(op==10 || op==11) {
            if(flags || q[2]>>32 || !zeros(q,3,8)) return bad("transfer reserved fields");
            if(!w || !h) continue;
            if((base&3) || (stride&7) || stride<w*4u || x<0 || y<0 || x+w>320 || y+h>240)
                return bad("transfer shape");
            for(unsigned row=0;row<h;++row) {
                uint64_t address=base+row*stride, end=address+w*4ull;
                if(!region(f,address&~7ull,((end+7)&~7ull)-(address&~7ull))) return bad("transfer DDR span");
            }
            if(op==10 && !x && !y && w==320 && h==240) initialized=1;
            if(!initialized) return bad("undefined transfer BRAM");
            if(op==11) ++stores;
            work+=(uint64_t)w*h; continue;
        }
        if(!initialized) return bad("draw before initialized framebuffer");
        if(op==5) {
            if(w || h || flags>2 || q[1]>>32 || !zeros(q,2,8)) return bad("affine setup descriptor");
            affine_pending=1; continue;
        } else if(op==4) {
            if(flags || !w || !h || q[2]>>32 || (base&3) || (stride&3))
                return bad("affine descriptor flags/shape");
            int32_t umin=(int32_t)q[3], umax=(int32_t)(q[3]>>32);
            int32_t vmin=(int32_t)q[4], vmax=(int32_t)(q[4]>>32);
            /* Current legacy RTL's unsigned address-expression context does
             * not sign-extend negative U. Reject that unsupported synthetic
             * domain rather than validate a different address than it fetches.
             * Nonnegative U bounds also match ordinary texture coordinates. */
            if(umin<0 || umin>=umax || vmin>=vmax) return bad("affine source bounds");
            affine_pending=0;
            work+=(uint64_t)w*h;
            if(work>MAX_PIXELS) return bad("pixel work limit");
            unsigned left=x<0?(unsigned)-x:0,top=y<0?(unsigned)-y:0;
            unsigned right=x>=320?0:(w<(unsigned)(320-x)?w:(unsigned)(320-x));
            unsigned bottom=y>=240?0:(h<(unsigned)(240-y)?h:(unsigned)(240-y));
            for(unsigned row=top;row<bottom;++row) for(unsigned col=left;col<right;++col) {
                /* The accumulator wraps at32 bits before signed bounds. The
                 * RTL truncates V's integer part to an unsigned16-bit row;
                 * accepted U stays nonnegative under the restriction above.
                 * Do not widen away the wrap or validate only the unwrapped
                 * bounding corners. */
                int32_t u=(int32_t)((uint32_t)q[5]+row*(uint32_t)q[7]+col*(uint32_t)q[6]);
                int32_t v=(int32_t)((uint32_t)(q[5]>>32)+row*(uint32_t)(q[7]>>32)+col*(uint32_t)(q[6]>>32));
                if(u<umin || u>=umax || v<vmin || v>=vmax) continue;
                int64_t sx=u>=0?u/65536:-((-(int64_t)u+65535)/65536);
                uint32_t sy=(uint32_t)v>>16;
                uint64_t address=(uint32_t)(base+sy*stride+sx*4);
                if(!cached(f,address)) return bad("affine source/cache span");
            }
        } else if(op==13) {
            if(flags || q[1]>>32 || q[2]>>32 || !zeros(q,3,8) || !w || !h || x<0 || y<0 || x+w>320 || y+h>240)
                return bad("generic descriptor bounds");
            if(!generic(f,base,w,h)) return 0;
            work+=(uint64_t)w*h;
        } else if(op==2 || op==3) {
            if(!w || !h || q[2]>>32 || q[3] || (flags & ~(op==2?7u:3u)) || (flags&3)==3)
                return bad("axis descriptor flags/bounds");
            if(op==2 && q[6]>>32) return bad("axis tint reserved fields");
            if(op==3 && (q[1]>>32 || q[4] || q[5] || q[6])) return bad("fill descriptor");
            // Legacy RTL walks declared pixels even when clipped away. Charge
            // all work before checking visible fetches, not just visible area.
            work+=(uint64_t)w*h;
            if(work>MAX_PIXELS) return bad("pixel work limit");
            unsigned left=x<0?(unsigned)-x:0,top=y<0?(unsigned)-y:0;
            unsigned right=x>=320?0:(w<(unsigned)(320-x)?w:(unsigned)(320-x));
            unsigned bottom=y>=240?0:(h<(unsigned)(240-y)?h:(unsigned)(240-y));
            for(unsigned row=top;row<bottom;++row) for(unsigned col=left;col<right;++col) {
                if(op==2) {
                    int32_t u=(int32_t)((uint32_t)q[4]+col*(uint32_t)q[5]);
                    uint32_t v=(uint32_t)(q[4]>>32)+row*(uint32_t)(q[5]>>32);
                    int64_t sx=u>=0?u/65536:-((-(int64_t)u+65535)/65536);
                    uint64_t address=(uint32_t)(base+(v>>16)*stride+sx*4);
                    if(!cached(f,address)) return bad("axis source/cache span");
                }
            }
        } else if(op==7) {
            if(flags || h || !w || w>240 || !zeros(q,3,6) || q[6]>>32 || q[7]) return bad("water descriptor");
            uint64_t table=(uint32_t)q[2]; unsigned dy=(q[2]>>32)&65535;
            if(q[2]>>48 || dy+w>240 || (base&7) || (table&7) || !immutable(f,table,w*8u)) return bad("water table shape");
            const uint8_t *p=region(f,table,w*8u); if(!p) return bad("water table span");
            for(unsigned row=0;row<w;++row) {
                unsigned dx=p[8*row]|p[8*row+1]<<8, count=p[8*row+2]|p[8*row+3]<<8;
                unsigned sx=p[8*row+4]|p[8*row+5]<<8,sy=p[8*row+6]|p[8*row+7]<<8;
                if(dx+count>320 || sx+count>320 || !region(f,base+sy*1280ull,1280)) return bad("water row span");
            }
            work+=w*320u;
        } else if(op==9) {
            if(flags || !w || !h || x<0 || y<0 || x+w>320 || y+h>240 || q[2]>>32 || q[3]>31 || q[6]>>32 || q[7])
                return bad("tiled descriptor");
            int32_t u=(int32_t)q[4]; int64_t sx=u>=0?u/65536:-((-(int64_t)u+65535)/65536);
            for(unsigned row=0;row<h;++row) {
                uint32_t v=(uint32_t)(q[4]>>32)+row*(uint32_t)(q[5]>>32);
                uint64_t address=(uint32_t)(base+(v>>16)*stride+sx*4);
                if((address&7) || !region(f,address,128)) return bad("tiled source span");
            }
            work+=(uint64_t)w*h;
        } else return bad("opcode outside fixed synthetic whitelist");
        if(work>MAX_PIXELS) return bad("pixel work limit");
    }
    return work<=MAX_PIXELS;
}

static int load_fixture(const char *path,Fixture *f) {
    memset(f,0,sizeof(*f)); FILE *in=fopen(path,"rb"); if(!in) return bad("cannot open fixture");
    if(fseek(in,0,SEEK_END)) { fclose(in); return bad("fixture seek"); }
    long size=ftell(in); if(size<(long)HEADER || size>(long)MAX_BYTES || fseek(in,0,SEEK_SET)) { fclose(in); return bad("fixture size"); }
    f->size=(size_t)size; f->bytes=malloc(f->size); if(!f->bytes) { fclose(in); return bad("allocation"); }
    if(fread(f->bytes,1,f->size,in)!=f->size) { fclose(in); return bad("fixture short read"); } fclose(in);
    const uint8_t *b=f->bytes;
    if(u32(b)!=MAGIC || u32(b+4)!=VERSION || u32(b+8)!=f->size || u32(b+32) || u32(b+36)) return bad("fixture header");
    f->count=u32(b+12); f->region_count=u32(b+16); f->expected_count=u32(b+20); f->capabilities=u32(b+24); f->crc=u32(b+28);
    if(!f->count || f->count>MAX_COMMANDS || !f->region_count || f->region_count>MAX_REGIONS || !f->expected_count || f->expected_count>MAX_REGIONS || f->capabilities!=31)
        return bad("fixture counts/capabilities");
    if(crc32(b+HEADER,f->size-HEADER)!=f->crc) return bad("fixture CRC32");
    size_t offset=HEADER+64u*f->count; if(offset>f->size) return bad("truncated commands"); f->commands=b+HEADER;
    for(unsigned group=0;group<2;++group) {
        Span *s=group?f->expected:f->regions; unsigned count=group?f->expected_count:f->region_count;
        for(unsigned i=0;i<count;++i) {
            if(f->size-offset<8) return bad("truncated span header");
            s[i].address=u32(b+offset); s[i].size=u32(b+offset+4); offset+=8;
            if(!pool(s[i].address,s[i].size) || s[i].size>f->size-offset || (s[i].address&3) || (s[i].size&3)) return bad("invalid reserved DDR span");
            if(!group && ((s[i].address&127) || (s[i].size&127))) return bad("input cacheline alignment");
            if(i && (uint64_t)s[i-1].address+s[i-1].size>s[i].address) return bad("overlapping/unsorted spans");
            s[i].data=b+offset; offset+=s[i].size;
            if(group && !region(f,s[i].address,s[i].size)) return bad("expected output not allocated");
        }
    }
    if(offset!=f->size) return bad("trailing fixture data");
    return descriptors(f);
}

#ifndef _WIN32
static volatile sig_atomic_t stopping;
static void stop_signal(int n) { stopping=n; }
static uint64_t now_ms(void) {
    struct timespec ts; if(clock_gettime(CLOCK_MONOTONIC,&ts)) return 0;
    return (uint64_t)ts.tv_sec*1000u+ts.tv_nsec/1000000u;
}
static void pause_ms(void) { struct timespec delay={0,1000000}; nanosleep(&delay,NULL); }
static int idle(volatile uint32_t *c) {
    __sync_synchronize(); uint32_t first=c[1],done=c[6]; __sync_synchronize(); return first==c[1] && first==done;
}
static int disarm(volatile uint32_t *c) {
    if(!idle(c)) return 0; c[0]=0; __sync_synchronize(); return c[0]==0 && idle(c);
}
typedef struct { void *mapping; size_t length; uint8_t *data; } Mapping;
static int map_span(int fd,uint32_t address,uint32_t size,Mapping *m) {
    uint32_t page=address&~4095u; size_t delta=address-page, length=(delta+size+4095u)&~4095u;
    m->mapping=mmap(NULL,length,PROT_READ|PROT_WRITE,MAP_SHARED,fd,page); m->length=length;
    if(m->mapping==MAP_FAILED) { m->mapping=NULL; return 0; } m->data=(uint8_t*)m->mapping+delta; return 1;
}
static int submit(volatile uint32_t *c,uint32_t count,uint32_t *sequence,uint32_t *cycles) {
    uint64_t started=now_ms();
    if(!started || stopping || !idle(c) || !disarm(c)) return bad("cannot acquire idle mailbox");
    uint32_t next=c[6]+1u; if(!next) next=1;
    c[2]=COMMANDS; c[3]=count; c[4]=0; c[5]=0; c[6]=0; c[7]=0; c[1]=next;
    __sync_synchronize(); c[0]=CONTROL_MAGIC; __sync_synchronize();
    while(c[6]!=next) {
        __sync_synchronize(); uint64_t now=now_ms();
        if(!now || now-started>=5000) {
            fprintf(stderr,"FAIL: timeout submitted=%u completed=%u; busy mailbox NOT disarmed, do not reset blindly\n",next,c[6]); return 0;
        }
        pause_ms();
    }
    __sync_synchronize(); *sequence=next; *cycles=c[7]&0x1fffffffu;
    if(!disarm(c)) return bad("cannot disarm completed mailbox");
    return 1;
}
static int run_fixture(const Fixture *f,const char *path) {
    int result=1,fd=-1; Mapping control={0},command={0},maps[MAX_REGIONS]={{0}};
    fd=open("/dev/mem",O_RDWR|O_SYNC|O_CLOEXEC); if(fd<0) { perror("/dev/mem"); return 1; }
    if(!map_span(fd,CONTROL,4096,&control)) goto cleanup;
    volatile uint32_t *c=(volatile uint32_t*)control.data;
    /* A stopped owner and valid completed sequence are required before any
     * command/texture overwrite. Equality is not a native-scanout drain claim. */
    if(!idle(c) || !disarm(c)) { fprintf(stderr,"FAIL: mailbox busy before fixture\n"); goto cleanup; }
    if(!map_span(fd,COMMANDS,65536,&command)) goto cleanup;
    // Establish capabilities from a newly completed bounded clear/fence job,
    // never retained DDR bits from a previous RBF. The probe performs no DMA.
    uint64_t probe[16]={1,0,0,0,0,0,0,0,12,0,0,0,0,0,0,0};
    memcpy(command.data,probe,sizeof(probe)); c[20]=0; c[21]=0; __sync_synchronize();
    uint32_t sequence=0,cycles=0;
    if(!submit(c,2,&sequence,&cycles)) goto cleanup;
    if(c[20]!=CAP_MAGIC || (c[21]&f->capabilities)!=f->capabilities) {
        fprintf(stderr,"FAIL: required capabilities31 absent (%08x/%08x)\n",c[20],c[21]); goto cleanup;
    }
    for(unsigned i=0;i<f->region_count;++i) {
        if(!map_span(fd,f->regions[i].address,f->regions[i].size,&maps[i])) goto cleanup;
        memcpy(maps[i].data,f->regions[i].data,f->regions[i].size);
    }
    memcpy(command.data,f->commands,f->count*64u); __sync_synchronize();
    if(stopping) { fprintf(stderr,"FAIL: interrupted before submission\n"); goto cleanup; }
    if(!submit(c,f->count,&sequence,&cycles)) goto cleanup;
    uint64_t compared=0,mismatches=0; uint32_t aggregate=2166136261u;
    for(unsigned i=0;i<f->expected_count;++i) {
        const Span *s=&f->expected[i]; const uint8_t *actual=NULL;
        for(unsigned j=0;j<f->region_count;++j) if(s->address>=f->regions[j].address &&
            (uint64_t)s->address+s->size<=(uint64_t)f->regions[j].address+f->regions[j].size)
            actual=maps[j].data+(s->address-f->regions[j].address);
        if(!actual) goto cleanup;
        for(uint32_t k=0;k<s->size;++k) {
            aggregate=(aggregate^actual[k])*16777619u;
            if(actual[k]!=s->data[k]) { if(mismatches<8) fprintf(stderr,"mismatch %08x actual=%02x expected=%02x\n",s->address+k,actual[k],s->data[k]); ++mismatches; }
        }
        compared+=s->size;
        printf("span=%08x bytes=%u actual_fnv1a=%08x expected_fnv1a=%08x\n",s->address,s->size,fnv(actual,s->size),fnv(s->data,s->size));
    }
    printf("%s fixture=%s commands=%u spans=%u bytes=%"PRIu64" mismatches=%"PRIu64" sequence=%u cycles=%u bundle_crc32=%08x actual_fnv1a=%08x disarmed=1\n",
           mismatches?"FAIL":"PASS",path,f->count,f->expected_count,compared,mismatches,sequence,cycles,f->crc,aggregate);
    result=mismatches||stopping?1:0;
cleanup:
    for(unsigned i=0;i<MAX_REGIONS;++i) if(maps[i].mapping) munmap(maps[i].mapping,maps[i].length);
    if(command.mapping) munmap(command.mapping,command.length);
    if(control.mapping) munmap(control.mapping,control.length);
    close(fd); return result;
}
#endif

int main(int argc,char **argv) {
    int run=argc>1 && !strcmp(argv[1],"--run"), start=2;
    if(argc<3 || (!run && strcmp(argv[1],"--validate"))) { fprintf(stderr,"usage: %s --validate|--run fixture.a2gt [...]\n",argv[0]); return 2; }
#ifdef _WIN32
    if(run) return !bad("hardware execution requires Linux ARM QA process");
#else
    struct sigaction action; memset(&action,0,sizeof(action)); action.sa_handler=stop_signal; sigemptyset(&action.sa_mask);
    if(sigaction(SIGTERM,&action,NULL) || sigaction(SIGINT,&action,NULL) || sigaction(SIGHUP,&action,NULL)) return 1;
#endif
    for(int i=start;i<argc;++i) {
        Fixture f; if(!load_fixture(argv[i],&f)) { free(f.bytes); return 1; }
        printf("VALID fixture=%s commands=%u regions=%u expected=%u crc32=%08x bundle_fnv1a=%08x\n",argv[i],f.count,f.region_count,f.expected_count,f.crc,fnv(f.bytes,f.size));
#ifndef _WIN32
        if(run && run_fixture(&f,argv[i])) { free(f.bytes); return 1; }
        if(stopping) { free(f.bytes); return 1; }
#endif
        free(f.bytes);
    }
    return 0;
}
