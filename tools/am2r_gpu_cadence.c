// SPDX-License-Identifier: GPL-3.0-or-later
// Read-only timing sampler for completed AM2R FPGA GPU frames.

#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#define CONTROL_PHYS 0x23ff0000u
#define CONTROL_BYTES 4096u
#define GPU_MAGIC 0x50473241u
#define MAX_SAMPLES 16384u

typedef struct {
    uint64_t interval_ns;
    uint32_t sequence;
    uint32_t cycles;
} FrameSample;

static uint64_t now_nanos(void)
{
    struct timespec value;
    if (clock_gettime(CLOCK_MONOTONIC_RAW, &value) != 0) return 0;
    return (uint64_t)value.tv_sec * 1000000000ull + (uint64_t)value.tv_nsec;
}

static int compare_u64(const void *lhs, const void *rhs)
{
    const uint64_t a = *(const uint64_t *)lhs;
    const uint64_t b = *(const uint64_t *)rhs;
    return a < b ? -1 : a > b;
}

int main(int argc, char **argv)
{
    unsigned seconds = 30;
    if (argc > 2) {
        fprintf(stderr, "usage: %s [seconds=30]\n", argv[0]);
        return 2;
    }
    if (argc == 2) {
        char *end = NULL;
        unsigned long parsed = strtoul(argv[1], &end, 10);
        if (*argv[1] == '\0' || *end != '\0' || parsed == 0 || parsed > 180) {
            fprintf(stderr, "seconds must be in the range 1..180\n");
            return 2;
        }
        seconds = (unsigned)parsed;
    }

    int fd = open("/dev/mem", O_RDONLY | O_SYNC | O_CLOEXEC);
    if (fd < 0) {
        perror("open /dev/mem");
        return 1;
    }
    volatile uint32_t *control = mmap(NULL, CONTROL_BYTES, PROT_READ,
                                      MAP_SHARED, fd, CONTROL_PHYS);
    if (control == MAP_FAILED) {
        perror("mmap control");
        close(fd);
        return 1;
    }
    if (control[0] != GPU_MAGIC) {
        fprintf(stderr, "AM2R GPU is not active (magic=%08x)\n", control[0]);
        munmap((void *)control, CONTROL_BYTES);
        close(fd);
        return 1;
    }

    FrameSample *samples = calloc(MAX_SAMPLES, sizeof(*samples));
    uint64_t *sorted = calloc(MAX_SAMPLES, sizeof(*sorted));
    if (samples == NULL || sorted == NULL) {
        perror("calloc");
        free(samples);
        free(sorted);
        munmap((void *)control, CONTROL_BYTES);
        close(fd);
        return 1;
    }

    uint32_t last_sequence = control[6];
    uint64_t previous_ns = 0;
    uint64_t deadline = now_nanos() + (uint64_t)seconds * 1000000000ull;
    size_t count = 0;
    struct timespec pause = { .tv_sec = 0, .tv_nsec = 50000 };
    while (now_nanos() < deadline && count < MAX_SAMPLES) {
        uint32_t sequence = control[6];
        if (sequence != 0 && sequence != last_sequence) {
            uint64_t timestamp_ns = now_nanos();
            if (previous_ns != 0) {
                samples[count].interval_ns = timestamp_ns - previous_ns;
                samples[count].sequence = sequence;
                samples[count].cycles = control[7] & 0x1fffffffu;
                sorted[count] = samples[count].interval_ns;
                count++;
            }
            previous_ns = timestamp_ns;
            last_sequence = sequence;
        }
        nanosleep(&pause, NULL);
    }

    if (count == 0) {
        fprintf(stderr, "no completed GPU frame intervals observed\n");
        free(samples);
        free(sorted);
        munmap((void *)control, CONTROL_BYTES);
        close(fd);
        return 1;
    }

    qsort(sorted, count, sizeof(*sorted), compare_u64);
    uint64_t sum = 0, cycle_sum = 0;
    uint64_t cycle_min = UINT64_MAX, cycle_max = 0;
    size_t over_17 = 0, over_18 = 0, over_20 = 0, over_25 = 0;
    for (size_t i = 0; i < count; ++i) {
        uint64_t interval = samples[i].interval_ns;
        uint64_t cycles = samples[i].cycles;
        sum += interval;
        cycle_sum += cycles;
        if (cycles < cycle_min) cycle_min = cycles;
        if (cycles > cycle_max) cycle_max = cycles;
        if (interval > 17000000ull) over_17++;
        if (interval > 18000000ull) over_18++;
        if (interval > 20000000ull) over_20++;
        if (interval > 25000000ull) over_25++;
    }
    size_t p50 = (count - 1) * 50 / 100;
    size_t p95 = (count - 1) * 95 / 100;
    size_t p99 = (count - 1) * 99 / 100;
    printf("samples=%zu seconds=%u fps=%.4f\n", count, seconds,
           1.0e9 * (double)count / (double)sum);
    printf("interval_ms min=%.4f p50=%.4f p95=%.4f p99=%.4f max=%.4f\n",
           sorted[0] / 1.0e6, sorted[p50] / 1.0e6, sorted[p95] / 1.0e6,
           sorted[p99] / 1.0e6, sorted[count - 1] / 1.0e6);
    printf("deadline_counts over17=%zu over18=%zu over20=%zu over25=%zu\n",
           over_17, over_18, over_20, over_25);
    printf("gpu_cycles min=%" PRIu64 " avg=%.1f max=%" PRIu64 "\n",
           cycle_min, (double)cycle_sum / count, cycle_max);
    printf("worst_intervals\n");
    unsigned shown = 0;
    for (size_t i = 0; i < count && shown < 20; ++i) {
        if (samples[i].interval_ns <= 18000000ull) continue;
        printf("sequence=%u interval_ms=%.4f cycles=%u\n",
               samples[i].sequence, samples[i].interval_ns / 1.0e6,
               samples[i].cycles);
        shown++;
    }

    free(samples);
    free(sorted);
    munmap((void *)control, CONTROL_BYTES);
    close(fd);
    return 0;
}
