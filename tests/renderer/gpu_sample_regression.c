// SPDX-License-Identifier: GPL-3.0-or-later
// Runs the production snapshot reader against an in-memory mailbox only.
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static volatile uint32_t mailbox[32];
static unsigned barriers, mutate_at, mutate_word;
static void test_barrier(void) {
    if (++barriers == mutate_at) mailbox[mutate_word] ^= 1u;
}
#define __sync_synchronize test_barrier
#include "gpu_sample_under_test.inc"

static void reset_mailbox(unsigned buffer, unsigned underflow) {
    memset((void*)mailbox, 0, sizeof(mailbox));
    barriers = mutate_at = mutate_word = 0;
    mailbox[0] = GPU_MAGIC;
    mailbox[1] = mailbox[6] = 19;
    mailbox[2] = 0x23fe0000u;
    mailbox[3] = 0x1234004du;
    mailbox[7] = (buffer << 30) | (underflow << 29) | CYCLE_MASK;
    mailbox[16] = 103;
    mailbox[17] = VBLANK_MAGIC;
    mailbox[18] = 101;
    mailbox[19] = 102;
}

static void expect_rejected(uint32_t previous) {
    Sample sample, untouched;
    memset(&sample, 0x5a, sizeof(sample));
    untouched = sample;
    assert(!snapshot(mailbox, previous, &sample));
    assert(!memcmp(&sample, &untouched, sizeof(sample)));
}

int main(void) {
    for (unsigned buffer = 0; buffer < 4; ++buffer) {
        for (unsigned underflow = 0; underflow < 2; ++underflow) {
            reset_mailbox(buffer, underflow);
            Sample sample = {0};
            if (!snapshot(mailbox, 18, &sample)) {
                fprintf(stderr, "FAIL: legal buffer%u was rejected\n", buffer);
                return 1;
            }
            assert(sample.sequence == 19 && sample.commands == 77);
            assert(sample.command_phys == 0x23fe0000u);
            assert((sample.completion >> 30) == buffer);
            assert(((sample.completion >> 29) & 1u) == underflow);
            assert((sample.completion & CYCLE_MASK) == CYCLE_MASK);
            assert(sample.vblank_valid && sample.vblank == 103);
            assert(sample.scanout_frame == 101 && sample.native_frame == 102);
        }
    }
    reset_mailbox(3, 0); mailbox[17] = 0;
    Sample sample = {0};
    assert(snapshot(mailbox, 18, &sample) && !sample.vblank_valid);
    reset_mailbox(3, 0); expect_rejected(19);
    reset_mailbox(3, 0); mailbox[0] = 0; expect_rejected(18);
    reset_mailbox(3, 0); mailbox[1] = 20; expect_rejected(18);
    reset_mailbox(3, 0); mailbox[6] = 0; expect_rejected(18);
    // Change each coherence-checked field after its sampled value was read.
    const unsigned raced_words[] = {0, 1, 2, 3, 6, 7};
    for (unsigned i = 0; i < sizeof(raced_words) / sizeof(raced_words[0]); ++i) {
        reset_mailbox(3, 0); mutate_at = 2; mutate_word = raced_words[i];
        expect_rejected(18);
    }
    reset_mailbox(3, 0); mutate_at = 1; mutate_word = 6;
    expect_rejected(18);
    puts("GPU sampler: all four buffers, independent underflow/cycle bits, stale/incomplete jobs and raced mailbox fields passed");
    return 0;
}
