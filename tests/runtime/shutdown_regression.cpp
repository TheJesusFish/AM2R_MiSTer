// Host: deterministic production-helper failure paths. ARM/Linux: real fork,
// signal delivery, atomic-save completion, WNOWAIT/reaping and reserved-mailbox
// ordering. This does not claim an FPGA native-reader drain test.
#undef NDEBUG
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>

#if defined(_WIN32)
#define AM2R_SHUTDOWN_TEST_PLATFORM
#define CLOCK_MONOTONIC 1
#define P_PID 1
#define WEXITED 4
#define WNOHANG 1
#define WNOWAIT 0x1000000
typedef unsigned id_t;
typedef int pid_t;
struct timespec { long tv_sec; long tv_nsec; };
struct siginfo_t { int si_pid; };
static uint64_t nowNs;
static unsigned exitMs, kills, waits, failKill, failWait, interrupted;
static int kill(pid_t pid, int signalNumber) {
    assert(pid == 17 && signalNumber == SIGTERM);
    ++kills;
    if (failKill) { errno = (int)failKill; return -1; }
    return 0;
}
static int waitid(int selector, id_t pid, siginfo_t *out, int flags) {
    assert(selector == P_PID && pid == 17);
    assert(flags == (WEXITED | WNOHANG | WNOWAIT));
    ++waits;
    if (interrupted) { --interrupted; errno = EINTR; return -1; }
    if (failWait) { errno = (int)failWait; return -1; }
    out->si_pid = nowNs >= (uint64_t)exitMs * 1000000ull ? 17 : 0;
    return 0;
}
static int clock_gettime(int clock, timespec *out) {
    assert(clock == CLOCK_MONOTONIC);
    out->tv_sec = (long)(nowNs / 1000000000ull);
    out->tv_nsec = (long)(nowNs % 1000000000ull);
    return 0;
}
static int usleep(unsigned amount) { nowNs += amount * 1000ull; return 0; }
#else
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include "../../third_party/Butterscotch/src/am2r_shutdown_signal.h"
#endif

#include "../../src/hps-wrapper/am2r_shutdown.h"

static void mailboxCases() {
    uint32_t words[8] = {};
    uint32_t submitted = 77, completed = 88;
    assert(am2r_gpu_mailbox_idle(words, &submitted, &completed));
    assert(submitted == 0 && completed == 0);
    words[1] = 1;
    words[0] = 0x50473241;
    uint32_t busyCopy[8];
    memcpy(busyCopy, words, sizeof(words));
    assert(!am2r_gpu_mailbox_disarm(words, nullptr, nullptr));
    assert(memcmp(busyCopy, words, sizeof(words)) == 0);
    assert(!am2r_gpu_mailbox_idle(words, &submitted, &completed));
    assert(submitted == 1 && completed == 0);
    words[6] = 1;
    assert(am2r_gpu_mailbox_idle(words, &submitted, &completed));
    uint32_t original[8];
    memcpy(original, words, sizeof(words));
    assert(am2r_gpu_mailbox_idle(words, nullptr, nullptr));
    assert(memcmp(original, words, sizeof(words)) == 0);
    assert(am2r_gpu_mailbox_disarm(words, &submitted, &completed));
    assert(words[0] == 0 && submitted == 1 && completed == 1);
    assert(memcmp(original + 1, words + 1, sizeof(words) - 4) == 0);
    // HPS-only reset retains completion=1: the next runner must start at 2,
    // not reuse 1 (which the existing FPGA would already consider consumed).
    uint32_t next = words[6] + 1;
    if (!next) next = 1;
    assert(next == 2);
    words[1] = 2;
    assert(!am2r_gpu_mailbox_idle(words, nullptr, nullptr));
    words[6] = 2;
    assert(am2r_gpu_mailbox_idle(words, nullptr, nullptr));
}

int main() {
    mailboxCases();
    int error = 99;
    assert(am2r_wait_child_stopped(-1, 1, &error) && error == 0);
#if defined(_WIN32)
    exitMs = 3;
    assert(am2r_wait_child_stopped(17, 5, &error));
    assert(kills == 1 && waits == 4 && nowNs == 3000000ull);
    nowNs = 0; exitMs = 99;
    assert(!am2r_wait_child_stopped(17, 2, &error) && error == ETIMEDOUT);
    assert(nowNs == 2000000ull && kills == 2);
    failKill = EPERM;
    assert(!am2r_wait_child_stopped(17, 2, &error) && error == EPERM);
    failKill = 0; failWait = ECHILD;
    assert(!am2r_wait_child_stopped(17, 2, &error) && error == ECHILD);
    failWait = 0; failKill = ESRCH; exitMs = 0;
    assert(am2r_wait_child_stopped(17, 2, &error));
    failKill = 0; interrupted = 1;
    assert(am2r_wait_child_stopped(17, 2, &error));
    puts("PASS: shutdown host contracts (timeouts, no force-kill, nonreaping wait, mailbox)");
#else
    char folder[] = "/tmp/am2r-shutdown-test-XXXXXX";
    assert(mkdtemp(folder));
    char saved[256], temporary[256];
    snprintf(saved, sizeof(saved), "%s/slot.fast", folder);
    snprintf(temporary, sizeof(temporary), "%s/slot.fast.new", folder);
    int file = open(saved, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    assert(file >= 0 && write(file, "old", 3) == 3 && close(file) == 0);
    volatile uint32_t *mailbox = (volatile uint32_t *)mmap(nullptr, 4096,
        PROT_READ | PROT_WRITE, MAP_SHARED | MAP_ANONYMOUS, -1, 0);
    assert(mailbox != MAP_FAILED);
    int ready[2];
    assert(pipe(ready) == 0);
    pid_t child = fork();
    assert(child >= 0);
    if (!child) {
        close(ready[0]);
        assert(am2rShutdownSignalInstall());
        mailbox[1] = 41;
        int output = open(temporary, O_WRONLY | O_CREAT | O_TRUNC, 0600);
        assert(output >= 0 && write(output, "new-", 4) == 4);
        assert(write(ready[1], "R", 1) == 1);
        // Simulated synchronous save transaction finishes even after TERM.
        for (int step = 0; step < 20; ++step) usleep(1000);
        assert(am2rShutdownSignal == SIGTERM);
        assert(write(output, "complete", 8) == 8);
        assert(fsync(output) == 0 && close(output) == 0);
        assert(rename(temporary, saved) == 0);
        // This stands in for the real renderer/platform completion barrier.
        __sync_synchronize();
        mailbox[6] = 41;
        __sync_synchronize();
        _exit(0);
    }
    close(ready[1]);
    char marker;
    assert(read(ready[0], &marker, 1) == 1);
    close(ready[0]);
    assert(am2r_wait_child_stopped(child, 1000, &error));
    assert(am2r_gpu_mailbox_idle(mailbox, nullptr, nullptr));
    assert(am2r_gpu_mailbox_disarm(mailbox, nullptr, nullptr));
    assert(mailbox[0] == 0 && mailbox[1] == 41 && mailbox[6] == 41);
    int status = 0;
    assert(waitpid(child, &status, WNOHANG) == child);
    assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    file = open(saved, O_RDONLY);
    char content[16] = {};
    assert(file >= 0 && read(file, content, sizeof(content)) == 12);
    assert(!strcmp(content, "new-complete") && close(file) == 0);
    assert(access(temporary, F_OK) < 0);

    // A busy/stuck worker is rejected without SIGKILL; then release it and
    // prove that a later retry succeeds and the owner can still reap it.
    assert(pipe(ready) == 0);
    child = fork();
    assert(child >= 0);
    if (!child) {
        close(ready[0]);
        assert(am2rShutdownSignalInstall());
        assert(write(ready[1], "R", 1) == 1);
        for (int step = 0; step < 100; ++step) usleep(1000);
        _exit(am2rShutdownSignal == SIGTERM ? 0 : 1);
    }
    close(ready[1]);
    assert(read(ready[0], &marker, 1) == 1);
    close(ready[0]);
    assert(!am2r_wait_child_stopped(child, 2, &error) && error == ETIMEDOUT);
    assert(kill(child, 0) == 0);
    assert(am2r_wait_child_stopped(child, 1000, &error));
    assert(waitpid(child, &status, WNOHANG) == child);
    assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    assert(munmap((void *)mailbox, 4096) == 0);
    assert(unlink(saved) == 0 && rmdir(folder) == 0);
    puts("PASS: shutdown real POSIX child, TERM-safe save, bounded rejection, retry/reap");
#endif
    return 0;
}
