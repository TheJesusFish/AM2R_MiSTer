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
#define COMMAND_PHYS 0x23fe0000u
#define TEXTURE_PHYS 0x24000000u
#define FRAMEBUFFER_PHYS 0x22001000u
#define NATIVE_POOL_PHYS 0x3a000000u
#define NATIVE_BUF0_OFFSET 0x00000100u
#define NATIVE_BUF1_OFFSET 0x0004b100u
#define NATIVE_POOL_BYTES 0x00096100u
#define CONTROL_MAGIC 0x50473241u
#define FB_WIDTH 320u
#define FB_HEIGHT 240u
#define FB_BYTES (FB_WIDTH * FB_HEIGHT * 4u)
#define COMMAND_BYTES 4096u
#define TEXTURE_WIDTH 128u
#define TEXTURE_HEIGHT 64u
#define TEXTURE_BYTES (TEXTURE_WIDTH * TEXTURE_HEIGHT * 4u)

typedef struct {
    uint64_t word[8];
} GpuCommand;

static void *map_physical(int fd, uint32_t address, size_t size)
{
    void *result = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, address);
    if (result == MAP_FAILED) {
        fprintf(stderr, "mmap 0x%08x (%zu): %s\n", address, size, strerror(errno));
        return NULL;
    }
    return result;
}

static uint32_t rgba(uint8_t r, uint8_t g, uint8_t b, uint8_t a)
{
    return (uint32_t)r | ((uint32_t)g << 8) | ((uint32_t)b << 16) | ((uint32_t)a << 24);
}

static uint8_t blend_component(uint8_t src, uint8_t dst, uint8_t alpha)
{
    return (uint8_t)(((uint32_t)src * alpha + (uint32_t)dst * (255u - alpha)) / 255u);
}

static uint32_t expected_xrgb(uint32_t src, uint32_t dst)
{
    const uint8_t sr = src;
    const uint8_t sg = src >> 8;
    const uint8_t sb = src >> 16;
    const uint8_t sa = src >> 24;
    const uint8_t dr = dst >> 16;
    const uint8_t dg = dst >> 8;
    const uint8_t db = dst;
    const uint8_t r = blend_component(sr, dr, sa);
    const uint8_t g = blend_component(sg, dg, sa);
    const uint8_t b = blend_component(sb, db, sa);
    return ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;
}

static uint8_t saturating_add(uint8_t a, uint8_t b)
{
    const unsigned sum = (unsigned)a + b;
    return (uint8_t)(sum > 255u ? 255u : sum);
}

static uint32_t expected_add_xrgb(uint32_t src, uint32_t dst)
{
    const uint8_t sa = src >> 24;
    const uint8_t r = saturating_add(dst >> 16, (uint8_t)(((src & 255u) * sa) / 255u));
    const uint8_t g = saturating_add(dst >> 8, (uint8_t)((((src >> 8) & 255u) * sa) / 255u));
    const uint8_t b = saturating_add(dst, (uint8_t)((((src >> 16) & 255u) * sa) / 255u));
    return ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;
}

static void command_clear(GpuCommand *cmd, uint32_t color)
{
    memset(cmd, 0, sizeof(*cmd));
    cmd->word[0] = 1;
    cmd->word[1] = color;
}

static void command_blit(GpuCommand *cmd, uint32_t source, uint32_t stride,
                         int16_t x, int16_t y, uint16_t width, uint16_t height,
                         int32_t u, int32_t v, int32_t du, int32_t dv,
                         uint32_t tint)
{
    memset(cmd, 0, sizeof(*cmd));
    cmd->word[0] = 2u | ((uint64_t)width << 16) | ((uint64_t)height << 32);
    cmd->word[1] = source | ((uint64_t)stride << 32);
    cmd->word[2] = (uint16_t)x | ((uint64_t)(uint16_t)y << 16);
    cmd->word[4] = (uint32_t)u | ((uint64_t)(uint32_t)v << 32);
    cmd->word[5] = (uint32_t)du | ((uint64_t)(uint32_t)dv << 32);
    cmd->word[6] = tint;
}

static void command_fill(GpuCommand *cmd, int16_t x, int16_t y,
                         uint16_t width, uint16_t height, uint32_t color)
{
    memset(cmd, 0, sizeof(*cmd));
    cmd->word[0] = 3u | ((uint64_t)width << 16) | ((uint64_t)height << 32);
    cmd->word[1] = color;
    cmd->word[2] = (uint16_t)x | ((uint64_t)(uint16_t)y << 16);
}

static void command_blit_vgradient(GpuCommand *cmd, uint32_t source,
                         uint32_t stride, int16_t x, int16_t y,
                         uint16_t width, uint16_t height, int32_t u, int32_t v,
                         int32_t du, int32_t dv, uint32_t tintStart,
                         int16_t dr, int16_t dg, int16_t db, int16_t da)
{
    command_blit(cmd, source, stride, x, y, width, height, u, v, du, dv, tintStart);
    cmd->word[7] = (uint16_t)dr | ((uint64_t)(uint16_t)dg << 16) |
                   ((uint64_t)(uint16_t)db << 32) | ((uint64_t)(uint16_t)da << 48);
}

static void command_set_additive(GpuCommand *cmd)
{
    cmd->word[0] |= 1u << 8;
}

static void command_affine_blit(GpuCommand *cmd, uint32_t source, uint32_t stride,
                         int16_t x, int16_t y, uint16_t width, uint16_t height,
                         int32_t uMin, int32_t uMax, int32_t vMin, int32_t vMax,
                         int32_t uStart, int32_t vStart,
                         int32_t uDx, int32_t vDx, int32_t uDy, int32_t vDy)
{
    memset(cmd, 0, sizeof(*cmd));
    cmd->word[0] = 4u | ((uint64_t)width << 16) | ((uint64_t)height << 32);
    cmd->word[1] = source | ((uint64_t)stride << 32);
    cmd->word[2] = (uint16_t)x | ((uint64_t)(uint16_t)y << 16);
    cmd->word[3] = (uint32_t)uMin | ((uint64_t)(uint32_t)uMax << 32);
    cmd->word[4] = (uint32_t)vMin | ((uint64_t)(uint32_t)vMax << 32);
    cmd->word[5] = (uint32_t)uStart | ((uint64_t)(uint32_t)vStart << 32);
    cmd->word[6] = (uint32_t)uDx | ((uint64_t)(uint32_t)vDx << 32);
    cmd->word[7] = (uint32_t)uDy | ((uint64_t)(uint32_t)vDy << 32);
}

static void command_end(GpuCommand *cmd)
{
    memset(cmd, 0, sizeof(*cmd));
}

static int submit(volatile uint32_t *control, uint32_t command_count,
                  uint32_t *sequence, uint32_t *gpu_cycles)
{
    uint32_t next = *sequence + 1;
    if (next == 0) next = 1;

    // Invalidate the magic first so the FPGA cannot observe a partial update.
    control[0] = 0;
    __sync_synchronize();
    control[2] = COMMAND_PHYS;
    control[3] = command_count;
    control[4] = FRAMEBUFFER_PHYS;
    control[5] = 0;
    control[6] = 0;
    control[7] = 0;
    control[1] = next;
    __sync_synchronize();
    control[0] = CONTROL_MAGIC;
    __sync_synchronize();

    for (unsigned wait = 0; wait < 50000; ++wait) {
        __sync_synchronize();
        if (control[6] == next) {
            *sequence = next;
            *gpu_cycles = control[7];
            return 0;
        }
        usleep(100);
    }
    fprintf(stderr, "GPU timeout: submitted=%u completed=%u\n", next, control[6]);
    return -1;
}

static int expect_pixel(const volatile uint32_t *framebuffer, unsigned x,
                        unsigned y, uint32_t expected)
{
    const uint32_t actual = framebuffer[y * FB_WIDTH + x];
    if (actual != expected) {
        fprintf(stderr, "pixel (%u,%u): got %08x expected %08x\n",
                x, y, actual, expected);
        return 1;
    }
    return 0;
}

static double monotonic_seconds(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1000000000.0;
}

int main(int argc, char **argv)
{
    unsigned stress_frames = 30;
    if (argc == 3 && strcmp(argv[1], "--stress-frames") == 0)
        stress_frames = (unsigned)strtoul(argv[2], NULL, 0);
    else if (argc != 1) {
        fprintf(stderr, "usage: %s [--stress-frames N]\n", argv[0]);
        return 2;
    }

    int memfd = open("/dev/mem", O_RDWR | O_SYNC | O_CLOEXEC);
    if (memfd < 0) {
        fprintf(stderr, "open devices: %s\n", strerror(errno));
        return 1;
    }

    volatile uint32_t *control = map_physical(memfd, CONTROL_PHYS, 4096);
    GpuCommand *commands = map_physical(memfd, COMMAND_PHYS, COMMAND_BYTES);
    uint32_t *texture = map_physical(memfd, TEXTURE_PHYS, TEXTURE_BYTES);
    uint8_t *native_pool = map_physical(memfd, NATIVE_POOL_PHYS, NATIVE_POOL_BYTES);
    if (!control || !commands || !texture || !native_pool) {
        return 1;
    }
    volatile uint32_t *framebuffer0 =
        (volatile uint32_t *)(native_pool + NATIVE_BUF0_OFFSET);
    volatile uint32_t *framebuffer1 =
        (volatile uint32_t *)(native_pool + NATIVE_BUF1_OFFSET);

    for (unsigned y = 0; y < TEXTURE_HEIGHT; ++y) {
        for (unsigned x = 0; x < TEXTURE_WIDTH; ++x) {
            uint8_t a = ((x + y) & 3) == 0 ? 0 : (((x + y) & 3) == 1 ? 128 : 255);
            texture[y * TEXTURE_WIDTH + x] = rgba((uint8_t)(x * 3),
                                                  (uint8_t)(y * 5),
                                                  (uint8_t)(x ^ y), a);
        }
    }

    // Put exact edge cases in the first 4x2 texels.
    texture[0] = rgba(255, 0, 0, 255);
    texture[1] = rgba(0, 255, 0, 255);
    texture[2] = rgba(0, 0, 0, 0);
    texture[3] = rgba(0, 0, 255, 128);
    texture[TEXTURE_WIDTH + 0] = rgba(255, 255, 0, 255);
    texture[TEXTURE_WIDTH + 1] = rgba(255, 255, 255, 255);
    texture[TEXTURE_WIDTH + 2] = rgba(0, 0, 0, 255);
    texture[TEXTURE_WIDTH + 3] = rgba(255, 0, 255, 255);

    uint32_t sequence = control[6];
    uint32_t gpu_cycles = 0;
    const uint32_t clear_rgba = rgba(8, 16, 32, 255);
    const uint32_t clear_xrgb = 0x00081020;

    command_clear(&commands[0], clear_rgba);
    command_blit(&commands[1], TEXTURE_PHYS, TEXTURE_WIDTH * 4,
                 1, 1, 4, 2, 0, 0, 1 << 16, 1 << 16, 0xffffffffu);
    command_fill(&commands[2], 5, 2, 3, 1, rgba(0, 255, 0, 128));
    command_blit(&commands[3], TEXTURE_PHYS, TEXTURE_WIDTH * 4,
                 9, 2, 1, 1, 0, 0, 1 << 16, 1 << 16,
                 rgba(128, 255, 255, 128));
    command_blit_vgradient(&commands[4], TEXTURE_PHYS, TEXTURE_WIDTH * 4,
                 10, 1, 1, 3, 0, 0, 0, 0, 0xffffffffu,
                 0, 0, 0, (int16_t)0xc000);
    command_blit(&commands[5], TEXTURE_PHYS, TEXTURE_WIDTH * 4,
                 11, 1, 1, 1, 3 << 16, 0, 0, 0, 0xffffffffu);
    command_set_additive(&commands[5]);
    command_affine_blit(&commands[6], TEXTURE_PHYS, TEXTURE_WIDTH * 4,
                 20, 1, 3, 2, 0, 4 << 16, 0, 2 << 16,
                 0, 0, 1 << 16, 1 << 16, 1 << 16, 0);
    command_end(&commands[7]);
    __sync_synchronize();
    if (submit(control, 8, &sequence, &gpu_cycles)) return 1;

    // Native scanout is double buffered. Select the buffer just published by
    // this submission rather than relying on the retired Linux framebuffer.
    __sync_synchronize();
    volatile uint32_t *framebuffer = framebuffer0[0] == clear_xrgb ?
        framebuffer0 : framebuffer1;

    int errors = 0;
    errors += expect_pixel(framebuffer, 0, 0, clear_xrgb);
    errors += expect_pixel(framebuffer, 1, 1, 0x00ff0000);
    errors += expect_pixel(framebuffer, 2, 1, 0x0000ff00);
    errors += expect_pixel(framebuffer, 3, 1, clear_xrgb);
    errors += expect_pixel(framebuffer, 4, 1,
                           expected_xrgb(rgba(0, 0, 255, 128), clear_xrgb));
    errors += expect_pixel(framebuffer, 1, 2, 0x00ffff00);
    errors += expect_pixel(framebuffer, 2, 2, 0x00ffffff);
    errors += expect_pixel(framebuffer, 3, 2, 0x00000000);
    errors += expect_pixel(framebuffer, 4, 2, 0x00ff00ff);
    errors += expect_pixel(framebuffer, 5, 2,
                           expected_xrgb(rgba(0, 255, 0, 128), clear_xrgb));
    errors += expect_pixel(framebuffer, 6, 2,
                           expected_xrgb(rgba(0, 255, 0, 128), clear_xrgb));
    errors += expect_pixel(framebuffer, 7, 2,
                           expected_xrgb(rgba(0, 255, 0, 128), clear_xrgb));
    errors += expect_pixel(framebuffer, 8, 2, clear_xrgb);
    errors += expect_pixel(framebuffer, 9, 2,
                           expected_xrgb(rgba(128, 0, 0, 128), clear_xrgb));
    errors += expect_pixel(framebuffer, 10, 1, 0x00ff0000);
    errors += expect_pixel(framebuffer, 10, 2,
                           expected_xrgb(rgba(255, 0, 0, 191), clear_xrgb));
    errors += expect_pixel(framebuffer, 10, 3,
                           expected_xrgb(rgba(255, 0, 0, 127), clear_xrgb));
    errors += expect_pixel(framebuffer, 11, 1,
                           expected_add_xrgb(rgba(0, 0, 255, 128), clear_xrgb));
    errors += expect_pixel(framebuffer, 20, 1, 0x00ff0000);
    errors += expect_pixel(framebuffer, 21, 1, 0x00ffffff);
    errors += expect_pixel(framebuffer, 22, 1, clear_xrgb);
    errors += expect_pixel(framebuffer, 20, 2, 0x0000ff00);
    errors += expect_pixel(framebuffer, 21, 2, 0x00000000);
    errors += expect_pixel(framebuffer, 22, 2, clear_xrgb);
    errors += expect_pixel(framebuffer, 319, 239, clear_xrgb);
    if (errors) {
        fprintf(stderr, "GPU correctness test failed with %d mismatches\n", errors);
        return 1;
    }
    printf("correctness: PASS, cycles=%u (%.3f ms at 80 MHz)\n",
           gpu_cycles, gpu_cycles / 80000.0);

    // Representative AM2R load: 49 alpha-textured quads and ~240k sampled
    // pixels, matching the measured first-playable-room command census.
    const unsigned quad_count = 49;
    const unsigned quad_width = 100;
    const unsigned quad_height = 49;
    const unsigned sampled_pixels = quad_count * quad_width * quad_height;
    command_clear(&commands[0], rgba(3, 7, 11, 255));
    for (unsigned i = 0; i < quad_count; ++i) {
        int16_t x = (int16_t)((i * 37) % 300) - 40;
        int16_t y = (int16_t)((i * 23) % 220) - 20;
        command_blit(&commands[i + 1], TEXTURE_PHYS, TEXTURE_WIDTH * 4,
                     x, y, quad_width, quad_height, 0, 0,
                     1 << 16, 1 << 16, 0xffffffffu);
    }
    command_end(&commands[quad_count + 1]);
    __sync_synchronize();

    uint64_t total_cycles = 0;
    uint32_t min_cycles = UINT32_MAX, max_cycles = 0;
    const double start = monotonic_seconds();
    for (unsigned frame = 0; frame < stress_frames; ++frame) {
        if (submit(control, quad_count + 2, &sequence, &gpu_cycles)) return 1;
        total_cycles += gpu_cycles;
        if (gpu_cycles < min_cycles) min_cycles = gpu_cycles;
        if (gpu_cycles > max_cycles) max_cycles = gpu_cycles;
    }
    const double elapsed = monotonic_seconds() - start;
    const double average_cycles = stress_frames ? (double)total_cycles / stress_frames : 0.0;
    const double gpu_fps = average_cycles ? 80000000.0 / average_cycles : 0.0;
    const double submit_fps = elapsed ? stress_frames / elapsed : 0.0;
    const double mpixels = elapsed ? ((double)sampled_pixels * stress_frames / elapsed) / 1000000.0 : 0.0;
    printf("stress: frames=%u quads/frame=%u sampled_pixels/frame=%u\n",
           stress_frames, quad_count, sampled_pixels);
    printf("stress: gpu_cycles min=%u avg=%.1f max=%u, gpu_fps=%.2f\n",
           min_cycles, average_cycles, max_cycles, gpu_fps);
    printf("stress: wall=%.3f s submit_fps=%.2f sampled=%.2f MPix/s\n",
           elapsed, submit_fps, mpixels);
    return 0;
}
