#include <stdint.h>
#include <stdio.h>

#include "../../third_party/Butterscotch/src/sw_raster_bounds.h"

static int check(float firstEdge, float lastEdge, int32_t expectedFirst,
                 int32_t expectedLast)
{
    const int32_t first = swPixelCoverageEdge(firstEdge);
    const int32_t last = swPixelCoverageEdge(lastEdge);
    if (first != expectedFirst || last != expectedLast || last < first) {
        fprintf(stderr, "coverage %.6f..%.6f was %d..%d, expected %d..%d\n",
                firstEdge, lastEdge, first, last, expectedFirst, expectedLast);
        return 1;
    }
    return 0;
}

int main(void)
{
    int failed = 0;
    failed |= check(0.0f, 64.0f, 0, 64);
    failed |= check(0.00003f, 64.00003f, 0, 64);
    failed |= check(-0.00003f, 63.99997f, 0, 64);
    failed |= check(0.25f, 64.25f, 0, 64);
    failed |= check(0.75f, 64.75f, 1, 65);
    failed |= check(-64.00003f, -0.00003f, -64, 0);

    // The failing slot-2 command used to become y=0,height=13 and overwrite
    // the next tile with atlas row 600. Pixel-center coverage is 12 rows.
    failed |= check(0.00003f, 12.00003f, 0, 12);

    // Conservative early rejection agrees with the rasterizer's half-open
    // scissor convention and handles mirrored (negative-scale) bounds.
    if (!swAxisBoundsOutsideScissor(-32.0f, 8.0f, 0.0f, 24.0f,
                                     0, 0, 320, 240)) failed = 1;
    if (!swAxisBoundsOutsideScissor(320.0f, 8.0f, 352.0f, 24.0f,
                                     0, 0, 320, 240)) failed = 1;
    if (!swAxisBoundsOutsideScissor(352.0f, 8.0f, 320.0f, 24.0f,
                                     0, 0, 320, 240)) failed = 1;
    if (swAxisBoundsOutsideScissor(-0.25f, 8.0f, 0.75f, 24.0f,
                                    0, 0, 320, 240)) failed = 1;
    if (swAxisBoundsOutsideScissor(319.25f, 8.0f, 320.25f, 24.0f,
                                    0, 0, 320, 240)) failed = 1;

    if (failed)
        return 1;

    puts("AM2R axis coverage test passed");
    return 0;
}
