// SPDX-License-Identifier: GPL-3.0-or-later
// Temporary hardware QA helper: submit one clear+present list to the AM2R GPU.

#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#define CONTROL_PHYS 0x23ff0000u
#define COMMAND_PHYS 0x23fc0000u
#define NATIVE_WINDOW_PHYS 0x3a000000u
#define CONTROL_BYTES 4096u
#define COMMAND_BYTES 4096u
#define NATIVE_BUFFER_OFFSET 0x100u
#define NATIVE_BUFFER_BYTES (320u * 240u * 4u)
#define NATIVE_BUFFER_COUNT 3u
#define NATIVE_WINDOW_BYTES (NATIVE_BUFFER_OFFSET + NATIVE_BUFFER_COUNT * NATIVE_BUFFER_BYTES)
#define GPU_MAGIC 0x50473241u

typedef struct { uint64_t word[8]; } Command;

static int wait_for(volatile uint32_t *control, uint32_t sequence)
{
    for (unsigned i = 0; i < 50000; ++i) {
        __sync_synchronize();
        if (control[6] == sequence) return 0;
        usleep(100);
    }
    return -1;
}

int main(void)
{
    int fd = open("/dev/mem", O_RDWR | O_SYNC | O_CLOEXEC);
    if (fd < 0) { perror("open /dev/mem"); return 1; }
    volatile uint32_t *control = mmap(NULL, CONTROL_BYTES,
        PROT_READ | PROT_WRITE, MAP_SHARED, fd, CONTROL_PHYS);
    volatile Command *commands = mmap(NULL, COMMAND_BYTES,
        PROT_READ | PROT_WRITE, MAP_SHARED, fd, COMMAND_PHYS);
    volatile uint8_t *native = mmap(NULL, NATIVE_WINDOW_BYTES,
        PROT_READ, MAP_SHARED, fd, NATIVE_WINDOW_PHYS);
    if (control == MAP_FAILED || commands == MAP_FAILED || native == MAP_FAILED) {
        perror("mmap");
        return 1;
    }

    uint32_t originalSequence = control[1];
    if (wait_for(control, originalSequence) != 0) {
        fprintf(stderr, "original GPU job did not complete\n");
        return 2;
    }
    uint32_t original[8];
    for (unsigned i = 0; i < 8; ++i) original[i] = control[i];

    memset((void *)commands, 0, 2u * sizeof(Command));
    commands[0].word[0] = 1u;
    commands[0].word[1] = 0xff0000ffu; // opaque RGBA red
    uint32_t probeSequence = originalSequence + 0x10000u;
    if (probeSequence == 0) probeSequence = 0x10000u;

    control[0] = 0;
    __sync_synchronize();
    control[1] = probeSequence;
    control[2] = COMMAND_PHYS;
    control[3] = 2;
    control[4] = 0;
    control[5] = 0;
    control[6] = 0;
    control[7] = 0;
    __sync_synchronize();
    control[0] = GPU_MAGIC;
    __sync_synchronize();
    if (wait_for(control, probeSequence) != 0) {
        fprintf(stderr, "clear probe did not complete\n");
        return 3;
    }

    uint32_t completion = control[7];
    uint32_t buffer = completion >> 30;
    const volatile uint32_t *pixels = (const volatile uint32_t *)(
        native + NATIVE_BUFFER_OFFSET + buffer * NATIVE_BUFFER_BYTES);
    uint32_t nonRed = 0;
    for (unsigned i = 0; i < 320u * 240u; ++i)
        if ((pixels[i] & 0x00ffffffu) != 0x00ff0000u) nonRed++;
    printf("probe sequence=%u buffer=%u cycles=%u non_red=%u samples=%08x,%08x\n",
           probeSequence, buffer, completion & 0x1fffffffu, nonRed,
           pixels[0], pixels[320u * 120u + 160u]);

    // Leave the mailbox idle but make any pending runner-side wait see the
    // completion that existed before this isolated probe. Its next submission
    // will replace the remaining fields and use a sequence distinct from ours.
    control[0] = 0;
    for (unsigned i = 1; i < 8; ++i) control[i] = original[i];
    __sync_synchronize();

    munmap((void *)native, NATIVE_WINDOW_BYTES);
    munmap((void *)commands, COMMAND_BYTES);
    munmap((void *)control, CONTROL_BYTES);
    close(fd);
    return nonRed == 0 ? 0 : 4;
}
