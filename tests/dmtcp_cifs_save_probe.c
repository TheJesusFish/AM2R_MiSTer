// SPDX-License-Identifier: GPL-3.0-or-later

#include <errno.h>
#include <stdio.h>
#include <string.h>

int
main(int argc, char **argv)
{
  unsigned char buffer[4096];
  size_t total = 0;
  FILE *file;

  if (argc != 2) {
    fprintf(stderr, "usage: %s FILE\n", argv[0]);
    return 2;
  }

  file = fopen(argv[1], "rb");
  if (file == NULL) {
    fprintf(stderr, "fopen failed: %s\n", strerror(errno));
    return 1;
  }

  while (!feof(file)) {
    size_t count = fread(buffer, 1, sizeof(buffer), file);
    total += count;
    if (ferror(file)) {
      fprintf(stderr, "fread failed after %lu bytes\n", (unsigned long)total);
      fclose(file);
      return 1;
    }
  }

  if (fclose(file) != 0) {
    fprintf(stderr, "fclose failed: %s\n", strerror(errno));
    return 1;
  }

  printf("read %lu bytes\n", (unsigned long)total);
  return 0;
}
