// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef AM2R_SHUTDOWN_H
#define AM2R_SHUTDOWN_H

#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <string.h>
#ifndef AM2R_SHUTDOWN_TEST_PLATFORM
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#endif

// Never reap here: this gate runs inside Main's input/menu call stack, while
// run_child owns waitpid and its reset/save-state bookkeeping.
static inline bool am2r_wait_child_stopped(pid_t child, unsigned timeoutMs,
                                         int *error)
{
    if (error) *error = 0;
    if (child <= 0) return true;
    if (kill(child, SIGTERM) < 0 && errno != ESRCH) {
        if (error) *error = errno;
        return false;
    }
    struct timespec started = {};
    if (clock_gettime(CLOCK_MONOTONIC, &started) != 0) {
        if (error) *error = errno;
        return false;
    }
    const uint64_t deadline = (uint64_t)started.tv_sec * 1000000000ull +
        started.tv_nsec + (uint64_t)timeoutMs * 1000000ull;
    for (;;) {
        siginfo_t result = {};
        if (waitid(P_PID, (id_t)child, &result, WEXITED | WNOHANG | WNOWAIT) == 0) {
            if (result.si_pid == child) return true;
        } else if (errno != EINTR) {
            // ECHILD is not proof that a PID can no longer submit commands.
            if (error) *error = errno;
            return false;
        }
        struct timespec now = {};
        if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) {
            if (error) *error = errno;
            return false;
        }
        if ((uint64_t)now.tv_sec * 1000000000ull + now.tv_nsec >= deadline) {
            if (error) *error = ETIMEDOUT;
            return false;
        }
        usleep(1000);
    }
}

// Call only after the AM2R child is known stopped, with an uncached /dev/mem
// mapping. A dead process alone is insufficient: the FPGA may still be busy.
static inline bool am2r_gpu_mailbox_idle(volatile const uint32_t *control,
                                        uint32_t *submitted, uint32_t *completed)
{
    __sync_synchronize();
    const uint32_t first = control[1];
    const uint32_t done = control[6];
    __sync_synchronize();
    const uint32_t last = control[1];
    if (submitted) *submitted = last;
    if (completed) *completed = done;
    return first == last && last == done;
}

static inline bool am2r_gpu_mailbox_disarm(volatile uint32_t *control,
                                          uint32_t *submitted, uint32_t *completed)
{
    if (!am2r_gpu_mailbox_idle(control, submitted, completed)) return false;
    // DDR survives core loading, but the FPGA's last_sequence does not. An
    // old valid job would otherwise replay immediately on reset, racing the
    // next runner's command-bank initialization. Keep the sequence history:
    // an HPS-only reset must submit completed+1 rather than reuse sequence 1.
    control[0] = 0;
    __sync_synchronize();
    return control[0] == 0 &&
           am2r_gpu_mailbox_idle(control, submitted, completed);
}

#endif
