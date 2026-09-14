// SPDX-License-Identifier: GPL-3.0-or-later
// Temporary hardware QA helper: fill both AM2R native scanout buffers.

#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/mman.h>
#include <unistd.h>

#define NATIVE_WINDOW_PHYS 0x3a000000u
#define NATIVE_BUFFER_OFFSET 0x100u
#define WIDTH 320u
#define HEIGHT 240u
#define NATIVE_BUFFER_BYTES (WIDTH * HEIGHT * 4u)
#define NATIVE_BUFFER_COUNT 3u
#define NATIVE_WINDOW_BYTES (NATIVE_BUFFER_OFFSET + NATIVE_BUFFER_COUNT * NATIVE_BUFFER_BYTES)

static uint32_t test_pixel(uint32_t x, uint32_t y)
{
    static const uint32_t bars[8] = {
        0x00ffffffu, 0x00ffff00u, 0x0000ffffu, 0x0000ff00u,
        0x00ff00ffu, 0x00ff0000u, 0x000000ffu, 0x00000000u,
    };
    uint32_t pixel = bars[(x * 8u) / WIDTH];
    if (((x >> 4) ^ (y >> 4)) & 1u) pixel ^= 0x00101010u;
    return pixel;
}

int main(void)
{
    int fd = open("/dev/mem", O_RDWR | O_SYNC | O_CLOEXEC);
    if (fd < 0) {
        perror("open /dev/mem");
        return 1;
    }

    uint8_t *mapping = mmap(NULL, NATIVE_WINDOW_BYTES, PROT_READ | PROT_WRITE,
                            MAP_SHARED, fd, NATIVE_WINDOW_PHYS);
    if (mapping == MAP_FAILED) {
        perror("mmap native window");
        close(fd);
        return 1;
    }

	for (uint32_t buffer = 0; buffer < NATIVE_BUFFER_COUNT; ++buffer) {
        volatile uint32_t *pixels = (volatile uint32_t *)(
            mapping + NATIVE_BUFFER_OFFSET + buffer * NATIVE_BUFFER_BYTES);
        for (uint32_t y = 0; y < HEIGHT; ++y)
            for (uint32_t x = 0; x < WIDTH; ++x)
                pixels[y * WIDTH + x] = test_pixel(x, y);
    }
    __sync_synchronize();
	printf("filled three AM2R native buffers from 0x3a000100\n");

    munmap(mapping, NATIVE_WINDOW_BYTES);
    close(fd);
    return 0;
}
