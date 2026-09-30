// Read-only AM2R GPU completion sampler. No framebuffer reads or MMIO writes.
// Control layout: src/backends/mister.c, rtl/am2r_gpu.sv, and
// rtl/am2r_ddr_arbiter.sv. Scanout details are asynchronous observations, not
// asserted to describe the same instant as the completed GPU job.
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

#define CONTROL_PHYS UINT32_C(0x23ff0000)
#define CONTROL_BYTES 4096u
#define GPU_MAGIC UINT32_C(0x50473241)
#define VBLANK_MAGIC UINT32_C(0x56424c4b)
#define CYCLE_MASK UINT32_C(0x1fffffff)
#define FLAG_MASK UINT32_C(0xe0000000)

typedef struct {
    uint64_t elapsed_ns;
    uint32_t sequence;
    uint32_t completion;
    uint32_t commands;
    uint32_t command_phys;
    uint32_t vblank_valid;
    uint32_t vblank;
    uint32_t scanout_frame;
    uint32_t native_frame;
} Sample;

static uint64_t monotonic_ns(void)
{
    struct timespec value;
    if (clock_gettime(CLOCK_MONOTONIC_RAW, &value) != 0) {
        perror("clock_gettime");
        exit(1);
    }
    return (uint64_t)value.tv_sec * UINT64_C(1000000000) +
           (uint64_t)value.tv_nsec;
}

static int snapshot(volatile const uint32_t *control, uint32_t previous,
                    Sample *sample)
{
    uint32_t magic = control[0];
    uint32_t submitted = control[1];
    uint32_t completed = control[6];
    if (magic != GPU_MAGIC || completed == 0 || completed == previous ||
        submitted != completed) return 0;
    __sync_synchronize();
    uint32_t completion = control[7];
    uint32_t commands = control[3];
    uint32_t command_phys = control[2];
    uint32_t vblank_magic = control[17];
    uint32_t vblank = control[16];
    uint32_t scanout = control[18];
    uint32_t native = control[19];
    __sync_synchronize();
    // Submission invalidates magic and clears completion before republishing.
    // Reject a read that straddled submission or another GPU completion.
    // Both high bits are the native-buffer index; all four values are valid.
    if (control[6] != completed || control[1] != submitted ||
        control[7] != completion || control[3] != commands ||
        control[2] != command_phys || control[0] != GPU_MAGIC) return 0;
    sample->sequence = completed;
    sample->completion = completion;
    sample->commands = commands & UINT32_C(0xffff);
    sample->command_phys = command_phys;
    sample->vblank_valid = vblank_magic == VBLANK_MAGIC;
    sample->vblank = vblank;
    sample->scanout_frame = scanout;
    sample->native_frame = native;
    return 1;
}

int main(int argc, char **argv)
{
    unsigned long duration_ms = 20000;
    if (argc > 2) {
        fprintf(stderr, "usage: %s [duration-ms=20000, max=180000]\n", argv[0]);
        return 2;
    }
    if (argc == 2) {
        char *end = NULL;
        errno = 0;
        duration_ms = strtoul(argv[1], &end, 10);
        if (errno || argv[1][0] == '\0' || *end ||
            duration_ms < 1 || duration_ms > 180000) {
            fprintf(stderr, "duration-ms must be 1..180000\n");
            return 2;
        }
    }
    // A 1 ms poll bounds samples; allocate before measuring and print afterward
    // so stdout/filesystem latency cannot disturb the measured gameplay route.
    const size_t capacity = (size_t)duration_ms + 2u;
    Sample *samples = calloc(capacity, sizeof(*samples));
    if (!samples) { perror("calloc"); return 1; }
    int fd = open("/dev/mem", O_RDONLY | O_SYNC | O_CLOEXEC);
    if (fd < 0) { perror("open /dev/mem"); free(samples); return 1; }
    volatile const uint32_t *control = mmap(NULL, CONTROL_BYTES, PROT_READ,
                                           MAP_SHARED, fd, CONTROL_PHYS);
    if (control == MAP_FAILED) {
        perror("mmap control"); close(fd); free(samples); return 1;
    }

    size_t count = 0;
    uint32_t previous = 0;
    uint64_t start = monotonic_ns();
    uint64_t deadline = start + (uint64_t)duration_ms * UINT64_C(1000000);
    const struct timespec poll = { .tv_sec = 0, .tv_nsec = 1000000 };
    while (monotonic_ns() < deadline && count < capacity) {
        Sample sample;
        if (snapshot(control, previous, &sample)) {
            sample.elapsed_ns = monotonic_ns() - start;
            samples[count++] = sample;
            previous = sample.sequence;
        }
        struct timespec remaining = poll;
        while (nanosleep(&remaining, &remaining) != 0 && errno == EINTR) {}
    }
    munmap((void *)control, CONTROL_BYTES);
    close(fd);

    puts("elapsed_ns,completed_seq,cycles,flags,buffer,underflow,commands,command_phys,vblank_valid,vblank,scanout_frame,native_frame");
    for (size_t i = 0; i < count; ++i) {
        const Sample *s = &samples[i];
        printf("%" PRIu64 ",%" PRIu32 ",%" PRIu32 ",0x%08" PRIx32
               ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",0x%08" PRIx32
               ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 "\n",
               s->elapsed_ns, s->sequence, s->completion & CYCLE_MASK,
               s->completion & FLAG_MASK, s->completion >> 30,
               (s->completion >> 29) & 1u, s->commands, s->command_phys,
               s->vblank_valid, s->vblank, s->scanout_frame, s->native_frame);
    }
    fprintf(stderr, "samples=%zu duration_ms=%lu poll_ns=1000000 read_only=1\n",
            count, duration_ms);
    free(samples);
    if (ferror(stdout)) { perror("stdout"); return 1; }
    return count ? 0 : 1;
}
