// SPDX-License-Identifier: GPL-3.0-or-later
// Print the same IEEE CRC-32 used by the AM2R save-state metadata.

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static uint32_t update_crc32(uint32_t crc, const unsigned char *data, size_t size)
{
    crc = ~crc;
    while (size--) {
        crc ^= *data++;
        for (unsigned int bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ (UINT32_C(0xedb88320) &
                               (uint32_t)-(int32_t)(crc & 1u));
    }
    return ~crc;
}

int main(int argc, char **argv)
{
    if (argc != 2) {
        fprintf(stderr, "usage: %s FILE\n", argv[0]);
        return 2;
    }
    FILE *file = fopen(argv[1], "rb");
    if (!file) {
        fprintf(stderr, "%s: %s\n", argv[1], strerror(errno));
        return 1;
    }
    uint32_t crc = 0;
    unsigned char buffer[65536];
    for (;;) {
        size_t count = fread(buffer, 1, sizeof(buffer), file);
        crc = update_crc32(crc, buffer, count);
        if (count != sizeof(buffer)) {
            if (ferror(file)) {
                fprintf(stderr, "%s: read failed\n", argv[1]);
                fclose(file);
                return 1;
            }
            break;
        }
    }
    if (fclose(file) != 0) return 1;
    printf("%08x  %s\n", crc, argv[1]);
    return 0;
}
