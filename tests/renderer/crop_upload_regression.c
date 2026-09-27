/* Actual production crop-span regression. The companion Python driver extracts
 * the implementation under test and optionally the preserved pre-edit baseline.
 * All target writes here are ordinary heap copies, not FPGA writes. */
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#if defined(__ARM_NEON)
#include <arm_neon.h>
#endif

static size_t uploadBytes, uploadCalls;
static uint8_t* trackedBase;
static size_t trackedWidth, trackedRows, previousWriteEnd;
static unsigned rowCopies[512];
static void trackedCopy(void *dst, const void *src, size_t size) {
    if (trackedBase) {
        assert(size != 0 && (uint8_t*)dst >= trackedBase);
        size_t offset = (size_t)((uint8_t*)dst - trackedBase);
        assert(offset <= trackedWidth * trackedRows && size <= trackedWidth * trackedRows - offset);
        assert(offset >= previousWriteEnd);
        size_t row = offset / trackedWidth, x = offset % trackedWidth;
        assert(x % 4 == 0 && size <= trackedWidth - x);
        assert((x + size) % 4 == 0 || x + size == trackedWidth);
        ++rowCopies[row];
        previousWriteEnd = offset + size;
    }
    uploadBytes += size;
    ++uploadCalls;
    memcpy(dst, src, size);
}
#define GPU_TEXTURE_COPY(dst, src, size) trackedCopy(dst, src, size)
#include "crop_upload_production.inc"
#ifdef CROP_BASELINE_EXTRACTED
#include "crop_upload_baseline.inc"
#else
/* Frozen original baseline keeps the public test runnable without private data.
 * --baseline-source substitutes functions extracted from the pre-edit file. */
static bool baselineBuffersEqual(const void *lhs, const void *rhs, size_t bytes) {
    if ((((uintptr_t)lhs | (uintptr_t)rhs | bytes) & 3u) != 0)
        return memcmp(lhs, rhs, bytes) == 0;
    const uint32_t *a = lhs, *b = rhs;
    size_t words = bytes / sizeof(uint32_t);
    while (words >= 8u) {
        if (a[0] != b[0] || a[1] != b[1] || a[2] != b[2] || a[3] != b[3] ||
            a[4] != b[4] || a[5] != b[5] || a[6] != b[6] || a[7] != b[7]) return false;
        a += 8; b += 8; words -= 8;
    }
    while (words-- != 0) { if (*a++ != *b++) return false; }
    return true;
}
static bool baselineUpload(uint8_t *shadowPixels, uint8_t *targetPixels,
                           const uint8_t *sourcePixels, size_t sourceRowBytes,
                           size_t cropRowBytes, size_t rowCount) {
    bool wrotePixels = false;
    for (size_t row = 0; row < rowCount; ++row) {
        const uint8_t *sourceRow = sourcePixels + row * sourceRowBytes;
        uint8_t *shadowRow = shadowPixels + row * cropRowBytes;
        if (baselineBuffersEqual(shadowRow, sourceRow, cropRowBytes)) continue;
        size_t first = 0;
        while (first < cropRowBytes && shadowRow[first] == sourceRow[first]) ++first;
        size_t last = cropRowBytes;
        while (last > first && shadowRow[last - 1] == sourceRow[last - 1]) --last;
        first &= ~(size_t)3;
        last = (last + 3) & ~(size_t)3;
        if (last > cropRowBytes) last = cropRowBytes;
        memcpy(shadowRow + first, sourceRow + first, last - first);
        GPU_TEXTURE_COPY(targetPixels + row * cropRowBytes + first, sourceRow + first, last - first);
        wrotePixels = true;
    }
    return wrotePixels;
}
#endif

static uint32_t randomState = 0x52601ab3;
static uint32_t randomValue(void) {
    randomState ^= randomState << 13;
    randomState ^= randomState >> 17;
    randomState ^= randomState << 5;
    return randomState;
}

static void verifyCase(const uint8_t *initial, const uint8_t *source,
                       size_t stride, size_t width, size_t rows, unsigned offset) {
    size_t count = width * rows;
    uint8_t *arrays[4];
    for (unsigned i = 0; i < 4; ++i) {
        arrays[i] = malloc(count + 64);
        assert(arrays[i]);
        memset(arrays[i], 0xa5, count + 64);
        memcpy(arrays[i] + offset, initial, count);
    }
    uploadBytes = uploadCalls = 0;
    bool expected = baselineUpload(arrays[0] + offset, arrays[1] + offset,
                                                   source, stride, width, rows);
    size_t expectedBytes = uploadBytes, expectedCalls = uploadCalls;
    uploadBytes = uploadCalls = 0;
    trackedBase = arrays[3] + offset;
    trackedWidth = width; trackedRows = rows; previousWriteEnd = 0;
    memset(rowCopies, 0, sizeof(rowCopies));
    bool actual = updateCroppedTextureChangedRows(arrays[2] + offset, arrays[3] + offset,
                                 source, stride, width, rows);
    trackedBase = NULL;
    assert(actual == expected);
    assert(uploadBytes <= expectedBytes && uploadCalls >= expectedCalls);
    assert(uploadCalls <= 8 * expectedCalls);
    assert(memcmp(arrays[0], arrays[2], count + 64) == 0);
    assert(memcmp(arrays[1], arrays[3], count + 64) == 0);
    for (size_t y = 0; y < rows; ++y) {
        assert(rowCopies[y] <= 8);
        assert(memcmp(arrays[3] + offset + y * width, source + y * stride, width) == 0);
    }
    for (unsigned i = 0; i < 4; ++i) free(arrays[i]);
}

static void regress(void) {
    uint8_t original[307200], source[491552];
    memset(original, 0x61, sizeof(original));
    memset(source, 0x61, sizeof(source));
    /* Every possible single-byte difference, including each word/vector tail. */
    unsigned cases = 0;
    for (size_t width = 1; width <= 137; ++width) {
        for (size_t changed = 0; changed < width; ++changed) {
            source[changed] ^= 0xa7;
            verifyCase(original, source, width + 17, width, 1, cases % 16);
            source[changed] ^= 0xa7;
            ++cases;
        }
    }
    for (unsigned trial = 0; trial < 3000; ++trial) {
        size_t width = 1 + randomValue() % 1280, rows = 1 + randomValue() % 17;
        size_t stride = width + randomValue() % 769;
        unsigned offset = randomValue() % 16, sourceOffset = randomValue() % 16;
        for (size_t i = 0; i < width * rows; ++i) original[i] = (uint8_t)randomValue();
        memset(source, 0x98, stride * rows + 16);
        for (size_t y = 0; y < rows; ++y)
            memcpy(source + sourceOffset + y * stride, original + y * width, width);
        for (unsigned edit = 0; edit < trial % 97; ++edit)
            source[sourceOffset + (randomValue() % rows) * stride + randomValue() % width] ^= 0x5a;
        verifyCase(original, source + sourceOffset, stride, width, rows, offset);
        ++cases;
    }
    /* Probe-boundary positions, exact64-byte gaps, merged subthreshold gaps,
     * partial last pixels, and the eight-copy cap. */
    for (unsigned shift = 0; shift < 32; shift += 4)
    for (unsigned gap = 0; gap <= 136; gap += 4)
    for (unsigned tail = 1; tail <= 4; ++tail) {
        size_t width = shift + 4 + gap + tail;
        memset(original, 0x61, width);
        memset(source, 0x61, width);
        source[shift + 3] ^= 1;
        source[width - 1] ^= 1;
        verifyCase(original, source, width, width, 1, shift % 16);
        assert(uploadBytes == (gap >= 64 ? 4 + tail : 4 + gap + tail));
        assert(uploadCalls == (gap >= 64 ? 2 : 1));
        ++cases;
    }
    memset(original, 0x61, 1280);
    memset(source, 0x61, 1280);
    for (size_t x = 0; x < 1280; x += 68) source[x] ^= 1;
    verifyCase(original, source, 1280, 1280, 1, 3);
    assert(uploadCalls == 8);
    ++cases;
    printf("PASS %u crop-span cases (same pixels/guards, ordered pixel-aligned writes, <=old bytes, <=8copies/row)\n", cases);
}

static void lifetimeRegression(void) {
    enum { WIDTH = 259, ROWS = 9, STRIDE = 391, BYTES = WIDTH * ROWS };
    uint8_t shadowOld[2][BYTES + 32], shadowNew[2][BYTES + 32];
    uint8_t targetOld[2][BYTES + 32], targetNew[2][BYTES + 32];
    uint8_t source[STRIDE * ROWS], sourceBefore[STRIDE * ROWS];
    memset(source, 0x67, sizeof(source));
    memset(shadowOld, 0x67, sizeof(shadowOld));
    memset(shadowNew, 0x67, sizeof(shadowNew));
    memset(targetOld, 0x67, sizeof(targetOld));
    memset(targetNew, 0x67, sizeof(targetNew));
    for (unsigned frame = 0; frame < 400; ++frame) {
        // Revisit alternate old contents; also update one allocation repeatedly,
        // including unchanged revisions and sparse/dense transitions.
        unsigned bank = frame % 3 == 0 ? 0 : frame % 2;
        if (frame % 4 != 0) {
            for (unsigned j = 0; j < (frame % 19); ++j)
                source[(randomValue() % ROWS) * STRIDE + randomValue() % WIDTH] ^= 0xc3;
        }
        if (frame % 31 == 0) memset(source, (uint8_t)frame, sizeof(source));
        memcpy(sourceBefore, source, sizeof(source));
        uploadBytes = uploadCalls = 0;
        bool oldChanged = baselineUpload(shadowOld[bank] + 3, targetOld[bank] + 3,
                                         source, STRIDE, WIDTH, ROWS);
        size_t oldBytes = uploadBytes;
        uploadBytes = uploadCalls = 0;
        bool newChanged = updateCroppedTextureChangedRows(shadowNew[bank] + 3, targetNew[bank] + 3,
                                                         source, STRIDE, WIDTH, ROWS);
        assert(newChanged == oldChanged && uploadBytes <= oldBytes);
        assert(!memcmp(shadowOld, shadowNew, sizeof(shadowOld)));
        assert(!memcmp(targetOld, targetNew, sizeof(targetOld)));
        assert(!memcmp(sourceBefore, source, sizeof(source)));
    }
    puts("PASS400 repeated/alternating upload sequences, full shadows/targets/source and guards identical");
}

typedef bool (*Upload)(uint8_t *, uint8_t *, const uint8_t *, size_t, size_t, size_t);
static volatile size_t benchmarkSink;
static void benchmarkOne(const char *name, Upload function, const uint8_t *source,
                         const uint8_t *initial, unsigned iterations) {
    uint8_t *shadow = malloc(307200), *target = malloc(307200);
    assert(shadow && target);
    uploadBytes = uploadCalls = 0;
    clock_t start = clock();
    for (unsigned i = 0; i < iterations; ++i) {
        memcpy(shadow, initial, 307200);
        function(shadow, target, source, 2048, 1280, 240);
        benchmarkSink += shadow[(i * 211u) % 307200];
    }
    double us = (double)(clock() - start) * 1000000.0 / CLOCKS_PER_SEC / iterations;
    printf("%s,%.3f,%zu,%zu\n", name, us, uploadBytes / iterations, uploadCalls / iterations);
    free(shadow); free(target);
}

static void benchmark(unsigned iterations) {
    uint8_t *source = malloc(491520), *initial = malloc(307200);
    assert(source && initial);
    memset(initial, 0xff, 307200);
    puts("Ordinary cached RAM only: includes shadow reset; NOT MiSTer DDR-upload timings.");
    puts("case,us_per_iteration,uploaded_bytes,copy_calls");
    for (unsigned pattern = 0; pattern < 4; ++pattern) {
        memset(source, 0xff, 491520);
        for (unsigned y = 0; y < 240; ++y) {
            if (pattern == 1) memset(source + y * 2048 + 600, 0x93, 16);
            if (pattern == 2) {
                memset(source + y * 2048 + 144, 0x93, 96);
                memset(source + y * 2048 + 1000, 0x37, 96);
            }
            if (pattern == 3) memset(source + y * 2048, 0x51, 1280);
        }
        const char *names[][2] = {{"unchanged_old", "unchanged_candidate"},
            {"thin_old", "thin_candidate"}, {"two_islands_old", "two_islands_candidate"},
            {"dense_old", "dense_candidate"}};
        benchmarkOne(names[pattern][0], baselineUpload, source, initial, iterations);
        benchmarkOne(names[pattern][1], updateCroppedTextureChangedRows, source, initial, iterations);
    }
    free(source); free(initial);
}

int main(int argc, char **argv) {
    regress();
    lifetimeRegression();
    if (argc == 2 && !strcmp(argv[1], "--regression-only")) return 0;
    if (argc > 1) {
        unsigned iterations = (unsigned)strtoul(argv[1], NULL, 10);
        if (iterations < 1 || iterations > 100000) return 2;
        benchmark(iterations);
    }
    return 0;
}
