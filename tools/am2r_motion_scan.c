#define _GNU_SOURCE
#define _FILE_OFFSET_BITS 64
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define G_RUNNER_ADDRESS ((uintptr_t)0x001dd8e0)
#define RUNNER_INSTANCES_OFFSET ((uintptr_t)0x20)
#define RUNNER_FRAME_OFFSET ((uintptr_t)0xe8)
#define RUNNER_ROOM_INDEX_OFFSET ((uintptr_t)0x18)
#define STBDS_HEADER_SIZE ((uintptr_t)16)

static int read_exact(int fd, uintptr_t address, void *buffer, size_t size)
{
    ssize_t count = pread(fd, buffer, size, (off_t)address);
    return count == (ssize_t)size ? 0 : -1;
}

static int list_instances(int fd, uintptr_t g_runner_address)
{
    uint32_t runner, instances, length;
    int32_t room_index;
    if (read_exact(fd, g_runner_address, &runner, sizeof(runner)) || !runner ||
        read_exact(fd, runner + RUNNER_ROOM_INDEX_OFFSET, &room_index,
                   sizeof(room_index)) ||
        read_exact(fd, runner + RUNNER_INSTANCES_OFFSET, &instances,
                   sizeof(instances)) || !instances ||
        read_exact(fd, instances - STBDS_HEADER_SIZE, &length, sizeof(length))) {
        perror("read runner registry");
        return 1;
    }
    printf("MOTION_RUNNER runner=0x%08" PRIx32
           " frame_address=0x%08" PRIx32 " room=%" PRId32
           " instances=0x%08" PRIx32
           " length=%" PRIu32 "\n",
           runner, runner + (uint32_t)RUNNER_FRAME_OFFSET, room_index,
           instances, length);
    if (length > 100000) {
        fprintf(stderr, "implausible instance count: %" PRIu32 "\n", length);
        return 1;
    }
    for (uint32_t index = 0; index < length; ++index) {
        uint32_t instance;
        unsigned char fields[0x38];
        if (read_exact(fd, instances + index * sizeof(instance), &instance,
                       sizeof(instance)) || !instance ||
            read_exact(fd, instance, fields, sizeof(fields))) continue;
        uint32_t instance_id;
        int32_t object_index;
        float x, y;
        memcpy(&instance_id, fields, sizeof(instance_id));
        memcpy(&object_index, fields + 4, sizeof(object_index));
        memcpy(&x, fields + 0x1c, sizeof(x));
        memcpy(&y, fields + 0x20, sizeof(y));
        if (!fields[0x36] || fields[0x37]) continue;
        printf("MOTION_INSTANCE registry=%" PRIu32 " instance=0x%08" PRIx32
               " id=%" PRIu32 " object=%" PRId32 " x=%.9g y=%.9g\n",
               index, instance, instance_id, object_index, x, y);
    }
    return 0;
}

static int scan_range(int fd, uintptr_t start, uintptr_t end,
                      float wanted_x, float wanted_y, const char *label)
{
    const size_t chunk_size = 1u << 20;
    unsigned char *buffer = malloc(chunk_size + sizeof(float) * 2);
    if (!buffer) return -1;
    uintptr_t address = start;
    size_t carry = 0;
    while (address < end) {
        size_t request = (size_t)(end - address);
        if (request > chunk_size) request = chunk_size;
        ssize_t count = pread(fd, buffer + carry, request, (off_t)address);
        if (count <= 0) {
            address += request;
            carry = 0;
            continue;
        }
        size_t available = carry + (size_t)count;
        uintptr_t base = address - carry;
        for (size_t offset = 0; offset + sizeof(float) * 2 <= available;
             offset += sizeof(float)) {
            float x, y;
            memcpy(&x, buffer + offset, sizeof(x));
            memcpy(&y, buffer + offset + sizeof(float), sizeof(y));
            if (x == wanted_x && y == wanted_y && base + offset >= 28) {
                printf("MOTION_CANDIDATE label=%s instance=0x%" PRIxPTR
                       " x=0x%" PRIxPTR " y=0x%" PRIxPTR "\n",
                       label, base + offset - 28, base + offset,
                       base + offset + sizeof(float));
            }
        }
        carry = available < sizeof(float) ? available : sizeof(float);
        memmove(buffer, buffer + available - carry, carry);
        address += (uintptr_t)count;
    }
    free(buffer);
    return 0;
}

int main(int argc, char **argv)
{
    if (argc != 3 && argc != 4 && argc != 6) {
        fprintf(stderr,
                "usage: %s pid --instances [g-runner-address]\n"
                "       %s pid character-x character-y camera-x camera-y\n",
                argv[0], argv[0]);
        return 2;
    }
    char *end = NULL;
    long pid = strtol(argv[1], &end, 0);
    if (!end || *end || pid <= 0) return 2;
    float character_x = 0, character_y = 0, camera_x = 0, camera_y = 0;
    if (argc == 6) {
        character_x = strtof(argv[2], &end);
        if (!end || *end) return 2;
        character_y = strtof(argv[3], &end);
        if (!end || *end) return 2;
        camera_x = strtof(argv[4], &end);
        if (!end || *end) return 2;
        camera_y = strtof(argv[5], &end);
        if (!end || *end) return 2;
    } else if (strcmp(argv[2], "--instances")) {
        return 2;
    }

    char maps_path[64], memory_path[64];
    snprintf(maps_path, sizeof(maps_path), "/proc/%ld/maps", pid);
    snprintf(memory_path, sizeof(memory_path), "/proc/%ld/mem", pid);
    FILE *maps = fopen(maps_path, "r");
    int memory = open(memory_path, O_RDONLY | O_CLOEXEC);
    if (!maps || memory < 0) {
        perror(!maps ? maps_path : memory_path);
        if (maps) fclose(maps);
        if (memory >= 0) close(memory);
        return 1;
    }

    if (argc == 3 || argc == 4) {
        uintptr_t g_runner_address = G_RUNNER_ADDRESS;
        if (argc == 4) {
            g_runner_address = (uintptr_t)strtoull(argv[3], &end, 0);
            if (!end || *end || !g_runner_address) {
                fclose(maps);
                close(memory);
                return 2;
            }
        }
        int result = list_instances(memory, g_runner_address);
        fclose(maps);
        close(memory);
        return result;
    }

    char line[512];
    while (fgets(line, sizeof(line), maps)) {
        uintptr_t start, end_address;
        char permissions[5] = {0};
        if (sscanf(line, "%" SCNxPTR "-%" SCNxPTR " %4s",
                   &start, &end_address, permissions) != 3 ||
            permissions[0] != 'r' || permissions[1] != 'w') continue;
        if (scan_range(memory, start, end_address, character_x, character_y,
                       "character") ||
            scan_range(memory, start, end_address, camera_x, camera_y,
                       "camera")) {
            fclose(maps);
            close(memory);
            return 1;
        }
    }
    fclose(maps);
    close(memory);
    return 0;
}
