// SPDX-License-Identifier: GPL-3.0-or-later
// Read-only physical-memory dumper for focused AM2R FPGA-GPU diagnostics.

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

static int parseU32(const char *text, uint32_t *value)
{
    char *end = NULL;
    errno = 0;
    unsigned long parsed = strtoul(text, &end, 0);
    if (errno != 0 || end == text || *end != '\0' || parsed > UINT32_MAX)
        return -1;
    *value = (uint32_t)parsed;
    return 0;
}

int main(int argc, char **argv)
{
    if (argc != 4) {
        fprintf(stderr, "usage: %s PHYSICAL_ADDRESS BYTE_COUNT OUTPUT\n", argv[0]);
        return 2;
    }

    uint32_t physical = 0;
    uint32_t byteCount = 0;
    if (parseU32(argv[1], &physical) != 0 ||
        parseU32(argv[2], &byteCount) != 0 || byteCount == 0) {
        fprintf(stderr, "invalid address or byte count\n");
        return 2;
    }

    const long pageSize = sysconf(_SC_PAGESIZE);
    if (pageSize <= 0) {
        perror("sysconf");
        return 1;
    }
    const uint32_t pageMask = (uint32_t)pageSize - 1u;
    const uint32_t pageBase = physical & ~pageMask;
    const size_t pageOffset = physical - pageBase;
    const size_t mapBytes = pageOffset + byteCount;

    int memoryFd = open("/dev/mem", O_RDONLY | O_SYNC | O_CLOEXEC);
    if (memoryFd < 0) {
        perror("open /dev/mem");
        return 1;
    }
    const uint8_t *mapping = mmap(NULL, mapBytes, PROT_READ, MAP_SHARED,
                                  memoryFd, pageBase);
    if (mapping == MAP_FAILED) {
        perror("mmap");
        close(memoryFd);
        return 1;
    }

    int outputFd = open(argv[3], O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (outputFd < 0) {
        perror("open output");
        munmap((void *)mapping, mapBytes);
        close(memoryFd);
        return 1;
    }

    const uint8_t *source = mapping + pageOffset;
    size_t remaining = byteCount;
    while (remaining != 0) {
        ssize_t written = write(outputFd, source, remaining);
        if (written < 0) {
            if (errno == EINTR)
                continue;
            perror("write");
            close(outputFd);
            munmap((void *)mapping, mapBytes);
            close(memoryFd);
            return 1;
        }
        source += written;
        remaining -= (size_t)written;
    }

    if (fsync(outputFd) != 0) {
        perror("fsync");
        close(outputFd);
        munmap((void *)mapping, mapBytes);
        close(memoryFd);
        return 1;
    }
    close(outputFd);
    munmap((void *)mapping, mapBytes);
    close(memoryFd);

    printf("dumped address=%08" PRIx32 " bytes=%" PRIu32 " output=%s\n",
           physical, byteCount, argv[3]);
    return 0;
}
