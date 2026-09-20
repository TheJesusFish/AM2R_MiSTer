// SPDX-License-Identifier: GPL-3.0-or-later
// Read-only sampler for completed AM2R native frames and their command lists.

#define _FILE_OFFSET_BITS 64

#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#define CONTROL_PHYS UINT32_C(0x23ff0000)
#define CONTROL_BYTES 4096u
#define FRAME_BYTES (320u * 240u * 4u)

static const uint32_t frame_physical[3] = {
    UINT32_C(0x3a000100),
    UINT32_C(0x3a04b100),
    UINT32_C(0x3a096100),
};

static uint64_t monotonic_ns(void)
{
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (uint64_t)now.tv_sec * UINT64_C(1000000000) + (uint64_t)now.tv_nsec;
}

static const uint32_t *map_frame(int fd, uint32_t physical, void **mapping,
                                 size_t *mapping_bytes)
{
    long page_size = sysconf(_SC_PAGESIZE);
    uint32_t page_mask = (uint32_t)page_size - 1u;
    uint32_t aligned = physical & ~page_mask;
    size_t offset = physical - aligned;
    *mapping_bytes = offset + FRAME_BYTES;
    *mapping = mmap(NULL, *mapping_bytes, PROT_READ, MAP_SHARED, fd, aligned);
    if (*mapping == MAP_FAILED) return NULL;
    return (const uint32_t *)((const uint8_t *)*mapping + offset);
}

int main(int argc, char **argv)
{
    if (argc < 2 || argc > 3 ||
        (argc == 3 && strcmp(argv[2], "--timing-only") != 0)) {
        fprintf(stderr, "usage: %s duration-ms [--timing-only]\n", argv[0]);
        return 2;
    }
    int timing_only = argc == 3;
    char *end = NULL;
    long duration_ms = strtol(argv[1], &end, 0);
    if (!end || *end || duration_ms <= 0) return 2;

    int memory_fd = open("/dev/mem", O_RDONLY | O_SYNC | O_CLOEXEC);
    if (memory_fd < 0) {
        perror("/dev/mem");
        return 1;
    }
    volatile const uint32_t *control = mmap(NULL, CONTROL_BYTES, PROT_READ,
                                             MAP_SHARED, memory_fd,
                                             CONTROL_PHYS);
    if (control == MAP_FAILED) {
        perror("mmap control");
        close(memory_fd);
        return 1;
    }

    void *frame_mapping[3] = { NULL, NULL, NULL };
    size_t frame_mapping_bytes[3] = { 0, 0, 0 };
    const uint32_t *frames[3] = { NULL, NULL, NULL };
    for (unsigned i = 0; i < 3; ++i) {
        frames[i] = map_frame(memory_fd, frame_physical[i],
                              &frame_mapping[i], &frame_mapping_bytes[i]);
        if (!frames[i]) {
            perror("mmap frame");
            return 1;
        }
    }

    uint64_t start = monotonic_ns();
    uint64_t deadline = start + (uint64_t)duration_ms * UINT64_C(1000000);
    uint32_t previous_sequence = 0;
    puts("elapsed_ns,sequence,buffer,nonblack,hud_left,hud_right,hud_left_hash,min_column,max_column,commands,command_phys,vblank,scanout_frame,native_frame");
    while (monotonic_ns() < deadline) {
        __sync_synchronize();
        uint32_t submitted = control[1];
        uint32_t completed = control[6];
        uint32_t completion = control[7];
        if (completed != 0 && completed != previous_sequence &&
            submitted == completed) {
            unsigned buffer = completion >> 30;
            if (buffer < 3) {
                uint32_t nonblack = 0;
                uint32_t hud_left = 0;
                uint32_t hud_right = 0;
                uint32_t hud_left_hash = timing_only ? 0 : UINT32_C(2166136261);
                uint16_t minimum = 0;
                uint16_t maximum = 0;
                uint16_t column_counts[320] = { 0 };
                if (!timing_only) {
                    for (unsigned y = 0; y < 240; ++y) {
                        for (unsigned x = 0; x < 320; ++x) {
                            uint32_t pixel = frames[buffer][y * 320u + x];
                            if (y < 32 && x < 128) {
                                hud_left_hash ^= pixel;
                                hud_left_hash *= UINT32_C(16777619);
                            }
                            if ((pixel & UINT32_C(0x00ffffff)) != 0) {
                                nonblack++;
                                column_counts[x]++;
                                if (y < 32 && x < 128) hud_left++;
                                if (y < 64 && x >= 240) hud_right++;
                            }
                        }
                    }
                    minimum = column_counts[0];
                    maximum = column_counts[0];
                    for (unsigned x = 1; x < 320; ++x) {
                        if (column_counts[x] < minimum) minimum = column_counts[x];
                        if (column_counts[x] > maximum) maximum = column_counts[x];
                    }
                }
                printf("%" PRIu64 ",%" PRIu32 ",%u,%" PRIu32
                       ",%" PRIu32 ",%" PRIu32 ",%08" PRIx32
                       ",%u,%u,%u,%08" PRIx32
                       ",%" PRIu32 ",%" PRIu32
                       ",%" PRIu32 "\n",
                       monotonic_ns() - start, completed, buffer, nonblack,
                       hud_left, hud_right, hud_left_hash, minimum, maximum,
                       control[3] & 0xffffu, control[2],
                       control[16], control[18], control[19]);
                fflush(stdout);
                previous_sequence = completed;
            }
        }
        usleep(100);
    }

    for (unsigned i = 0; i < 3; ++i)
        munmap(frame_mapping[i], frame_mapping_bytes[i]);
    munmap((void *)control, CONTROL_BYTES);
    close(memory_fd);
    return 0;
}
