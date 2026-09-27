// SPDX-License-Identifier: GPL-3.0-or-later
// Exercise the actual bounded collector, including automatic scope unwinding.
#include <fcntl.h>
#ifdef _WIN32
// No request-file I/O is used by this portable collector test. Linux hardware
// tests separately exercise O_NOFOLLOW/O_EXCL request handling.
#define O_CLOEXEC 0
#define O_NOFOLLOW 0
#define O_NONBLOCK 0
#endif
#include "../../third_party/Butterscotch/src/render_diagnostics.c"
#include <assert.h>

int main(int argc, char** argv) {
    assert(argc == 2);
    frames = calloc(2, sizeof(*frames));
    assert(frames);
    limit = 2;
    output = fopen(argv[1], "w");
    assert(output);
    origin = rdNow();
    RD_beginFrame(101, 160, 8);
    {
        RD_SCOPE(RD_STEP, -1, 0);
        {
            RD_SCOPE(RD_UPLOAD, -1, 0);
            RD_UPLOAD_BYTES(40);
            {
                RD_SCOPE(RD_UPLOAD, -1, 0);
                RD_UPLOAD_BYTES(60);
            }
        }
        {
            RD_SCOPE(RD_USER_AXIS, 7, 1024);
            {
                RD_SCOPE(RD_SUBTRACT_SCALED, 7, 1024);
            }
        }
    }
    // Backend handle scopes share the bounded collector and must all retain
    // their labels/handle/area without new files or a separate unbounded log.
    for (int operation=RD_SURFACE_FLUSH;operation<RD_OPERATION_COUNT;operation++) {
        RD_SCOPE(operation, 0x123405, 131072);
    }
    RD_capturePerturbation();
    RD_endFrame();
    RD_beginFrame(102, 160, 16);
    for (int i = 0; i < 80; ++i) {
        RD_SCOPE(RD_USER_CLEAR, i, 32);
    }
    RD_endFrame();
    assert(!frames && !output && !current);
    for (int i = 0; i < RD_OPERATION_COUNT; ++i) assert(!depths[i]);
    puts("render collector bounds, nesting, cleanup, bytes and flush pass");
    return 0;
}
