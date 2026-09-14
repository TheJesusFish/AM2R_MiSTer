// SPDX-License-Identifier: GPL-3.0-or-later
// Sample user-space instruction pointers from a running ARM process without ptrace.

#define _GNU_SOURCE

#include <errno.h>
#include <linux/perf_event.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

typedef struct {
    uint64_t ip;
    uint64_t caller;
    uint64_t count;
} IpCount;

static int compare_counts(const void *lhs, const void *rhs)
{
    const IpCount *a = (const IpCount *)lhs;
    const IpCount *b = (const IpCount *)rhs;
    if (a->count < b->count) return 1;
    if (a->count > b->count) return -1;
    if (a->ip < b->ip) return -1;
    if (a->ip > b->ip) return 1;
    return a->caller < b->caller ? -1 : a->caller != b->caller;
}

static void account_ip(IpCount **counts, size_t *used, size_t *capacity,
                       uint64_t ip, uint64_t caller)
{
    for (size_t i = 0; i < *used; ++i) {
        if ((*counts)[i].ip == ip && (*counts)[i].caller == caller) {
            (*counts)[i].count++;
            return;
        }
    }
    if (*used == *capacity) {
        size_t next = *capacity ? *capacity * 2u : 256u;
        IpCount *grown = (IpCount *)realloc(*counts, next * sizeof(*grown));
        if (grown == NULL) {
            perror("realloc");
            exit(1);
        }
        *counts = grown;
        *capacity = next;
    }
    (*counts)[*used].ip = ip;
    (*counts)[*used].caller = caller;
    (*counts)[*used].count = 1;
    (*used)++;
}

static void copy_ring(const uint8_t *data, size_t data_size, uint64_t offset,
                      void *destination, size_t bytes)
{
    size_t start = (size_t)(offset & (data_size - 1u));
    size_t first = bytes < data_size - start ? bytes : data_size - start;
    memcpy(destination, data + start, first);
    if (first < bytes)
        memcpy((uint8_t *)destination + first, data, bytes - first);
}

static int perf_event_open(struct perf_event_attr *attr, pid_t pid)
{
    return (int)syscall(__NR_perf_event_open, attr, pid, -1, -1, 0);
}

static uint64_t monotonic_millis(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return 0;
    return (uint64_t)now.tv_sec * 1000u + (uint64_t)now.tv_nsec / 1000000u;
}

int main(int argc, char **argv)
{
    if (argc < 2 || argc > 4) {
        fprintf(stderr, "usage: %s PID [seconds=10] [sample-hz=997]\n", argv[0]);
        return 2;
    }
    char *end = NULL;
    long pid_value = strtol(argv[1], &end, 10);
    if (*argv[1] == '\0' || *end != '\0' || pid_value <= 0) return 2;
    unsigned seconds = argc >= 3 ? (unsigned)strtoul(argv[2], &end, 10) : 10u;
    if ((argc >= 3 && (*argv[2] == '\0' || *end != '\0')) || seconds == 0 || seconds > 120)
        return 2;
    unsigned frequency = argc >= 4 ? (unsigned)strtoul(argv[3], &end, 10) : 997u;
    if ((argc >= 4 && (*argv[3] == '\0' || *end != '\0')) || frequency == 0 || frequency > 10000)
        return 2;

    struct perf_event_attr attr;
    memset(&attr, 0, sizeof(attr));
    attr.type = PERF_TYPE_SOFTWARE;
    attr.size = sizeof(attr);
    attr.config = PERF_COUNT_SW_CPU_CLOCK;
    attr.sample_type = PERF_SAMPLE_IP | PERF_SAMPLE_CALLCHAIN;
    attr.freq = 1;
    attr.sample_freq = frequency;
    attr.disabled = 1;
    attr.exclude_kernel = 1;
    attr.exclude_hv = 1;
    attr.wakeup_events = 1;

    int fd = perf_event_open(&attr, (pid_t)pid_value);
    if (fd < 0) {
        fprintf(stderr, "perf_event_open: %s\n", strerror(errno));
        return 1;
    }

    long page_size = sysconf(_SC_PAGESIZE);
    const size_t data_pages = 32u;
    size_t mapping_bytes = (size_t)page_size * (1u + data_pages);
    struct perf_event_mmap_page *meta = mmap(NULL, mapping_bytes,
        PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (meta == MAP_FAILED) {
        perror("mmap perf ring");
        close(fd);
        return 1;
    }
    uint8_t *data = (uint8_t *)meta + page_size;
    size_t data_size = (size_t)page_size * data_pages;
    IpCount *counts = NULL;
    size_t used = 0, capacity = 0;
    uint64_t total_samples = 0, lost_samples = 0;

    if (ioctl(fd, PERF_EVENT_IOC_RESET, 0) != 0 ||
        ioctl(fd, PERF_EVENT_IOC_ENABLE, 0) != 0) {
        perror("enable perf event");
        munmap(meta, mapping_bytes);
        close(fd);
        return 1;
    }

    uint64_t deadline = monotonic_millis() + (uint64_t)seconds * 1000u;
    struct pollfd poll_fd = { .fd = fd, .events = POLLIN };
    while (monotonic_millis() < deadline) {
        poll(&poll_fd, 1, 100);
        __sync_synchronize();
        uint64_t head = meta->data_head;
        uint64_t tail = meta->data_tail;
        while (tail < head) {
            struct perf_event_header header;
            copy_ring(data, data_size, tail, &header, sizeof(header));
            if (header.size < sizeof(header) || header.size > data_size) {
                fprintf(stderr, "invalid perf record size %u\n", header.size);
                ioctl(fd, PERF_EVENT_IOC_DISABLE, 0);
                munmap(meta, mapping_bytes);
                close(fd);
                free(counts);
                return 1;
            }
            if (header.type == PERF_RECORD_SAMPLE) {
                uint64_t ip;
                copy_ring(data, data_size, tail + sizeof(header), &ip, sizeof(ip));
                uint64_t nr = 0;
                copy_ring(data, data_size, tail + sizeof(header) + sizeof(ip),
                          &nr, sizeof(nr));
                uint64_t caller = 0;
                for (uint64_t i = 0; i < nr; ++i) {
                    uint64_t frame = 0;
                    copy_ring(data, data_size,
                              tail + sizeof(header) + sizeof(ip) + sizeof(nr) +
                                  i * sizeof(frame),
                              &frame, sizeof(frame));
                    if ((int64_t)frame >= 0 && frame != ip) {
                        caller = frame;
                        break;
                    }
                }
                account_ip(&counts, &used, &capacity, ip, caller);
                total_samples++;
            } else if (header.type == PERF_RECORD_LOST) {
                struct { uint64_t id, lost; } lost;
                copy_ring(data, data_size, tail + sizeof(header), &lost, sizeof(lost));
                lost_samples += lost.lost;
            }
            tail += header.size;
        }
        meta->data_tail = tail;
    }
    ioctl(fd, PERF_EVENT_IOC_DISABLE, 0);

    qsort(counts, used, sizeof(*counts), compare_counts);
    printf("samples=%llu lost=%llu unique=%zu seconds=%u hz=%u\n",
           (unsigned long long)total_samples,
           (unsigned long long)lost_samples, used, seconds, frequency);
    size_t show = used < 100u ? used : 100u;
    for (size_t i = 0; i < show; ++i)
        printf("0x%08llx caller=0x%08llx %llu %.3f%%\n",
               (unsigned long long)counts[i].ip,
               (unsigned long long)counts[i].caller,
               (unsigned long long)counts[i].count,
               total_samples ? 100.0 * counts[i].count / total_samples : 0.0);

    free(counts);
    munmap(meta, mapping_bytes);
    close(fd);
    return 0;
}
