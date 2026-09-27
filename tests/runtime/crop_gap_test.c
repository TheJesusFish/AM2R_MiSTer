// SPDX-License-Identifier: GPL-3.0-or-later
// Run the actual opt-in metric collector; all pixels are ordinary test RAM.
#include <fcntl.h>
#ifdef _WIN32
#define O_CLOEXEC 0
#define O_NOFOLLOW 0
#define O_NONBLOCK 0
#endif
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include "../../third_party/Butterscotch/src/render_diagnostics.c"

static void check_record(const RD_CropRecord* record) {
    assert(record->singleBytes == record->changedBytes + record->gapBytes);
    assert(record->predictedBytes[0] == record->changedBytes);
    for (unsigned t = 0; t < RD_CROP_THRESHOLDS; ++t) {
        assert(record->predictedBytes[t] >= record->changedBytes);
        assert(record->predictedBytes[t] <= record->singleBytes);
        assert(record->predictedCalls[t] >= record->singleCalls);
        if (t) {
            assert(record->predictedBytes[t] >= record->predictedBytes[t - 1]);
            assert(record->predictedCalls[t] <= record->predictedCalls[t - 1]);
        }
    }
}

int main(int argc, char** argv) {
    if (argc == 2 && !strcmp(argv[1], "--check-disabled")) {
        openCropFrames(".", 1);
        assert(!cropFrames && !cropOutput);
        puts("crop metrics runtime opt-in guard PASS");
        return 0;
    }
    assert(argc == 3);
    uint8_t shadow[4096], source[8192], oldShadow[4096], oldSource[8192];
    memset(shadow, 0xa5, sizeof(shadow));
    memset(source, 0xa5, sizeof(source));
    // No active collector: even invalid input pointers must not be accessed.
    RD_CROP_UPLOAD(NULL, NULL, SIZE_MAX, SIZE_MAX, SIZE_MAX);
    frames = calloc(8, sizeof(*frames));
    assert(frames);
    limit = 8;
    output = fopen(argv[1], "w");
    assert(output);
    origin = rdNow();
    RD_beginFrame(100, 166, 0);
    RD_CROP_UPLOAD(NULL, NULL, SIZE_MAX, SIZE_MAX, SIZE_MAX);
    assert(!cropFrames && !cropOutput);
    RD_endFrame();

    // Activation is explicitly gated. The Python caller supplies the setting.
    const char* enabled = getenv("AM2R_CROP_GAPS");
    assert(enabled && !strcmp(enabled, "1"));
    openCropFrames(argv[2], limit);
    assert(cropFrames && cropOutput);
    RD_beginFrame(101, 166, 1);
    RD_CROP_UPLOAD(shadow, source, 8, 8, 2);
    RD_CropRecord* record = &cropFrames[used].records[0];
    assert(record->singleBytes == 0 && record->singleCalls == 0);
    check_record(record);
    RD_endFrame();

    // Dense two-row upload; padding is not sampled.
    memset(source, 0x17, 16);
    memset(source + 32, 0x61, 16);
    RD_beginFrame(102, 166, 2);
    RD_CROP_UPLOAD(shadow, source, 32, 16, 2);
    record = &cropFrames[used].records[0];
    assert(record->singleBytes == 32 && record->changedBytes == 32 && record->singleCalls == 2);
    assert(record->predictedCalls[0] == 2);
    check_record(record);
    RD_endFrame();

    // Alpha-only changes in separate pixels, with precisely16 bytes of gap.
    memset(source, 0xa5, sizeof(source));
    source[3] ^= 1; source[23] ^= 1;
    memcpy(oldSource, source, sizeof(source));
    memcpy(oldShadow, shadow, sizeof(shadow));
    RD_beginFrame(103, 166, 3);
    RD_CROP_UPLOAD(shadow, source, 32, 32, 1);
    record = &cropFrames[used].records[0];
    assert(record->singleBytes == 24 && record->changedBytes == 8 && record->gapBytes == 16);
    assert(record->predictedBytes[0] == 8 && record->predictedCalls[0] == 2);
    assert(record->predictedBytes[1] == 24 && record->predictedCalls[1] == 1);
    assert(!memcmp(source, oldSource, sizeof(source)) && !memcmp(shadow, oldShadow, sizeof(shadow)));
    check_record(record);
    RD_endFrame();

    // Tail pixel contains only three valid bytes. Misaligned pointers and
    // strided rows must not include adjacent padding in any byte prediction.
    memset(source, 0xa5, sizeof(source));
    source[1 + 1] ^= 1; source[1 + 18] ^= 1; source[1 + 33 + 18] ^= 1;
    RD_beginFrame(104, 166, 4);
    RD_CROP_UPLOAD(shadow + 1, source + 1, 33, 19, 2);
    record = &cropFrames[used].records[0];
    assert(record->singleBytes == 22 && record->changedBytes == 10 && record->gapBytes == 12);
    assert(record->predictedBytes[0] == 10 && record->predictedCalls[0] == 3);
    check_record(record);
    RD_endFrame();

    // Every threshold boundary, no source/shadow mutation; four records are
    // available per frame. A gap equal to the threshold is coalesced.
    RD_beginFrame(105, 166, 5);
    for (unsigned t = 1; t < RD_CROP_THRESHOLDS; ++t) {
        memset(source, 0xa5, sizeof(source));
        size_t gap = cropThresholds[t];
        source[0] ^= 1; source[4 + gap] ^= 1;
        RD_CROP_UPLOAD(shadow, source, gap + 8, gap + 8, 1);
        record = &cropFrames[used].records[t - 1];
        assert(record->changedBytes == 8 && record->singleBytes == gap + 8);
        assert(record->predictedBytes[t] == gap + 8 && record->predictedCalls[t] == 1);
        assert(record->predictedBytes[t - 1] == 8 && record->predictedCalls[t - 1] == 2);
        check_record(record);
    }
    // Over-limit calls are disclosed and never dereference their inputs.
    RD_CROP_UPLOAD(NULL, NULL, SIZE_MAX, SIZE_MAX, SIZE_MAX);
    assert(cropFrames[used].seen == 5 && cropFrames[used].count == 4 && cropFrames[used].dropped == 1);
    RD_endFrame();

    // Invalid geometry is a dropped observation, not an unchanged zero sample.
    RD_beginFrame(106, 166, 6);
    RD_CROP_UPLOAD(NULL, NULL, 0, 0, 0);
    RD_CROP_UPLOAD(shadow, source, 4, 8, 1);
    RD_CROP_UPLOAD(shadow, source, RD_CROP_MAX_ROW_BYTES + 1, RD_CROP_MAX_ROW_BYTES + 1, 1);
    RD_CROP_UPLOAD(shadow, source, 8, 8, RD_CROP_MAX_ROWS + 1);
    RD_CROP_UPLOAD(shadow, source, SIZE_MAX, 4, 2);
    RD_CROP_UPLOAD(shadow, source, RD_CROP_MAX_ROW_BYTES, RD_CROP_MAX_ROW_BYTES, RD_CROP_MAX_ROWS);
    assert(cropFrames[used].seen == 6 && cropFrames[used].count == 0 && cropFrames[used].dropped == 6);
    // Beginning again discards the incomplete frame's measurements.
    RD_beginFrame(107, 166, 7);
    assert(cropFrames[used].seen == 0 && cropFrames[used].count == 0 && cropFrames[used].dropped == 0);
    RD_endFrame();
    RD_beginFrame(108, 166, 8);
    RD_CROP_UPLOAD(shadow, source, 4, 8, 1);
    RD_endFrame();
    assert(!frames && !output && !current && !cropFrames && !cropOutput);

    // Existing output is not overwritten, even when a second pass is enabled.
    openCropFrames(argv[2], 1);
    assert(!cropFrames && !cropOutput);
    puts("crop metrics exact spans, thresholds, tails, alpha, immutable inputs, finite bounds and flush PASS");
    return 0;
}
