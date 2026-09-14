// SPDX-License-Identifier: GPL-3.0-or-later
// Read-only diagnostic for the AM2R FPGA GPU mailbox and active command list.

#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>

#define CONTROL_PHYS 0x23ff0000u
#define CONTROL_BYTES 4096u
#define COMMAND_BYTES 65536u
#define GPU_MAGIC 0x50473241u
#define NATIVE_BUF0_PHYS 0x3a000100u
#define NATIVE_BUF1_PHYS 0x3a04b100u
#define NATIVE_BUF2_PHYS 0x3a096100u
#define NATIVE_BUF_BYTES (320u * 240u * 4u)

typedef struct { uint64_t word[8]; } Command;

static void reportAxisAlpha(int memoryFd, uint32_t commandIndex,
                            const volatile Command *command)
{
    uint64_t word0 = command->word[0];
    if ((word0 & 0xffu) != 2u || (word0 & 0x100u) != 0u ||
        (uint32_t)command->word[5] != 0x00010000u ||
        (uint32_t)(command->word[5] >> 32) != 0x00010000u ||
        (uint32_t)command->word[6] != 0xffffffffu)
        return;

    uint32_t width = (uint32_t)((word0 >> 16) & 0xffffu);
    uint32_t height = (uint32_t)((word0 >> 32) & 0xffffu);
    uint32_t physical = (uint32_t)command->word[1];
    uint32_t stride = (uint32_t)(command->word[1] >> 32);
    int32_t sourceX = (int32_t)(uint32_t)command->word[4] >> 16;
    int32_t sourceY = (int32_t)(uint32_t)(command->word[4] >> 32) >> 16;
    if (width == 0 || height == 0 || stride == 0 || sourceX < 0 || sourceY < 0)
        return;

    uint64_t lastOffset = (uint64_t)(sourceY + (int32_t)height - 1) * stride +
                          (uint64_t)(sourceX + (int32_t)width - 1) * 4u;
    uint32_t pageBase = physical & ~4095u;
    size_t pageOffset = physical - pageBase;
    if (lastOffset > SIZE_MAX - pageOffset - sizeof(uint32_t)) return;
    size_t mapBytes = pageOffset + (size_t)lastOffset + sizeof(uint32_t);
    const uint8_t *mapping = mmap(NULL, mapBytes, PROT_READ, MAP_SHARED,
                                  memoryFd, pageBase);
    if (mapping == MAP_FAILED) {
        fprintf(stderr, "mmap source command=%u physical=%08x bytes=%zu: ",
                commandIndex, physical, mapBytes);
        perror("");
        return;
    }

    const uint8_t *source = mapping + pageOffset;
    uint64_t transparent = 0, opaque = 0, partial = 0, cacheLines = 0;
    for (uint32_t y = 0; y < height; ++y) {
        uint64_t rowOffset = (uint64_t)(sourceY + (int32_t)y) * stride +
                             (uint64_t)sourceX * 4u;
        uint64_t firstLine = (physical + rowOffset) >> 5;
        uint64_t lastLine = (physical + rowOffset + (uint64_t)(width - 1) * 4u) >> 5;
        cacheLines += lastLine - firstLine + 1u;
        const uint32_t *pixels = (const uint32_t *)(source + rowOffset);
        for (uint32_t x = 0; x < width; ++x) {
            uint8_t alpha = (uint8_t)(pixels[x] >> 24);
            if (alpha == 0) transparent++;
            else if (alpha == 255) opaque++;
            else partial++;
        }
    }
    printf("alpha command=%u transparent=%" PRIu64 " opaque=%" PRIu64
           " partial=%" PRIu64 " cache32=%" PRIu64 "\n",
           commandIndex, transparent, opaque, partial, cacheLines);
    munmap((void *)mapping, mapBytes);
}

int main(void)
{
    int fd = open("/dev/mem", O_RDONLY | O_SYNC | O_CLOEXEC);
    if (fd < 0) { perror("open /dev/mem"); return 1; }
    volatile uint32_t *control = mmap(NULL, CONTROL_BYTES, PROT_READ, MAP_SHARED, fd, CONTROL_PHYS);
    if (control == MAP_FAILED) { perror("mmap control"); close(fd); return 1; }
    uint32_t magic = control[0], sequence = control[1], commandPhys = control[2];
    uint32_t commandCount = control[3] & 0xffffu;
    uint32_t completed = control[6], cycles = control[7];
    if (magic != GPU_MAGIC || commandCount > COMMAND_BYTES / sizeof(Command)) {
        fprintf(stderr, "invalid mailbox magic=%08x count=%u\n", magic, commandCount);
        munmap((void *)control, CONTROL_BYTES); close(fd); return 2;
    }
    volatile Command *commands = mmap(NULL, COMMAND_BYTES, PROT_READ, MAP_SHARED, fd, commandPhys);
    if (commands == MAP_FAILED) { perror("mmap commands"); munmap((void *)control, CONTROL_BYTES); close(fd); return 1; }

    uint64_t areaByOpcode[7] = {0};
    uint64_t whiteArea = 0, alphaOnlyArea = 0, otherTintArea = 0, gradientArea = 0;
    uint32_t countByOpcode[7] = {0};
    printf("mailbox sequence=%u completed=%u prior_cycles=%u command_phys=%08x commands=%u\n",
           sequence, completed, cycles, commandPhys, commandCount);
    for (uint32_t i = 0; i < commandCount; ++i) {
        uint64_t word0 = commands[i].word[0];
        uint32_t opcode = (uint32_t)(word0 & 0xffu);
        uint32_t width = (uint32_t)((word0 >> 16) & 0xffffu);
        uint32_t height = (uint32_t)((word0 >> 32) & 0xffffu);
        uint64_t area = (uint64_t)width * height;
        if (opcode < 7) { countByOpcode[opcode]++; areaByOpcode[opcode] += area; }
        if (opcode == 2) {
            uint32_t tint = (uint32_t)commands[i].word[6];
            uint64_t gradient = commands[i].word[7];
            if (gradient != 0) gradientArea += area;
            else if (tint == 0xffffffffu) whiteArea += area;
            else if ((tint & 0x00ffffffu) == 0x00ffffffu) alphaOnlyArea += area;
            else otherTintArea += area;
        }
        printf("command=%u opcode=%u flags=%u size=%ux%u area=%" PRIu64
               " word1=%016" PRIx64 " dst=%d,%d uv=%08x,%08x"
               " step=%08x,%08x tint=%08x gradient=%016" PRIx64 "\n",
               i, opcode, (uint32_t)((word0 >> 8) & 0xffu), width, height, area,
               commands[i].word[1], (int16_t)commands[i].word[2],
               (int16_t)(commands[i].word[2] >> 16),
               (uint32_t)commands[i].word[4],
               (uint32_t)(commands[i].word[4] >> 32),
               (uint32_t)commands[i].word[5],
               (uint32_t)(commands[i].word[5] >> 32),
               (uint32_t)commands[i].word[6], commands[i].word[7]);
        reportAxisAlpha(fd, i, &commands[i]);
    }
    for (uint32_t opcode = 0; opcode < 7; ++opcode)
        if (countByOpcode[opcode] != 0)
            printf("opcode=%u count=%u area=%" PRIu64 "\n",
                   opcode, countByOpcode[opcode], areaByOpcode[opcode]);
    printf("blit_mix white=%" PRIu64 " alpha_only=%" PRIu64
           " other_tint=%" PRIu64 " gradient=%" PRIu64 "\n",
           whiteArea, alphaOnlyArea, otherTintArea, gradientArea);

	const uint32_t nativePhys[3] = {
		NATIVE_BUF0_PHYS, NATIVE_BUF1_PHYS, NATIVE_BUF2_PHYS
	};
	for (uint32_t buffer = 0; buffer < 3; ++buffer) {
        uint32_t pageBase = nativePhys[buffer] & ~4095u;
        size_t pageOffset = nativePhys[buffer] - pageBase;
        size_t mapBytes = pageOffset + NATIVE_BUF_BYTES;
        const uint8_t *mapping = mmap(NULL, mapBytes, PROT_READ,
                                      MAP_SHARED, fd, pageBase);
        if (mapping == MAP_FAILED) {
            perror("mmap native buffer");
            continue;
        }
        const uint32_t *pixels = (const uint32_t *)(mapping + pageOffset);
        uint32_t nonBlack = 0;
        uint32_t hash = 2166136261u;
        for (uint32_t i = 0; i < 320u * 240u; ++i) {
            uint32_t pixel = pixels[i];
            if ((pixel & 0x00ffffffu) != 0) nonBlack++;
            hash = (hash ^ pixel) * 16777619u;
        }
        printf("native_buffer=%u physical=%08x nonblack=%u hash=%08x"
               " samples=%08x,%08x,%08x,%08x\n",
               buffer, nativePhys[buffer], nonBlack, hash,
               pixels[0], pixels[159], pixels[320u * 120u + 160u],
               pixels[320u * 240u - 1u]);
        munmap((void *)mapping, mapBytes);
    }

    munmap((void *)commands, COMMAND_BYTES);
    munmap((void *)control, CONTROL_BYTES);
    close(fd);
    return 0;
}
