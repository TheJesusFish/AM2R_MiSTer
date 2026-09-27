#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

static int fault;
static unsigned seeks;
static long proofTell(FILE* file) { return fault == 1 ? -1 : ftell(file); }
static int proofSeek(FILE* file, long position, int whence) {
    ++seeks;
    if ((fault == 2 && seeks == 1) || (fault == 3 && seeks == 2)) return -1;
    return fseek(file, position, whence);
}
static size_t proofRead(void* data, size_t size, size_t count, FILE* file) {
    return fault == 4 ? 0 : fread(data, size, count, file);
}
#define ftell proofTell
#define fseek proofSeek
#define fread proofRead
#include "am2r_data_proof.h"
#undef ftell
#undef fseek
#undef fread
#undef assert
#define assert(condition) do { if (!(condition)) { \
    fprintf(stderr, "FAILED %s:%d: %s\n", __FILE__, __LINE__, #condition); abort(); \
} } while (0)

int main(int argc, char** argv) {
    assert(argc >= 3);
    FILE* file = fopen(argv[2], "rb");
    assert(file != NULL);
    if (strcmp(argv[1], "hash") == 0) {
        size_t chunk = argc > 3 ? (size_t)strtoul(argv[3], NULL, 10) : 65536;
        assert(chunk > 0 && chunk <= 65536);
        uint8_t buffer[65536], digest[32];
        Am2rProofSha256 hash;
        am2rProofSha256Init(&hash);
        for (;;) {
            size_t size = fread(buffer, 1, chunk, file);
            if (size == 0) break;
            am2rProofSha256Update(&hash, buffer, size);
            am2rProofSha256Update(&hash, NULL, 0);
        }
        assert(!ferror(file));
        am2rProofSha256Finish(&hash, digest);
        for (unsigned i = 0; i < sizeof(digest); ++i) printf("%02x", digest[i]);
        puts("");
    } else if (strcmp(argv[1], "proof") == 0) {
        assert(fseek(file, 0, SEEK_END) == 0);
        long length = ftell(file);
        assert(length >= 0 && fseek(file, 0, SEEK_SET) == 0);
        size_t size = (size_t)length;
        uint8_t* bytes = (uint8_t*)malloc(size ? size : 1);
        assert(bytes != NULL && fread(bytes, 1, size, file) == size);
        assert(fseek(file, 13, SEEK_SET) == 0);
        bool memory = am2rDataProofMatches(bytes, size, NULL);
        bool stream = am2rDataProofMatches(NULL, size, file);
        assert(ftell(file) == 13 && memory == stream);
        assert(!am2rDataProofMatches(bytes, size + 1, NULL));
        if (size) assert(!am2rDataProofMatches(bytes, size - 1, NULL));
        assert(!am2rDataProofMatches(NULL, AM2R_11_DATA_PROOF_SIZE, NULL));
        if (memory) {
            bytes[0] ^= 1;
            assert(!am2rDataProofMatches(bytes, size, NULL));
            bytes[0] ^= 1; bytes[size-1] ^= 1;
            assert(!am2rDataProofMatches(bytes, size, NULL));
        }
        for (fault = 1; fault <= 4; ++fault) {
            seeks = 0;
            assert(!am2rDataProofMatches(NULL, AM2R_11_DATA_PROOF_SIZE, file));
            assert(fseek(file, 13, SEEK_SET) == 0);
        }
        fault = 0;
        /* Short read with the required nominal size must also refuse. */
        if (size < AM2R_11_DATA_PROOF_SIZE)
            assert(!am2rDataProofMatches(NULL, AM2R_11_DATA_PROOF_SIZE, file));
        assert(ftell(file) == 13);
        printf("proof=%d cursor=13 faults=4\n", memory);
        free(bytes);
    } else {
        assert(!"unknown mode");
    }
    fclose(file);
    return 0;
}
