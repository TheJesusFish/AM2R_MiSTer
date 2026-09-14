// SPDX-License-Identifier: GPL-3.0-or-later

#include "file_system.h"
#include "log.h"
#include "overlay_file_system.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#include <direct.h>
#define make_dir(path) _mkdir(path)
#define remove_dir(path) _rmdir(path)
#else
#include <sys/stat.h>
#include <unistd.h>
#define make_dir(path) mkdir((path), 0777)
#define remove_dir(path) rmdir(path)
#endif

void platformLog(const logType type, const char* format, va_list args)
{
    (void)type;
    vfprintf(stderr, format, args);
}

static int write_direct(const char* path, const char* contents)
{
    FILE* file = fopen(path, "wb");
    if (file == NULL) return 0;
    size_t length = strlen(contents);
    int ok = fwrite(contents, 1, length, file) == length;
    if (fclose(file) != 0) ok = 0;
    return ok;
}

static int expect_text(FileSystem* fs, const char* path, const char* expected)
{
    char* actual = fs->vtable->readFileText(fs, path);
    int ok = actual != NULL && strcmp(actual, expected) == 0;
    free(actual);
    return ok;
}

int main(int argc, char** argv)
{
    if (argc != 2) {
        fprintf(stderr, "usage: %s <empty-test-root>\n", argv[0]);
        return 2;
    }

    char bundle[1024];
    char saves[1024];
    char config[1024];
    char temp[1024];
    if (snprintf(bundle, sizeof(bundle), "%s/bundle", argv[1]) >= (int)sizeof(bundle) ||
        snprintf(saves, sizeof(saves), "%s/saves", argv[1]) >= (int)sizeof(saves) ||
        snprintf(config, sizeof(config), "%s/saves/config.ini", argv[1]) >= (int)sizeof(config) ||
        snprintf(temp, sizeof(temp), "%s/saves/config.ini.butterscotch-tmp", argv[1]) >= (int)sizeof(temp)) {
        fprintf(stderr, "test path is too long\n");
        return 2;
    }

    if (make_dir(argv[1]) != 0 || make_dir(bundle) != 0 || make_dir(saves) != 0) {
        perror("create test directories");
        return 1;
    }
    if (!write_direct(config, "old\n")) {
        perror("seed config");
        return 1;
    }

    OverlayFileSystem* overlay = OverlayFileSystem_create(bundle, saves);
    FileSystem* fs = (FileSystem*)overlay;

    if (!fs->vtable->writeFileText(fs, "config.ini", "new\n") ||
        !expect_text(fs, "config.ini", "new\n")) {
        fprintf(stderr, "successful atomic replacement did not publish the new contents\n");
        return 1;
    }
    FILE* stray = fopen(temp, "rb");
    if (stray != NULL) {
        fclose(stray);
        fprintf(stderr, "successful replacement left its temporary file behind\n");
        return 1;
    }

    if (!write_direct(config, "preserve-me\n") || make_dir(temp) != 0) {
        perror("prepare forced temporary-file failure");
        return 1;
    }
    if (fs->vtable->writeFileText(fs, "config.ini", "must-not-publish\n")) {
        fprintf(stderr, "write unexpectedly succeeded with an unusable temporary path\n");
        return 1;
    }
    if (!expect_text(fs, "config.ini", "preserve-me\n")) {
        fprintf(stderr, "failed replacement damaged the previous destination\n");
        return 1;
    }

    OverlayFileSystem_destroy(overlay);
    if (remove_dir(temp) != 0 || remove(config) != 0 ||
        remove_dir(saves) != 0 || remove_dir(bundle) != 0 || remove_dir(argv[1]) != 0) {
        perror("clean test directories");
        return 1;
    }

    puts("overlay atomic text-write test passed");
    return 0;
}
