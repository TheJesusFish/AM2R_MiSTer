#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv) {
    if (argc != 4 || strlen(argv[2]) != strlen(argv[3])) {
        fprintf(stderr, "usage: %s FILE OLD_PATH SAME_LENGTH_NEW_PATH\n", argv[0]);
        return 2;
    }

    FILE *file = fopen(argv[1], "rb+");
    if (file == NULL) {
        fprintf(stderr, "open %s: %s\n", argv[1], strerror(errno));
        return 1;
    }
    if (fseek(file, 0, SEEK_END) != 0) return 1;
    long length = ftell(file);
    if (length < 0 || fseek(file, 0, SEEK_SET) != 0) return 1;

    uint8_t *data = malloc((size_t)length);
    if (data == NULL || fread(data, 1, (size_t)length, file) != (size_t)length) {
        fprintf(stderr, "read %s failed\n", argv[1]);
        return 1;
    }

    const size_t path_length = strlen(argv[2]);
    unsigned replacements = 0;
    for (size_t i = 0; i + path_length <= (size_t)length; ++i) {
        if (memcmp(data + i, argv[2], path_length) == 0) {
            memcpy(data + i, argv[3], path_length);
            ++replacements;
            i += path_length - 1;
        }
    }

    if (fseek(file, 0, SEEK_SET) != 0 ||
        fwrite(data, 1, (size_t)length, file) != (size_t)length ||
        fflush(file) != 0) {
        fprintf(stderr, "write %s failed\n", argv[1]);
        return 1;
    }
    free(data);
    fclose(file);
    printf("replacements=%u\n", replacements);
    return replacements == 0 ? 3 : 0;
}
