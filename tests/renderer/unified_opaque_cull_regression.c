/* Ordinary-RAM adapter around extracted production opacity/coverage/cull code.
 * Pixel equivalence lives in the independent Python descriptor renderer. */
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif
#include "opaque_types.inc"
static MisterGpuCommand commands[GPU_COMMAND_CAPACITY];
static MisterGpuCommand* g_gpu_commands=commands;
static uint32_t g_gpu_command_count,g_gpu_texture_record_count;
static MisterGpuTexture g_gpu_texture_records[1];
static uint64_t g_gpu_opaque_coverage[MISTER_HEIGHT][MISTER_WIDTH/64];
#include "opaque_production.inc"
int main(void) {
#ifdef _WIN32
    _setmode(_fileno(stdin),_O_BINARY);_setmode(_fileno(stdout),_O_BINARY);
#endif
    /* count,start,w,h,flags,generation,warmup,mutationIndex,mutationPixel,
       sourceBytes,recordBytes,reserved */
    uint32_t input[12];
    if(fread(input,sizeof(input),1,stdin)!=1 || input[0]>GPU_COMMAND_CAPACITY ||
       input[9]>4u*1024u*1024u || input[10]>input[9] || input[11]) return 2;
    g_gpu_command_count=input[0];
    if(fread(commands,sizeof(MisterGpuCommand),input[0],stdin)!=input[0])return 3;
    void* pixels=malloc(input[9]?input[9]:1);
    if(!pixels || fread(pixels,1,input[9],stdin)!=input[9])return 4;
    MisterGpuTexture* texture=&g_gpu_texture_records[0];
    texture->physical[0]=0x24000000;texture->physical[1]=0x25000000;
    texture->shadow=pixels;texture->bytes=input[10];texture->shadow_valid=true;texture->generation=1;
    g_gpu_texture_record_count=1;
    if(input[6]&&!prepareTextureOpacity(texture))return 5;
    if(input[7]!=UINT32_MAX) {
        if(input[7]>=input[9]/4)return 6;
        ((uint32_t*)pixels)[input[7]]=input[8];
    }
    texture->dynamic=(input[4]&1)!=0;texture->shadow_valid=(input[4]&2)==0;
    if(input[4]&4)texture->shadow=NULL;
    texture->released=(input[4]&8)!=0;texture->generation=input[5];
    if(input[4]&16)g_gpu_texture_record_count=0;
    uint32_t removed=gpuSurfaceCullOpaquePrefix(input[1],input[2],input[3]);
    uint32_t output[]={removed,g_gpu_command_count};
    if(fwrite(output,sizeof(output),1,stdout)!=1 ||
       fwrite(commands,sizeof(MisterGpuCommand),g_gpu_command_count,stdout)!=g_gpu_command_count)return 7;
    free(texture->opaque_pixels);free(pixels);return 0;
}
