#define _GNU_SOURCE
#define _FILE_OFFSET_BITS 64
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static uint64_t monotonic_ns(void)
{
	struct timespec now;
	clock_gettime(CLOCK_MONOTONIC, &now);
	return (uint64_t)now.tv_sec * 1000000000ull + (uint64_t)now.tv_nsec;
}

static int read_float(int fd, uintptr_t address, float *value)
{
	return pread(fd, value, sizeof(*value), (off_t)address) == sizeof(*value) ? 0 : -1;
}

static int read_int32(int fd, uintptr_t address, int32_t *value)
{
	return pread(fd, value, sizeof(*value), (off_t)address) == sizeof(*value) ? 0 : -1;
}

int main(int argc, char **argv)
{
	if (argc != 7) {
		fprintf(stderr,
		        "usage: %s pid frame-address character-instance camera-instance duration-ms interval-us\n",
		        argv[0]);
		return 2;
	}
	char *end = NULL;
	long pid = strtol(argv[1], &end, 0);
	if (!end || *end || pid <= 0) return 2;
	uintptr_t frame_address = (uintptr_t)strtoull(argv[2], &end, 0);
	if (!end || *end || !frame_address) return 2;
	uintptr_t character = (uintptr_t)strtoull(argv[3], &end, 0);
	if (!end || *end || !character) return 2;
	uintptr_t camera = (uintptr_t)strtoull(argv[4], &end, 0);
	if (!end || *end || !camera) return 2;
	long duration_ms = strtol(argv[5], &end, 0);
	if (!end || *end || duration_ms <= 0) return 2;
	long interval_us = strtol(argv[6], &end, 0);
	if (!end || *end || interval_us <= 0) return 2;

	char path[64];
	snprintf(path, sizeof(path), "/proc/%ld/mem", pid);
	int fd = open(path, O_RDONLY | O_CLOEXEC);
	if (fd < 0) {
		perror(path);
		return 1;
	}

	const uintptr_t x_offset = 28;
	const uintptr_t y_offset = 32;
	uint64_t started = monotonic_ns();
	uint64_t deadline = started + (uint64_t)duration_ms * 1000000ull;
	uint64_t next = started;
	puts("elapsed_ns,frame,character_x,character_y,camera_x,camera_y");
	while (next < deadline) {
		int32_t frame;
		float character_x, character_y, camera_x, camera_y;
		if (read_int32(fd, frame_address, &frame) ||
		    read_float(fd, character + x_offset, &character_x) ||
		    read_float(fd, character + y_offset, &character_y) ||
		    read_float(fd, camera + x_offset, &camera_x) ||
		    read_float(fd, camera + y_offset, &camera_y)) {
			fprintf(stderr, "process read failed: %s\n", strerror(errno));
			close(fd);
			return 1;
		}
		uint64_t now = monotonic_ns();
		printf("%" PRIu64 ",%" PRId32 ",%.9g,%.9g,%.9g,%.9g\n",
		       now - started, frame, character_x, character_y, camera_x, camera_y);
		next += (uint64_t)interval_us * 1000ull;
		struct timespec target = {
			.tv_sec = (time_t)(next / 1000000000ull),
			.tv_nsec = (long)(next % 1000000000ull),
		};
		clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &target, NULL);
	}
	close(fd);
	return 0;
}
