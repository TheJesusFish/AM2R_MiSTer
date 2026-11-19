// Read-only, buffered native active-start cadence measurements. The FPGA
// counts every raster/repeat/skipped publication; CPU polling cannot lose
// those events. This does not measure the independent HDMI adoption edge.
// SPDX-License-Identifier: GPL-2.0-or-later
#define _GNU_SOURCE
#define _FILE_OFFSET_BITS 64
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#define PRESENT_MAGIC UINT32_C(0x5352504e)
typedef struct {
    uint64_t elapsed_ns;
    uint32_t generation, rasters, scanout, repeats, skips;
} Sample;

static uint64_t now_ns(void)
{
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC_RAW, &t)) { perror("clock_gettime"); exit(1); }
    return (uint64_t)t.tv_sec * UINT64_C(1000000000) + (uint64_t)t.tv_nsec;
}

static int snapshot(volatile const uint32_t *control, Sample *s)
{
    // Header is written odd BEFORE either payload, then committed even.
    // The barriers include device reads on ARM; an interrupted transaction
    // or a change during these reads is not a usable observation.
    uint32_t generation = control[23];
    if (!generation || (generation & 1u)) return 0;
    __sync_synchronize();
    uint32_t magic = control[22];
    s->rasters = control[24];
    s->scanout = control[25];
    s->repeats = control[26];
    s->skips = control[27];
    __sync_synchronize();
    if (magic != PRESENT_MAGIC || control[23] != generation ||
        control[22] != PRESENT_MAGIC) return 0;
    s->generation = generation;
    return 1;
}

int main(int argc, char **argv)
{
    unsigned long duration_ms = 45000;
    char *end = NULL;
    if (argc > 2) return 2;
    if (argc == 2) {
        errno = 0;
        duration_ms = strtoul(argv[1], &end, 10);
        if (errno || !argv[1][0] || *end) return 2;
    }
    if (duration_ms < 1 || duration_ms > 180000) return 2;
    size_t capacity = (size_t)duration_ms + 2u;
    Sample *samples = calloc(capacity, sizeof(*samples));
    if (!samples) { perror("calloc"); return 1; }
    // Fault sample pages in before the timed interval, not while the game
    // is submitting a frame. Hardware counters remain authoritative.
    for (size_t i=0; i<capacity*sizeof(*samples); i+=4096)
        ((volatile unsigned char *)samples)[i] = 0;
    int fd = open("/dev/mem", O_RDONLY | O_SYNC | O_CLOEXEC);
    if (fd < 0) { perror("open /dev/mem"); free(samples); return 1; }
    volatile const uint32_t *control = mmap(NULL, 4096, PROT_READ, MAP_SHARED, fd, UINT32_C(0x23ff0000));
    if (control == MAP_FAILED) { perror("mmap"); close(fd); free(samples); return 1; }
    size_t n = 0;
    unsigned invalid = 0;
    uint32_t previous = 0;
    uint32_t previous_rasters=0, previous_repeats=0, previous_skips=0;
    int reset_epoch = 0;
    uint64_t start = now_ns(), deadline = start + duration_ms * UINT64_C(1000000);
    const struct timespec poll = { .tv_sec = 0, .tv_nsec = 1000000 };
    while (now_ns() < deadline && n < capacity) {
        Sample s;
        if (snapshot(control, &s)) {
            // No32-bit counter can naturally wrap in this <=180s window
            // after a fresh QA core launch. Refuse a reset epoch instead of
            // turning a decreasing counter into billions of missed frames.
            if (previous && (s.generation < previous || s.rasters < previous_rasters ||
                             s.repeats < previous_repeats || s.skips < previous_skips)) {
                reset_epoch = 1;
                break;
            }
            if (s.generation != previous) {
                s.elapsed_ns = now_ns() - start;
                samples[n++] = s;
                previous = s.generation;
                previous_rasters=s.rasters; previous_repeats=s.repeats; previous_skips=s.skips;
            }
        } else invalid++;
        struct timespec remaining = poll;
        while (nanosleep(&remaining, &remaining) && errno == EINTR) {}
    }
    munmap((void *)control, 4096); close(fd);
    puts("elapsed_ns,generation,active_rasters,scanout_frame,repeat_rasters,skipped_publications");
    for (size_t i=0; i<n; i++) {
        const Sample *s = &samples[i];
        printf("%" PRIu64 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 "\n",
               s->elapsed_ns, s->generation, s->rasters, s->scanout, s->repeats, s->skips);
    }
    fprintf(stderr,"samples=%zu invalid_polls=%u duration_ms=%lu poll_ns=1000000 read_only=1 reset_epoch=%d\n",n,invalid,duration_ms,reset_epoch);
    free(samples);
    return n && !reset_epoch && !ferror(stdout) ? 0 : 1;
}
