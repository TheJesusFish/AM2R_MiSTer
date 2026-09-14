// SPDX-License-Identifier: GPL-3.0-or-later
// Verify whether transient right-edge artifacts exist in completed GPU frames.

#define _GNU_SOURCE

#include <fcntl.h>
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
#define EDGE_X 280u
#define FRAME_BYTES (WIDTH * HEIGHT * 4u)
#define NATIVE_BYTES (NATIVE_OFFSET + 3u * FRAME_BYTES)

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

int main(int argc, char **argv)
{
	unsigned seconds = 35;
	if (argc == 2) {
		char *end = NULL;
		unsigned long parsed = strtoul(argv[1], &end, 0);
		if (!end || *end || parsed < 2 || parsed > 120) return 2;
		seconds = (unsigned)parsed;
	} else if (argc != 1) {
		fprintf(stderr, "usage: %s [seconds]\n", argv[0]);
		return 2;
	}

	int fd = open("/dev/mem", O_RDONLY | O_SYNC | O_CLOEXEC);
	if (fd < 0) { perror("open /dev/mem"); return 1; }
	volatile uint32_t *control = mmap(NULL, CONTROL_BYTES, PROT_READ,
		MAP_SHARED, fd, CONTROL_PHYS);
	uint8_t *native = mmap(NULL, NATIVE_BYTES, PROT_READ,
		MAP_SHARED, fd, NATIVE_PHYS);
	uint8_t *command_region = mmap(NULL, COMMAND_REGION_BYTES, PROT_READ,
		MAP_SHARED, fd, COMMAND_REGION_PHYS);
	if (control == MAP_FAILED || native == MAP_FAILED ||
		command_region == MAP_FAILED) {
		perror("mmap AM2R DDR");
		return 1;
	}

	uint32_t *storage = malloc(FRAME_BYTES * 3u);
	if (!storage) { perror("allocate frame storage"); return 1; }
	uint32_t *best = storage;
	uint32_t *previous = storage + WIDTH * HEIGHT;
	uint32_t *current = storage + 2u * WIDTH * HEIGHT;
	uint32_t previous_sequence = control[6];
	uint32_t best_sequence = 0;
	unsigned best_buffer = 0;
	unsigned best_edge_pixels = 0;
	unsigned captured = 0;
	unsigned have_previous = 0;
	unsigned event_captured = 0;
	uint64_t deadline = monotonic_ns() + (uint64_t)seconds * 1000000000ULL;

	while (monotonic_ns() < deadline) {
		uint32_t sequence = control[6];
		if (sequence == 0 || sequence == previous_sequence) {
			usleep(100);
			continue;
		}
		__sync_synchronize();
		unsigned buffer = control[7] >> 30;
		const uint32_t *frame = (const uint32_t *)(native + NATIVE_OFFSET +
			buffer * FRAME_BYTES);
		memcpy(current, frame, FRAME_BYTES);
		unsigned edge_pixels = 0;
		for (unsigned y = 0; y < HEIGHT; ++y)
			for (unsigned x = EDGE_X; x < WIDTH; ++x)
			{
				uint32_t pixel = current[y * WIDTH + x];
				if (((pixel >> 16) & 0xffu) > 16u ||
				    ((pixel >> 8) & 0xffu) > 16u ||
				    (pixel & 0xffu) > 16u)
					edge_pixels++;
			}
		if (captured == 0 || edge_pixels > best_edge_pixels) {
			best_edge_pixels = edge_pixels;
			best_sequence = sequence;
			best_buffer = buffer;
			memcpy(best, current, FRAME_BYTES);
		}
		if (edge_pixels != 0) {
			printf("edge sequence=%u buffer=%u pixels=%u\n",
				sequence, buffer, edge_pixels);
			if (!event_captured && have_previous) {
				uint32_t command_phys = control[2];
				uint32_t command_count = control[3] & 0xffffu;
				uint32_t completed_after = control[6];
				if (write_blob("/tmp/am2r-native-edge-pre.xrgb", previous,
				               FRAME_BYTES) ||
				    write_blob("/tmp/am2r-native-edge-event.xrgb", current,
				               FRAME_BYTES)) return 1;
				if (write_blob("/tmp/am2r-native-edge-commands-fd.bin",
				               command_region, COMMAND_BYTES) ||
				    write_blob("/tmp/am2r-native-edge-commands-fe.bin",
				               command_region + COMMAND_BYTES,
				               COMMAND_BYTES)) return 1;
				if (completed_after == sequence && command_count <=
				    COMMAND_BYTES / 64u &&
				    (command_phys == 0x23fd0000u ||
				     command_phys == 0x23fe0000u)) {
					const void *commands = command_region +
						(command_phys - COMMAND_REGION_PHYS);
					if (write_blob("/tmp/am2r-native-edge-commands.bin",
					               commands, command_count * 64u)) return 1;
					printf("event commands sequence=%u phys=%08x count=%u\n",
					       sequence, command_phys, command_count);
				} else {
					printf("event command race sequence=%u completed=%u phys=%08x count=%u\n",
					       sequence, completed_after, command_phys, command_count);
				}
				event_captured = 1;
			}
		} else if (event_captured == 1) {
			if (write_blob("/tmp/am2r-native-edge-post.xrgb", current,
			               FRAME_BYTES)) return 1;
			event_captured = 2;
			printf("event post sequence=%u buffer=%u\n", sequence, buffer);
		}
		memcpy(previous, current, FRAME_BYTES);
		have_previous = 1;
		previous_sequence = sequence;
		captured++;
	}

	FILE *output = fopen("/tmp/am2r-native-edge-max.xrgb", "wb");
	if (!output || fwrite(best, 1, FRAME_BYTES, output) != FRAME_BYTES ||
		fclose(output) != 0) {
		perror("write edge frame");
		return 1;
	}
	printf("edge-probe frames=%u best_sequence=%u best_buffer=%u edge_pixels=%u\n",
		captured, best_sequence, best_buffer, best_edge_pixels);

	free(storage);
	munmap(command_region, COMMAND_REGION_BYTES);
	munmap(native, NATIVE_BYTES);
	munmap((void *)control, CONTROL_BYTES);
	close(fd);
	return 0;
}
