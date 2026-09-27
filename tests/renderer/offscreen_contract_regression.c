#include "mister_offscreen.h"

#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <limits.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define W 512
#define H 256
#define VIEW_W 320
#define VIEW_H 240

static uint32_t randomState = 0xa2f1e55u;
static uint32_t randomWord(void) {
    randomState ^= randomState << 13;
    randomState ^= randomState >> 17;
    randomState ^= randomState << 5;
    return randomState;
}

static uint32_t referencePixel(uint32_t destination, uint32_t source,
                               uint32_t tint, bool roundTint) {
    uint32_t result = 0;
    for (unsigned lane = 0; lane != 4; ++lane) {
        unsigned shift = lane * 8;
        unsigned sample = ((source >> shift) & 255) * ((tint >> shift) & 255);
        sample = (sample + (roundTint ? 127 : 0)) / 255;
        result |= (((destination >> shift) & 255) * (255 - sample) / 255) << shift;
    }
    return result;
}

static void referenceReplay(uint32_t* target, const MisterOffscreenAxis* op) {
    for (int y = op->y; y < op->y + op->height; ++y) {
        int sy = (int)(op->sourceY0 + (((double)y + 0.5) - op->vertexY0) * op->sourceDy);
        if (sy < 0) sy = 0;
        if (sy >= op->sourceHeight) sy = op->sourceHeight - 1;
        int64_t u = op->startU;
        for (int x = op->x; x < op->x + op->width; ++x) {
            int sx = (int)(u >> 16);
            if (sx < 0) sx = 0;
            if (sx >= op->sourceWidth) sx = op->sourceWidth - 1;
            uint32_t pixel = op->source[sy * op->sourceWidth + sx];
            target[y * W + x] = referencePixel(target[y * W + x], pixel, op->tint, false);
            if (x + 1 < op->x + op->width) u += op->stepU;
        }
    }
}

static void descriptorReplay(uint32_t* target, const MisterOffscreenAxis* op,
                             const MisterOffscreenBlit* commands, size_t count,
                             int viewW, int viewH, bool floorTint) {
    for (size_t command = 0; command < count; ++command) {
        const MisterOffscreenBlit* blit = &commands[command];
        assert(blit->x >= 0 && blit->y >= 0);
        assert(blit->width && blit->height);
        assert(blit->x + blit->width <= viewW && blit->y + blit->height <= viewH);
        for (int row = 0; row < blit->height; ++row) {
            int64_t v = (int64_t)blit->v + (int64_t)row * blit->dv;
            assert(v >= INT32_MIN && v <= INT32_MAX);
            int sy = (int)((v >> 16) & 65535);
            assert(sy >= 0 && sy < op->sourceHeight);
            for (int col = 0; col < blit->width; ++col) {
                int64_t u = (int64_t)blit->u + (int64_t)col * blit->du;
                assert(u >= INT32_MIN && u <= INT32_MAX);
                int sx = (int)(u >> 16);
                assert(sx >= 0 && sx < op->sourceWidth);
                int index = (blit->y + row) * viewW + blit->x + col;
                target[index] = referencePixel(target[index],
                    op->source[sy * op->sourceWidth + sx], op->tint, !floorTint);
            }
        }
    }
}

int main(void) {
    uint32_t* actual = malloc(W * H * sizeof(uint32_t));
    uint32_t* expected = malloc(W * H * sizeof(uint32_t));
    uint32_t* view = malloc(VIEW_W * VIEW_H * sizeof(uint32_t));
    uint32_t source[64 * 64];
    assert(actual && expected && view);
    MisterOffscreenBlit plan[MISTER_OFFSCREEN_MAX_BLITS];
    unsigned planned = 0, nonempty = 0, floorPlanned = 0, arbitraryTintPlans = 0;
    const double dyCases[] = {0, 1, -1, 2, -2, 0.5, 1.0 / 3.0, 1.7, -0.75};
    const int64_t duCases[] = {0, 65536, -65536, 131072, -131072, 32768, 21845, -49152};
    for (unsigned trial = 0; trial < 2400; ++trial) {
        for (unsigned i = 0; i < 64 * 64; ++i) source[i] = randomWord();
        uint32_t clear = randomWord();
        for (unsigned i = 0; i < W * H; ++i) actual[i] = expected[i] = clear;
        int viewX = (trial & 4) ? 17 : 0, viewY = (trial & 8) ? 9 : 0;
        for (unsigned i = 0; i < VIEW_W * VIEW_H; ++i) view[i] = clear;
        uint32_t tint = (trial % 3 == 0) ? randomWord() : 0xffffffffu;
        if (trial % 3 == 1) {
            tint = 0;
            for (unsigned shift = 0; shift < 32; shift += 8)
                if (randomWord() & 1) tint |= 255u << shift;
        }
        MisterOffscreenAxis op = {
            .source = source, .sourceWidth = 64, .sourceHeight = 64,
            .x = (int)(randomWord() % 470), .y = (int)(randomWord() % 215),
            .width = 1 + (int)(randomWord() % 42),
            .height = 1 + (int)(randomWord() % 41),
            .startU = ((int64_t)(randomWord() % 80) - 8) * 65536 + (randomWord() & 65535),
            .stepU = duCases[trial % 8],
            .sourceY0 = (double)(randomWord() % 80) - 8.0,
            .sourceDy = dyCases[trial % 9],
            .tint = tint,
        };
        op.vertexY0 = op.y + ((trial & 1) ? 0.5 : -0.25);
        referenceReplay(expected, &op);
        assert(MisterOffscreen_replayAxis(actual, W, H, &op));
        assert(!memcmp(actual, expected, W * H * sizeof(uint32_t)));
        size_t count = SIZE_MAX;
        if (MisterOffscreen_planAxis(&op, viewX, viewY, VIEW_W, VIEW_H,
                                     plan, MISTER_OFFSCREEN_MAX_BLITS, &count)) {
            planned++;
            if (count) nonempty++;
            assert(count <= MISTER_OFFSCREEN_MAX_BLITS);
            descriptorReplay(view, &op, plan, count, VIEW_W, VIEW_H, false);
            for (int y = 0; y < VIEW_H; ++y)
                for (int x = 0; x < VIEW_W; ++x)
                    assert(view[y * VIEW_W + x] == actual[(y + viewY) * W + x + viewX]);
        } else assert(count == 0);
        // The new descriptor uses the same exact row/crop plan, but floor
        // tint makes arbitrary color and fractional alpha eligible as well.
        for (unsigned i = 0; i < VIEW_W * VIEW_H; ++i) view[i] = clear;
        if (MisterOffscreen_planAxisTint(&op, viewX, viewY, VIEW_W, VIEW_H,
                plan, MISTER_OFFSCREEN_MAX_BLITS, &count, true)) {
            floorPlanned++;
            if (count && trial % 3 == 0) arbitraryTintPlans++;
            descriptorReplay(view, &op, plan, count, VIEW_W, VIEW_H, true);
            for (int y = 0; y < VIEW_H; ++y)
                for (int x = 0; x < VIEW_W; ++x)
                    assert(view[y * VIEW_W + x] == actual[(y + viewY) * W + x + viewX]);
        } else assert(count == 0);
    }
    assert(nonempty > 300);
    assert(floorPlanned > planned && arbitraryTintPlans > 100);

    // Specific visible-prefix quantization and thirds Y drift cases.
    MisterOffscreenAxis op = {
        .source = source, .sourceWidth = 64, .sourceHeight = 64,
        .x = 0, .y = 0, .width = 32, .height = 20,
        .startU = (int64_t)(-0.1 * 65536), .stepU = (int64_t)(0.1 * 65536),
        .sourceY0 = 0, .vertexY0 = 0.5, .sourceDy = 1.0 / 3.0,
        .tint = 0xffffffffu,
    };
    size_t count;
    assert(MisterOffscreen_planAxis(&op, 11, 0, 20, 20, plan, 240, &count));
    assert(plan[0].u == op.startU + 11 * op.stepU);
    assert((plan[0].u >> 16) == 0);
    // The planner may choose a proved accumulator interval to encode these
    // exact thirds samples in one descriptor; either form must match pixels.
    assert(count >= 1);
    for (unsigned i = 0; i < W * H; ++i) actual[i] = expected[i] = randomWord();
    memcpy(expected, actual, W * H * sizeof(uint32_t));

    // Nonbinary tint, capacity, bounds, float and accumulator failures are atomic.
    MisterOffscreenBlit sentinel[240];
    memset(plan, 0xa5, sizeof(plan)); memcpy(sentinel, plan, sizeof(plan));
    assert(!MisterOffscreen_planAxis(&op, 11, 0, 20, 20, plan, 0, &count));
    assert(count == 0 && !memcmp(plan, sentinel, sizeof(plan)));
    op.tint = 0x80808080;
    assert(!MisterOffscreen_planAxis(&op, 11, 0, 20, 20, plan, 240, &count));
    assert(count == 0 && !memcmp(plan, sentinel, sizeof(plan)));
    assert(!MisterOffscreen_planAxisTint(&op, 11, 0, 20, 20, plan, 240, &count, false));
    assert(count == 0 && !memcmp(plan, sentinel, sizeof(plan)));
    assert(!MisterOffscreen_planAxisTint(&op, 11, 0, 20, 20, plan, 0, &count, true));
    assert(count == 0 && !memcmp(plan, sentinel, sizeof(plan)));
    // Invisible unsupported tint needs no GPU work and is accepted.
    assert(MisterOffscreen_planAxis(&op, 100, 100, 20, 20, NULL, 0, &count) && count == 0);
    op.sourceDy = NAN;
    assert(!MisterOffscreen_replayAxis(actual, W, H, &op));
    assert(!memcmp(actual, expected, W * H * sizeof(uint32_t)));
    op.sourceDy = 1.0;
    op.startU = INT64_MAX; op.stepU = INT64_MAX;
    assert(!MisterOffscreen_replayAxis(actual, W, H, &op));
    assert(!memcmp(actual, expected, W * H * sizeof(uint32_t)));
    op.startU = 0; op.stepU = 65536; op.x = W - 1;
    assert(!MisterOffscreen_replayAxis(actual, W, H, &op));
    assert(!memcmp(actual, expected, W * H * sizeof(uint32_t)));

    // This one-pixel fixture must distinguish the two hardware tint modes.
    // Source 1 * tint 128 rounds to 1, but the CPU floors it to zero.
    source[0] = 0x01010101u;
    op = (MisterOffscreenAxis){
        .source = source, .sourceWidth = 64, .sourceHeight = 64,
        .width = 1, .height = 1, .tint = 0x80808080u,
    };
    assert(MisterOffscreen_planAxisTint(&op, 0, 0, 1, 1, plan, 240, &count, true));
    view[0] = 0xffffffffu;
    descriptorReplay(view, &op, plan, count, 1, 1, true);
    assert(view[0] == 0xffffffffu);
    view[0] = 0xffffffffu;
    descriptorReplay(view, &op, plan, count, 1, 1, false);
    assert(view[0] == 0xfefefefeu);

    // Mutable aliases must preserve the immediate CPU left-to-right order.
    for (unsigned trial = 0; trial < 32; ++trial) {
        for (unsigned i = 0; i < W * H; ++i) actual[i] = expected[i] = randomWord();
        memcpy(expected, actual, W * H * sizeof(uint32_t));
        op = (MisterOffscreenAxis){
            .source = expected, .sourceWidth = W, .sourceHeight = H,
            .x = 8, .y = 0, .width = 49, .height = 2,
            .startU = (trial & 1) ? 60 * 65536 : 0,
            .stepU = (trial & 1) ? -2 * 65536 : 2 * 65536,
            .sourceY0 = 0, .sourceDy = 1, .vertexY0 = 0.5,
            .tint = randomWord(),
        };
        referenceReplay(expected, &op);
        op.source = actual;
        assert(MisterOffscreen_replayAxis(actual, W, H, &op));
        assert(!memcmp(actual, expected, W * H * sizeof(uint32_t)));
    }
    printf("PASS: 2400 full CPU operations; %u legacy plans (%u nonempty); %u floor plans (%u arbitrary tint); 32 aliases; atomic rejects; exact row runs and tint discriminator\n", planned, nonempty, floorPlanned, arbitraryTintPlans);
    free(view); free(expected); free(actual);
    return 0;
}
