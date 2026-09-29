// SPDX-License-Identifier: GPL-3.0-or-later
// Read-only native XRGB8888 snapshot; stdout is exactly 320*240*4 bytes.
// Reject concurrent publication rather than reporting a potentially torn image.
#define _GNU_SOURCE
#define _FILE_OFFSET_BITS 64
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#define CONTROL_PHYS UINT32_C(0x23ff0000)
#define NATIVE_PHYS UINT32_C(0x3a000000)
#define FRAME_BYTES (320u * 240u * 4u)
#define NATIVE_BYTES (256u + 4u * FRAME_BYTES)
#define GPU_MAGIC UINT32_C(0x50473241)

int main(int argc, char **argv)
{
    (void)argv;
    if (argc != 1) return 2;
    int fd = open("/dev/mem", O_RDONLY | O_SYNC | O_CLOEXEC);
    if (fd < 0) { perror("open /dev/mem"); return 1; }
    volatile const uint32_t *control = mmap(NULL, 4096, PROT_READ,
                                           MAP_SHARED, fd, CONTROL_PHYS);
    if (control == MAP_FAILED) { perror("control map"); close(fd); return 1; }
    const uint8_t *native = mmap(NULL, NATIVE_BYTES, PROT_READ,
                                MAP_SHARED, fd, NATIVE_PHYS);
    if (native == MAP_FAILED) {
        perror("native map"); munmap((void *)control, 4096); close(fd); return 1;
    }
    static uint8_t snapshot[FRAME_BYTES];
    int result = 1;
    for (unsigned attempt = 0; attempt < 2000; ++attempt) {
        uint32_t sequence = control[6], completion = control[7];
        uint32_t frame = control[19];
        unsigned buffer = completion >> 30;
        if (control[0] == GPU_MAGIC && sequence && buffer < 4 &&
            sequence == control[1]) {
            __sync_synchronize();
            memcpy(snapshot, native + 256u + buffer * FRAME_BYTES, FRAME_BYTES);
            __sync_synchronize();
            if (control[0] == GPU_MAGIC && control[1] == sequence &&
                control[6] == sequence && control[7] == completion &&
                control[19] == frame) {
                fprintf(stderr, "stable_native_frame=%" PRIu32
                        " completed_sequence=%" PRIu32 " buffer=%u bytes=%u\n",
                        frame, sequence, buffer, FRAME_BYTES);
                result = fwrite(snapshot, 1, FRAME_BYTES, stdout) != FRAME_BYTES;
                if (fflush(stdout) != 0) result = 1;
                break;
            }
        }
        usleep(1000);
    }
    if (result) fprintf(stderr, "No stable snapshot written, or stdout failed.\n");
    munmap((void *)native, NATIVE_BYTES);
    munmap((void *)control, 4096);
    close(fd);
    return result;
}
