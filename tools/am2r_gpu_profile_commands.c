// SPDX-License-Identifier: GPL-3.0-or-later
// Profile the exact completed AM2R command list on MiSTer.
//
// The caller must stop the ARM runner before invoking this tool and resume it
// afterwards. The tool copies the completed list, uses the inactive 64 KiB
// command buffer for isolated submissions, restores that buffer, and leaves
// the mailbox invalid until the runner publishes its next complete list.

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#define CONTROL_PHYS 0x23ff0000u
#define CONTROL_BYTES 4096u
#define COMMAND_A_PHYS 0x23fd0000u
#define COMMAND_B_PHYS 0x23fe0000u
#define COMMAND_BYTES 65536u
#define GPU_MAGIC 0x50473241u
#define GPU_CYCLE_MASK 0x1fffffffu
#define REPEATS 3u

typedef struct { uint64_t word[8]; } Command;

static void copy_from_volatile(void *destination, const volatile void *source,
                               size_t bytes)
{
    uint8_t *out = destination;
    const volatile uint8_t *in = source;
    while (bytes--) *out++ = *in++;
}

static void copy_to_volatile(volatile void *destination, const void *source,
                             size_t bytes)
{
    volatile uint8_t *out = destination;
    const uint8_t *in = source;
    while (bytes--) *out++ = *in++;
}

static int wait_for_completion(volatile uint32_t *control, uint32_t sequence,
                               uint32_t *cycles)
{
    for (unsigned wait = 0; wait < 100000; ++wait) {
        __sync_synchronize();
        if (control[6] == sequence) {
            *cycles = control[7] & GPU_CYCLE_MASK;
            return 0;
        }
        usleep(50);
    }
    fprintf(stderr, "GPU timeout: submitted=%u completed=%u\n",
            sequence, control[6]);
    return -1;
}

static int submit(volatile uint32_t *control, uint32_t command_phys,
                  uint32_t command_count, uint32_t framebuffer_phys,
                  uint32_t *sequence, uint32_t *cycles)
{
    uint32_t next = *sequence + 1u;
    if (next == 0) next = 1;
    control[0] = 0;
    __sync_synchronize();
    control[2] = command_phys;
    control[3] = command_count;
    control[4] = framebuffer_phys;
    control[5] = 0;
    control[6] = 0;
    control[7] = 0;
    control[1] = next;
    __sync_synchronize();
    control[0] = GPU_MAGIC;
    __sync_synchronize();
    if (wait_for_completion(control, next, cycles)) return -1;
    *sequence = next;
    return 0;
}

static uint32_t median3(uint32_t a, uint32_t b, uint32_t c)
{
    if (a > b) { uint32_t temporary = a; a = b; b = temporary; }
    if (b > c) { uint32_t temporary = b; b = c; c = temporary; }
    if (a > b) b = a;
    return b;
}

int main(void)
{
    int result = 1;
    int memory_fd = -1;
    volatile uint32_t *control = MAP_FAILED;
    volatile Command *active = MAP_FAILED;
    volatile Command *scratch = MAP_FAILED;
    Command *snapshot = NULL;
    uint8_t *scratch_backup = NULL;

    memory_fd = open("/dev/mem", O_RDWR | O_SYNC | O_CLOEXEC);
    if (memory_fd < 0) { perror("open /dev/mem"); goto cleanup; }
    control = mmap(NULL, CONTROL_BYTES, PROT_READ | PROT_WRITE, MAP_SHARED,
                   memory_fd, CONTROL_PHYS);
    if (control == MAP_FAILED) { perror("mmap control"); goto cleanup; }

    uint32_t magic = control[0];
    uint32_t sequence = control[1];
    uint32_t command_phys = control[2];
    uint32_t command_count = control[3] & 0xffffu;
    uint32_t framebuffer_phys = control[4];
    uint32_t completed = control[6];
    if (magic != GPU_MAGIC || completed != sequence || command_count == 0 ||
        command_count > COMMAND_BYTES / sizeof(Command) ||
        (command_phys != COMMAND_A_PHYS && command_phys != COMMAND_B_PHYS)) {
        fprintf(stderr, "unstable mailbox magic=%08x sequence=%u completed=%u "
                        "commands=%u physical=%08x\n", magic, sequence,
                completed, command_count, command_phys);
        goto cleanup;
    }

    uint32_t scratch_phys = command_phys == COMMAND_A_PHYS ?
                            COMMAND_B_PHYS : COMMAND_A_PHYS;
    active = mmap(NULL, COMMAND_BYTES, PROT_READ, MAP_SHARED, memory_fd,
                  command_phys);
    scratch = mmap(NULL, COMMAND_BYTES, PROT_READ | PROT_WRITE, MAP_SHARED,
                   memory_fd, scratch_phys);
    if (active == MAP_FAILED || scratch == MAP_FAILED) {
        perror("mmap command buffer");
        goto cleanup;
    }
    snapshot = malloc((size_t)command_count * sizeof(Command));
    scratch_backup = malloc(COMMAND_BYTES);
    if (!snapshot || !scratch_backup) { perror("malloc"); goto cleanup; }
    copy_from_volatile(snapshot, active,
                       (size_t)command_count * sizeof(Command));
    copy_from_volatile(scratch_backup, scratch, COMMAND_BYTES);

    Command end = {{0}};
    uint32_t baseline_samples[REPEATS];
    for (unsigned repeat = 0; repeat < REPEATS; ++repeat) {
        copy_to_volatile(&scratch[0], &end, sizeof(end));
        __sync_synchronize();
        if (submit(control, scratch_phys, 1, framebuffer_phys, &sequence,
                   &baseline_samples[repeat])) goto restore;
    }
    uint32_t baseline = median3(baseline_samples[0], baseline_samples[1],
                                baseline_samples[2]);
    printf("profile sequence=%u commands=%u source=%08x scratch=%08x "
           "present_baseline=%u\n", control[1], command_count, command_phys,
           scratch_phys, baseline);
    printf("index,opcode,flags,width,height,area,total_cycles,command_cycles\n");

    for (uint32_t index = 0; index < command_count; ++index) {
        uint32_t opcode = (uint32_t)snapshot[index].word[0] & 0xffu;
        if (opcode == 0) continue;
        uint32_t samples[REPEATS];
        for (unsigned repeat = 0; repeat < REPEATS; ++repeat) {
            copy_to_volatile(&scratch[0], &snapshot[index],
                             sizeof(Command));
            copy_to_volatile(&scratch[1], &end, sizeof(end));
            __sync_synchronize();
            if (submit(control, scratch_phys, 2, framebuffer_phys, &sequence,
                       &samples[repeat])) goto restore;
        }
        uint32_t total = median3(samples[0], samples[1], samples[2]);
        uint32_t command_cycles = total > baseline ? total - baseline : 0;
        uint32_t flags = ((uint32_t)snapshot[index].word[0] >> 8) & 0xffu;
        uint32_t width = (uint32_t)(snapshot[index].word[0] >> 16) & 0xffffu;
        uint32_t height = (uint32_t)(snapshot[index].word[0] >> 32) & 0xffffu;
        printf("%u,%u,%u,%u,%u,%" PRIu64 ",%u,%u\n", index, opcode,
               flags, width, height, (uint64_t)width * height, total,
               command_cycles);
    }
    result = 0;

restore:
    control[0] = 0;
    __sync_synchronize();
    if (scratch != MAP_FAILED && scratch_backup)
        copy_to_volatile(scratch, scratch_backup, COMMAND_BYTES);
    __sync_synchronize();
cleanup:
    free(scratch_backup);
    free(snapshot);
    if (scratch != MAP_FAILED) munmap((void *)scratch, COMMAND_BYTES);
    if (active != MAP_FAILED) munmap((void *)active, COMMAND_BYTES);
    if (control != MAP_FAILED) munmap((void *)control, CONTROL_BYTES);
    if (memory_fd >= 0) close(memory_fd);
    return result;
}
