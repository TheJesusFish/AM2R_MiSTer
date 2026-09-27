// SPDX-License-Identifier: GPL-3.0-or-later
// Executes the real backend capture implementation with an in-memory POSIX
// file/device boundary. No GPU, /dev/mem, real game pixels, or device I/O.
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

typedef long long ssize_t;
#define MISTER_FB_BYTES ((size_t)320 * 240 * 4)
#define GPU_TEXTURE_PHYS 0x24000000u
#define GPU_TEXTURE_BYTES (128u * 1024u * 1024u)
#define GPU_TEXTURE_RECORDS 128u
#define GPU_NATIVE_WINDOW_PHYS 0x3a000000u
#define GPU_NATIVE_BUFFER_OFFSET 0x100u
#define GPU_NATIVE_BUFFER_BYTES MISTER_FB_BYTES
#define GPU_NATIVE_BUFFER_COUNT 3u
#define GPU_COMMAND_BYTES 65536u
#define GPU_COMMAND_CAPACITY 1024u
#define GPU_COMMAND_BUFFER_COUNT 2u
#define GPU_COMMAND_PHYS 0x23fe0000u
#define GPU_COMMAND2_PHYS 0x23fd0000u
#define MISTER_RENDER_DIAGNOSTICS 1
#define GPU_FRAMEBUFFER_PHYS 0x22001000u
#define GPU_CONTROL_MAGIC 0x50473241u
#define O_WRONLY 1
#define O_RDONLY 2
#define O_CREAT 4
#define O_EXCL 8
#define O_NOFOLLOW 16
#define O_CLOEXEC 32
#define O_DIRECTORY 64
#define O_NONBLOCK 128
#define S_ISREG(mode) ((mode) == 7)
#define RD_SCOPE(a, b, c) ((void)0)
#define RD_CAPTURE 0
#define RD_capturePerturbation() (perturbations++)
#define __sync_synchronize() ((void)0)
#define logWarn(...) ((void)0)
#define logInfo(...) ((void)0)
struct stat { int st_mode; size_t st_size; };
struct statvfs { uint64_t f_bavail; uint64_t f_frsize; };
typedef struct { uint64_t word[8]; } MisterGpuCommand;
static MisterGpuCommand commands[8];
static MisterGpuCommand alternateCommands[8];
static MisterGpuCommand* g_gpu_command_buffers[2]={commands,alternateCommands};
static MisterGpuCommand* g_gpu_commands = commands;
static uint32_t g_gpu_sequence,g_gpu_pending_sequence,g_gpu_write_buffer;
static bool g_gpu_pending_presents;
static uint8_t textures[4u * 1024u * 1024u];
static uint8_t* g_gpu_textures = textures;
static uint32_t native[3][320 * 240];
static uint32_t* g_gpu_native_buffers[3] = {native[0], native[1], native[2]};
static uint32_t control[32];
static uint32_t* g_gpu_control = control;
static struct { int frameCount, currentRoomIndex; void* renderer; } runner = {42, 160, NULL};
static bool SWRenderer_misterOffscreenReference(void* renderer, uint32_t address, uint8_t* crop) {
    (void)renderer; (void)address; (void)crop;
    return false;
}
static typeof(runner)* g_runner = &runner;
static uint32_t g_gpu_command_count, g_gpu_frame_serial;
static size_t g_gpu_texture_offset;
static uint8_t g_gpu_last_presented_buffer;
static uint32_t g_gpu_last_completed_cycles;
static bool g_gpu_pending, g_gpu_available, g_gpu_last_presented_valid;
static bool g_gpu_scanout_underflow;
static bool g_gpu_unified_enabled, g_gpu_surface_targets;
static uint32_t bram[320u*240u];
static unsigned observerFences;
static bool observerTimeout,observerTimeoutAfterFirst;
static uint32_t reserveGpuTexture(size_t bytes) {
    size_t first=(g_gpu_texture_offset+127u)&~(size_t)127u;
    size_t size=(bytes+127u)&~(size_t)127u;
    if(first>sizeof(textures)||size>sizeof(textures)-first)return 0;
    g_gpu_texture_offset=first+size;
    return GPU_TEXTURE_PHYS+(uint32_t)first;
}
static int fake_usleep(unsigned usec) {
    assert(usec==100);
    if(observerTimeout||(observerTimeoutAfterFirst&&observerFences))return 0;
    assert(control[0]==GPU_CONTROL_MAGIC&&control[3]==2);
    assert(control[2]>=GPU_TEXTURE_PHYS);
    MisterGpuCommand* probe=(MisterGpuCommand*)(textures+control[2]-GPU_TEXTURE_PHYS);
    assert(probe[0].word[0]==(11u|(320ull<<16)|(240ull<<32))&&probe[1].word[0]==12);
    uint32_t destination=(uint32_t)probe[0].word[1];
    assert(destination>=GPU_TEXTURE_PHYS&&destination-GPU_TEXTURE_PHYS+MISTER_FB_BYTES<=sizeof(textures));
    memcpy(textures+destination-GPU_TEXTURE_PHYS,bram,MISTER_FB_BYTES);
    control[6]=control[1];observerFences++;
    return 0;
}
static bool waitGpuCompletion(uint32_t* cycles) {
    if(cycles)*cycles=0;
    assert(g_gpu_pending);control[6]=g_gpu_pending_sequence;g_gpu_pending=false;
    return true;
}

typedef struct { char name[80]; uint8_t* data; size_t size; bool closed; } File;
static File files[128];
static int fileCount, perturbations, opens, writes, failWrite;
static bool enabled, requestPresent, enoughSpace, directoryExists;
static bool badRequest;
static const char* minimumCommands;
static FILE* manifestStream;
static int manifestFd;
static const char* fake_getenv(const char* key) {
    if (strcmp(key, "AM2R_GPU_CAPTURE_MIN_COMMANDS") == 0) return minimumCommands;
    assert(strcmp(key, "AM2R_GPU_CAPTURE_DIR") == 0);
    return enabled ? "/qa" : NULL;
}
static int fake_open(const char* path, int flags) {
    assert(strcmp(path, "/qa") == 0);
    assert(flags & O_DIRECTORY);
    opens++;
    return 1;
}
static int fake_openat(int dir, const char* name, int flags, ...) {
    assert(dir >= 1);
    opens++;
    if (strcmp(name, "capture.request") == 0) return requestPresent ? 2 : -1;
    if (flags & O_DIRECTORY) return 3;
    assert(flags & O_EXCL);
    for (int i = 0; i < fileCount; i++) assert(strcmp(files[i].name, name) != 0);
    assert(fileCount < 128);
    snprintf(files[fileCount].name, sizeof(files[fileCount].name), "%s", name);
    return 10 + fileCount++;
}
static int fake_fstat(int fd, struct stat* info) {
    assert(fd == 2);
    info->st_mode = badRequest ? 0 : 7;
    info->st_size = 0;
    return 0;
}
static int fake_unlinkat(int dir, const char* name, int flags) {
    (void)dir; (void)flags;
    if (strcmp(name, "capture.request") == 0) requestPresent = false;
    return 0;
}
static int fake_close(int fd) {
    if (fd >= 10) files[fd - 10].closed = true;
    return 0;
}
static ssize_t fake_write(int fd, const void* data, size_t size) {
    writes++;
    if (failWrite && writes >= failWrite) { errno = ENOSPC; return -1; }
    File* file = &files[fd - 10];
    // Force short writes to exercise the real implementation's retry loop.
    if (size > 701) size = 701;
    file->data = realloc(file->data, file->size + size);
    assert(file->data != NULL);
    memcpy(file->data + file->size, data, size);
    file->size += size;
    return (ssize_t)size;
}
static int fake_fsync(int fd) { (void)fd; return 0; }
static int fake_fstatvfs(int fd, struct statvfs* info) {
    assert(fd == 1);
    info->f_bavail = enoughSpace ? 1024u * 1024u : 0;
    info->f_frsize = 4096;
    return 0;
}
static int fake_getpid(void) { return 99; }
static int fake_mkdirat(int fd, const char* name, int mode) {
    assert(fd == 1 && strncmp(name, "job-99-", 7) == 0 && mode == 0700);
    return directoryExists ? -1 : 0;
}
static FILE* fake_fdopen(int fd, const char* mode) {
    assert(strcmp(mode, "w") == 0);
    manifestFd = fd;
    manifestStream = tmpfile();
    assert(manifestStream != NULL);
    return manifestStream;
}
static int fake_fclose(FILE* file) {
    assert(file == manifestStream);
    long size = ftell(file);
    assert(size > 0);
    rewind(file);
    File* output = &files[manifestFd - 10];
    output->data = calloc((size_t)size + 1, 1);
    output->size = (size_t)size;
    assert(fread(output->data, 1, output->size, file) == output->size);
    output->closed = true;
    return fclose(file);
}
static int fake_renameat(int fromDir, const char* from, int toDir, const char* to) {
    assert(fromDir == 3 && toDir == 3);
    assert(strcmp(from, "manifest.json.tmp") == 0);
    assert(strcmp(to, "manifest.json") == 0);
    File* output = &files[manifestFd - 10];
    assert(output->closed);
    assert(strcmp(output->name, from) == 0);
    snprintf(output->name, sizeof(output->name), "%s", to);
    return 0;
}
#define getenv fake_getenv
#define open fake_open
#define openat fake_openat
#define fstat fake_fstat
#define unlinkat fake_unlinkat
#define close fake_close
#define write fake_write
#define fsync fake_fsync
#define fstatvfs fake_fstatvfs
#define getpid fake_getpid
#define mkdirat fake_mkdirat
#define fdopen fake_fdopen
#define fclose fake_fclose
#define renameat fake_renameat
#define usleep fake_usleep
#include "gpu_capture_under_test.inc"
#undef fclose

static File* findFile(const char* name) {
    for (int i = 0; i < fileCount; ++i)
        if (strcmp(files[i].name, name) == 0) return &files[i];
    return NULL;
}
static void reset(void) {
    for (int i = 0; i < fileCount; ++i) free(files[i].data);
    memset(files, 0, sizeof(files));
    memset(commands, 0, sizeof(commands));
    memset(alternateCommands,0,sizeof(alternateCommands));
    g_gpu_commands=commands;g_gpu_sequence=g_gpu_pending_sequence=g_gpu_write_buffer=0;
    memset(textures, 11, sizeof(textures));
    memset(native, 22, sizeof(native));
    memset(control, 0, sizeof(control));
    memset(&g_gpu_capture, 0, sizeof(g_gpu_capture));
    g_gpu_capture.directory = -1;
    g_gpu_capture_root = -1;
    g_gpu_capture_initialized = false;
    g_gpu_capture_attempts = 0;
    g_gpu_capture_min_commands = 0;
    minimumCommands = NULL;
    fileCount = perturbations = opens = writes = failWrite = 0;
    enoughSpace = enabled = requestPresent = true;
    badRequest = directoryExists = false;
    g_gpu_command_count = 3;
    g_gpu_texture_offset = 2u * MISTER_FB_BYTES;
    g_gpu_frame_serial = 123;
    g_gpu_pending = false;
    g_gpu_available = g_gpu_last_presented_valid = true;
    g_gpu_last_presented_buffer = 1;
    g_gpu_last_completed_cycles = 700000;
    g_gpu_scanout_underflow = false;
    g_gpu_unified_enabled=false;g_gpu_surface_targets=true;
    observerFences=0;observerTimeout=observerTimeoutAfterFirst=false;
    g_capture_probe_commands=g_capture_probe_rgba=0;
    for(unsigned i=0;i<320u*240u;i++)bram[i]=0x00563412u+i;
    commands[0].word[0] = 1;
    commands[1].word[0] = 6;
    commands[1].word[1] = GPU_TEXTURE_PHYS;
}
static void reject(void) {
    assert(!gpuCapturePrepare(19, 0x23fe0000));
    assert(findFile("manifest.json") == NULL);
    assert(g_gpu_capture.directory == -1);
}
static void exportFixture(const char* directory) {
    if (!directory) return;
    for (int i=0;i<fileCount;i++) {
        char path[4096];
        int count=snprintf(path,sizeof(path),"%s/%s",directory,files[i].name);
        assert(count>0&&(size_t)count<sizeof(path));
        FILE* output=fopen(path,"wb");assert(output);
        assert(fwrite(files[i].data,1,files[i].size,output)==files[i].size);
        assert(fclose(output)==0);
    }
}
static void knownSemanticFixture(const char* directory) {
    reset();memset(commands,0,sizeof(commands));
    g_gpu_command_count=5;g_gpu_unified_enabled=true;
    const uint32_t source=GPU_TEXTURE_PHYS+0x10000u;
    const uint32_t destination=GPU_TEXTURE_PHYS+0x20004u;
    const uint32_t packetAddress=GPU_TEXTURE_PHYS+0x30000u;
    const uint32_t pixels[4]={0x00112233u,0x80556677u,0xff99aabbu,0x01ccddefu};
    memcpy(textures+source-GPU_TEXTURE_PHYS,pixels,8);
    memcpy(textures+source-GPU_TEXTURE_PHYS+16,pixels+2,8);
    commands[0].word[0]=10u|(2ull<<16)|(2ull<<32);
    commands[0].word[1]=source|(16ull<<32);commands[0].word[2]=5u|(7ull<<16);
    commands[1].word[0]=13u|(1ull<<16)|(1ull<<32);
    commands[1].word[1]=packetAddress;commands[1].word[2]=6u|(8ull<<16);
    commands[2].word[0]=14u|(1ull<<16)|(1ull<<32);
    commands[2].word[1]=0x00010203u;commands[2].word[2]=5u|(8ull<<16);
    commands[3].word[0]=11u|(2ull<<16)|(2ull<<32);
    commands[3].word[1]=destination|(24ull<<32);commands[3].word[2]=5u|(7ull<<16);
    commands[4].word[0]=12;
    uint64_t* packet=(uint64_t*)(textures+packetAddress-GPU_TEXTURE_PHYS);
    memset(packet,0,512);packet[0]=0x31504741u;packet[2]=15ull<<40;
    packet[23]=1ull<<32;packet[27]=1ull<<31;packet[35]=1ull<<31;
    assert(gpuCapturePrepare(33,GPU_COMMAND_PHYS));
    // Independent, deliberately narrow execution of the five descriptors:
    // load preserves RGBA including zero alpha; generic is a constant solid
    // replacement (R=1,G=.5,B=0,A=.5, no blending); store has row padding.
    // This is a transport/observer integration fixture, not a general raster
    // model or proof of arbitrary blends, UV interpolation, or triangles.
    for(unsigned y=0;y<2;y++)
        memcpy(bram+(7+y)*320u+5,textures+source-GPU_TEXTURE_PHYS+y*16u,8);
    bram[8u*320u+6]=0x7f007fffu;
    bram[8u*320u+5]=0x00010203u; // Bounded replacement preserves zero alpha.
    for(unsigned y=0;y<2;y++)
        memcpy(textures+destination-GPU_TEXTURE_PHYS+y*24u,bram+(7+y)*320u+5,8);
    control[6]=33;gpuCaptureComplete();
    assert(observerFences==2&&findFile("manifest.json"));
    exportFixture(directory);
}
int main(int argc,char** argv) {
    assert(argc<=2);
    reset();
    enabled = false;
    reject();
    assert(opens == 0 && perturbations == 0);
    reset(); minimumCommands = "4"; reject();
    assert(requestPresent && perturbations == 0 && g_gpu_capture_attempts == 0);
    g_gpu_command_count = 4; commands[2].word[0] = 1;
    assert(gpuCapturePrepare(19, 0x23fe0000));
    assert(!requestPresent && perturbations == 1);
    reset(); minimumCommands = "invalid";
    assert(gpuCapturePrepare(19, 0x23fe0000));
    reset(); minimumCommands = "999999999999999999999999";
    assert(gpuCapturePrepare(19, 0x23fe0000));
    reset(); requestPresent = false; reject(); assert(perturbations == 0);
    reset(); badRequest = true; reject(); assert(requestPresent);
    reset(); g_gpu_pending = true; reject(); assert(!requestPresent);
    reset(); commands[0].word[0] = 6; reject();
    reset(); commands[1].word[0] = 99; reject();
    reset(); commands[2].word[0] = 2; reject();
    reset(); commands[1].word[0] = 0; reject();
    reset(); commands[1].word[0] = 8; g_gpu_last_presented_valid = false; reject();
    reset(); commands[1].word[1] += 4; reject();
    reset(); commands[1].word[1] += sizeof(textures); reject();
    reset(); enoughSpace = false; reject(); assert(fileCount == 0);
    reset(); directoryExists = true; reject(); assert(fileCount == 0);
    reset(); failWrite = 3; reject(); assert(findFile("commands.bin") != NULL);
    reset();
    assert(gpuCapturePrepare(19, 0x23fe0000));
    assert(perturbations == 1 && !requestPresent);
    assert(findFile("manifest.json") == NULL);
    assert(findFile("textures.bin")->size == 2u*MISTER_FB_BYTES);
    assert(findFile("textures.bin")->data[0] == 11);
    assert(findFile("native-1.bin")->data[0] == 22);
    memset(textures, 33, sizeof(textures));
    memset(native[2], 44, sizeof(native[2]));
    g_gpu_last_presented_buffer = 2;
    control[6] = 19;
    gpuCaptureComplete();
    assert(g_gpu_capture.directory == -1);
    assert(findFile("textures.bin")->data[0] == 11);
    assert(findFile("native-1.bin")->data[0] == 22);
    assert(findFile("expected.bin")->data[0] == 44);
    assert(findFile("export-0.bin")->data[0] == 33);
    assert(strstr((char*)findFile("manifest.json")->data, "\"sequence\":19") != NULL);
    assert(strstr((char*)findFile("manifest.json")->data, "\"room\":160") != NULL);
    assert(strstr((char*)findFile("manifest.json")->data,
                  "\"requires_initialization_proof\":false") != NULL);
    for (int i = 0; i < fileCount; ++i) assert(files[i].closed);
    reset();
    // Prefix culling can replace the leading clear with an opaque blit. The
    // capture must preserve that job verbatim, not inject a clear. The offline
    // reference, not this writer, decides whether its full coverage is proven.
    commands[0].word[0] = 2;
    commands[0].word[1] = GPU_TEXTURE_PHYS;
    assert(gpuCapturePrepare(21, 0x23fe0000));
    assert(findFile("commands.bin")->data[0] == 2);
    control[6] = 21;
    gpuCaptureComplete();
    assert(strstr((char*)findFile("manifest.json")->data,
                  "\"requires_initialization_proof\":true") != NULL);
    reset(); commands[0].word[0] = 5; reject(); // Setup then export still needs prior BRAM.
    reset(); assert(gpuCapturePrepare(19, 0x23fe0000));
    control[6] = 20;
    gpuCaptureComplete();
    assert(findFile("manifest.json") == NULL);
    reset(); g_gpu_capture_attempts = 8; reject(); assert(opens == 1);
    reset();
    // Sparse targets/packets with no native presentation still capture exact
    // raw alpha before and after, plus every written row and source payload.
    memset(commands,0,sizeof(commands));g_gpu_command_count=4;g_gpu_unified_enabled=true;
    g_gpu_last_presented_valid=false;
    const uint32_t source=GPU_TEXTURE_PHYS+0x10000u,destination=GPU_TEXTURE_PHYS+0x20004u;
    const uint32_t packetAddress=GPU_TEXTURE_PHYS+0x30000u;
    commands[0].word[0]=10u|(2ull<<16)|(2ull<<32);commands[0].word[1]=source|(16ull<<32);
    commands[1].word[0]=11u|(2ull<<16)|(2ull<<32);commands[1].word[1]=destination|(24ull<<32);
    commands[2].word[0]=13u|(2ull<<16)|(2ull<<32);commands[2].word[1]=packetAddress;
    commands[3].word[0]=12;
    uint64_t* packet=(uint64_t*)(textures+packetAddress-GPU_TEXTURE_PHYS);memset(packet,0,512);
    packet[0]=0x31504741u|(1ull<<32);packet[1]=source|(16ull<<32);packet[2]=2u|(2ull<<16);
    MisterGpuCommand original[4];memcpy(original,commands,sizeof(original));
    control[6]=17;assert(gpuCapturePrepare(31,0x23fe0000));
    assert(observerFences==1&&control[6]==17&&control[0]==0);
    assert(!memcmp(original,commands,sizeof(original)));
    assert(findFile("textures.bin")==NULL&&findFile("initial.rgba"));
    assert(g_capture_input_bytes<1024&&g_capture_input_count==2&&g_capture_output_count==2);
    assert(findFile("initial.rgba")->size==MISTER_FB_BYTES);
    assert(!memcmp(findFile("initial.rgba")->data,bram,MISTER_FB_BYTES));
    memset(bram,77,sizeof(bram));memset(textures+destination-GPU_TEXTURE_PHYS,66,8);
    memset(textures+destination-GPU_TEXTURE_PHYS+24,88,8);control[6]=31;
    gpuCaptureComplete();assert(observerFences==2&&control[6]==31&&control[0]==0);
    assert(!memcmp(original,commands,sizeof(original)));
    assert(findFile("expected.rgba")->data[0]==77&&findFile("export-0.bin")->size==8);
    assert(findFile("export-0.bin")->data[0]==66&&findFile("export-1.bin")->data[0]==88);
    assert(findFile("native-1.bin")->data[0]==22&&native[1][0]==0x16161616u);
    assert(strstr((char*)findFile("manifest.json")->data,"\"diagnostic_fence_count\":2"));
    assert(strstr((char*)findFile("manifest.json")->data,"\"format\":\"rgba8888\""));
    reset();g_gpu_unified_enabled=true;commands[1].word[0]=13u|(2ull<<16)|(2ull<<32);
    commands[1].word[1]=GPU_TEXTURE_PHYS+g_gpu_texture_offset-128u;reject();assert(observerFences==0);
    reset();g_gpu_unified_enabled=true;commands[2].word[0]=12;observerTimeout=true;
    reject();assert(!g_gpu_available&&findFile("manifest.json")==NULL);
    reset();g_gpu_unified_enabled=true;commands[2].word[0]=12;observerTimeoutAfterFirst=true;
    // Execute the actual submitter: original job completion does not hide a
    // failed final observer fence or publish a successful capture manifest.
    assert(!submitGpuCommandsAsync());assert(observerFences==1&&!g_gpu_available);
    assert(findFile("manifest.json")==NULL);
    reset();g_gpu_unified_enabled=true;commands[2].word[0]=12;g_gpu_surface_targets=false;
    reject();assert(observerFences==0);
    reset();g_gpu_unified_enabled=true;memset(commands,0,sizeof(commands));
    g_gpu_command_count=2;commands[0].word[0]=14u|(1ull<<16)|(1ull<<32)|(1ull<<8);
    commands[1].word[0]=12;reject();assert(observerFences==0);
    reset();
    // Planning is bounded without dereferencing entire advertised textures.
    // A64MiB source plus another disjoint cache line exceeds the capture cap.
    g_gpu_texture_offset=GPU_TEXTURE_BYTES;g_gpu_command_count=3;
    memset(commands,0,sizeof(commands));memset(textures+0x30000,0,512);
    packet=(uint64_t*)(textures+0x30000);
    packet[0]=0x31504741u|(1ull<<32);packet[1]=GPU_TEXTURE_PHYS|(16384ull<<32);
    packet[2]=4096u|(4096ull<<16);
    commands[0].word[0]=13u|(1ull<<16)|(1ull<<32);commands[0].word[1]=GPU_TEXTURE_PHYS+0x30000;
    commands[1].word[0]=10u|(1ull<<16)|(1ull<<32);
    commands[1].word[1]=(GPU_TEXTURE_PHYS+100u*1024u*1024u)|(8ull<<32);commands[2].word[0]=12;
    g_gpu_capture.commandCount=3;assert(!gpuCapturePlanUnified(&g_gpu_capture));
    assert(observerFences==0&&fileCount==0);
    reset();g_gpu_texture_offset=GPU_TEXTURE_BYTES;g_gpu_capture.commandCount=6;
    memset(commands,0,sizeof(commands));
    for(unsigned i=0;i<5;i++) {
        commands[i].word[0]=11u|(1ull<<16)|(240ull<<32);
        commands[i].word[1]=(GPU_TEXTURE_PHYS+i*240u*8192u)|(8192ull<<32);
    }
    commands[5].word[0]=12;assert(!gpuCapturePlanUnified(&g_gpu_capture));
    assert(observerFences==0&&fileCount==0);
    knownSemanticFixture(argc==2?argv[1]:NULL);
    puts("GPU capture: legacy boundaries, sparse targets/generic dependencies, exact raw BRAM observer fences, immutable descriptors/native buffers, partial exports, timeout/rejection and no-overwrite passed");
    return 0;
}
