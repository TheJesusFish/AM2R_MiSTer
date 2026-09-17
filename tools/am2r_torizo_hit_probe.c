// SPDX-License-Identifier: GPL-3.0-or-later
// Capture native FPGA frames around a real Torizo-health change.

#define _GNU_SOURCE
#define _FILE_OFFSET_BITS 64

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#define CONTROL_PHYS 0x23ff0000u
#define CONTROL_BYTES 4096u
#define NATIVE_PHYS 0x3a000000u
#define NATIVE_OFFSET 0x100u
#define COMMAND_REGION_PHYS 0x23fd0000u
#define COMMAND_REGION_BYTES 0x20000u
#define COMMAND_BYTES 0x10000u
#define WIDTH 320u
#define HEIGHT 240u
#define FRAME_BYTES (WIDTH * HEIGHT * 4u)
#define NATIVE_BYTES (NATIVE_OFFSET + 3u * FRAME_BYTES)
#define POST_FRAMES 8u

static uint64_t monotonic_ns(void)
{
	struct timespec now;
	if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return 0;
	return (uint64_t)now.tv_sec * 1000000000ULL + (uint64_t)now.tv_nsec;
}

static int write_blob(const char *path, const void *data, size_t bytes)
{
	FILE *output = fopen(path, "wb");
	if (!output || fwrite(data, 1, bytes, output) != bytes ||
	    fclose(output) != 0) {
		perror(path);
		return -1;
	}
	return 0;
}

static int read_process(int fd, uintptr_t address, void *value, size_t bytes)
{
	ssize_t count = pread(fd, value, bytes, (off_t)address);
	return count == (ssize_t)bytes ? 0 : -1;
}

int main(int argc, char **argv)
{
	if (argc != 5) {
		fprintf(stderr,
		        "usage: %s pid health-real-address frame-address seconds\n",
		        argv[0]);
		return 2;
	}
	char *end = NULL;
	long pid = strtol(argv[1], &end, 0);
	if (!end || *end || pid <= 1) return 2;
	uintptr_t health_address = (uintptr_t)strtoul(argv[2], &end, 0);
	if (!end || *end || !health_address) return 2;
	uintptr_t frame_address = (uintptr_t)strtoul(argv[3], &end, 0);
	if (!end || *end || !frame_address) return 2;
	unsigned long seconds = strtoul(argv[4], &end, 0);
	if (!end || *end || seconds < 2 || seconds > 120) return 2;

	char memory_path[64];
	snprintf(memory_path, sizeof(memory_path), "/proc/%ld/mem", pid);
	int process_fd = open(memory_path, O_RDONLY | O_CLOEXEC);
	int memory_fd = open("/dev/mem", O_RDONLY | O_SYNC | O_CLOEXEC);
	if (process_fd < 0 || memory_fd < 0) {
		perror("open process or FPGA memory");
		return 1;
	}
	volatile uint32_t *control = mmap(NULL, CONTROL_BYTES, PROT_READ,
		MAP_SHARED, memory_fd, CONTROL_PHYS);
	uint8_t *native = mmap(NULL, NATIVE_BYTES, PROT_READ,
		MAP_SHARED, memory_fd, NATIVE_PHYS);
	uint8_t *commands = mmap(NULL, COMMAND_REGION_BYTES, PROT_READ,
		MAP_SHARED, memory_fd, COMMAND_REGION_PHYS);
	if (control == MAP_FAILED || native == MAP_FAILED ||
	    commands == MAP_FAILED) {
		perror("mmap AM2R buffers");
		return 1;
	}

	uint8_t *previous = malloc(FRAME_BYTES);
	uint8_t *current = malloc(FRAME_BYTES);
	if (!previous || !current) return 1;
	double previous_health = 0.0;
	int32_t game_frame = 0;
	if (read_process(process_fd, health_address, &previous_health,
	                 sizeof(previous_health)) ||
	    previous_health < -1.0 || previous_health > 10000.0) {
		fprintf(stderr, "invalid initial Torizo health\n");
		return 1;
	}
	uint32_t previous_sequence = control[6];
	unsigned have_previous = 0;
	unsigned post_remaining = 0;
	unsigned post_index = 0;
	unsigned hit_count = 0;
	uint64_t started = monotonic_ns();
	uint64_t deadline = started + seconds * 1000000000ULL;

	printf("torizo-probe pid=%ld health_address=0x%" PRIxPTR
	       " frame_address=0x%" PRIxPTR " health=%.3f\n",
	       pid, health_address, frame_address, previous_health);
	while (monotonic_ns() < deadline) {
		double health = previous_health;
		if (read_process(process_fd, health_address, &health, sizeof(health))) {
			perror("read Torizo health");
			return 1;
		}
		if (health != previous_health) {
			read_process(process_fd, frame_address, &game_frame,
			             sizeof(game_frame));
			uint64_t elapsed = monotonic_ns() - started;
			printf("torizo-hit index=%u elapsed_ns=%" PRIu64
			       " game_frame=%d native_sequence=%u health=%.3f->%.3f\n",
			       hit_count, elapsed, game_frame, control[6],
			       previous_health, health);
			if (have_previous) {
				char path[128];
				snprintf(path, sizeof(path),
				         "/tmp/am2r-torizo-hit-%u-pre.xrgb", hit_count);
				if (write_blob(path, previous, FRAME_BYTES)) return 1;
			}
			char path[128];
			snprintf(path, sizeof(path),
			         "/tmp/am2r-torizo-hit-%u-commands-fd.bin", hit_count);
			if (write_blob(path, commands, COMMAND_BYTES)) return 1;
			snprintf(path, sizeof(path),
			         "/tmp/am2r-torizo-hit-%u-commands-fe.bin", hit_count);
			if (write_blob(path, commands + COMMAND_BYTES, COMMAND_BYTES))
				return 1;
			post_remaining = POST_FRAMES;
			post_index = 0;
			hit_count++;
			previous_health = health;
		}

		uint32_t sequence = control[6];
		if (sequence == 0 || sequence == previous_sequence) {
			usleep(100);
			continue;
		}
		__sync_synchronize();
		unsigned buffer = control[7] >> 30;
		if (buffer >= 3u) continue;
		memcpy(current, native + NATIVE_OFFSET + buffer * FRAME_BYTES,
		       FRAME_BYTES);
		if (post_remaining && hit_count) {
			char path[128];
			snprintf(path, sizeof(path),
			         "/tmp/am2r-torizo-hit-%u-post-%u.xrgb",
			         hit_count - 1, post_index++);
			if (write_blob(path, current, FRAME_BYTES)) return 1;
			post_remaining--;
		}
		uint8_t *swap = previous;
		previous = current;
		current = swap;
		have_previous = 1;
		previous_sequence = sequence;
	}

	printf("torizo-probe-complete hits=%u final_health=%.3f\n",
	       hit_count, previous_health);
	return hit_count ? 0 : 3;
}
