// SPDX-License-Identifier: GPL-3.0-or-later
// Capture the most transient-looking completed AM2R native DDR frame.

#define _GNU_SOURCE

#include <errno.h>
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
#define WIDTH 320u
#define HEIGHT 240u
#define FRAME_BYTES (WIDTH * HEIGHT * 4u)
#define NATIVE_BYTES (NATIVE_OFFSET + 3u * FRAME_BYTES)

static uint64_t monotonic_ns(void)
{
	struct timespec now;
	if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return 0;
	return (uint64_t)now.tv_sec * 1000000000ULL + (uint64_t)now.tv_nsec;
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
	if (fd < 0) {
		perror("open /dev/mem");
		return 1;
	}
	volatile uint32_t *control = mmap(NULL, CONTROL_BYTES, PROT_READ,
		MAP_SHARED, fd, CONTROL_PHYS);
	uint8_t *native = mmap(NULL, NATIVE_BYTES, PROT_READ,
		MAP_SHARED, fd, NATIVE_PHYS);
	if (control == MAP_FAILED || native == MAP_FAILED) {
		perror("mmap AM2R DDR");
		return 1;
	}

	uint32_t *storage = malloc(FRAME_BYTES * 4u);
	if (!storage) {
		perror("allocate frame ring");
		return 1;
	}
	uint32_t *older = storage;
	uint32_t *previous = storage + WIDTH * HEIGHT;
	uint32_t *current = storage + 2u * WIDTH * HEIGHT;
	uint32_t *best = storage + 3u * WIDTH * HEIGHT;
	uint32_t previous_sequence = control[6];
	uint32_t candidate_sequence_source = 0;
	unsigned candidate_buffer = 0;
	uint32_t candidate_sequence = 0;
	unsigned captured = 0;
	unsigned best_score = 0;
	unsigned best_rows = 0;
	unsigned best_buffer = 0;
	uint64_t deadline = monotonic_ns() + (uint64_t)seconds * 1000000000ULL;

	while (monotonic_ns() < deadline) {
		uint32_t sequence = control[6];
		// The HPS clears completion sequence to zero while publishing the next
		// command list. Only a new nonzero value denotes a completed DDR frame.
		if (sequence == 0 || sequence == previous_sequence) {
			usleep(100);
			continue;
		}
		__sync_synchronize();
		uint32_t completion = control[7];
		unsigned buffer = completion >> 30;
		memcpy(current, native + NATIVE_OFFSET + buffer * FRAME_BYTES,
			FRAME_BYTES);
		__sync_synchronize();

		if (captured >= 2) {
			unsigned score = 0;
			unsigned rows = 0;
			for (unsigned y = 0; y < HEIGHT; ++y) {
				unsigned row_score = 0;
				for (unsigned x = 0; x < WIDTH; ++x) {
					unsigned i = y * WIDTH + x;
					if (older[i] == current[i] && previous[i] != current[i])
						row_score++;
				}
				if (row_score >= 8) rows++;
				score += row_score;
			}
			if (score > best_score) {
				best_score = score;
				best_rows = rows;
				candidate_sequence = candidate_sequence_source;
				best_buffer = candidate_buffer;
				memcpy(best, previous, FRAME_BYTES);
			}
			if (score >= 1000)
				printf("candidate sequence=%u score=%u rows=%u\n",
					candidate_sequence_source, score, rows);
		}

		uint32_t *swap = older;
		older = previous;
		previous = current;
		current = swap;
		candidate_sequence_source = sequence;
		candidate_buffer = buffer;
		previous_sequence = sequence;
		captured++;
	}

	FILE *output = fopen("/tmp/am2r-native-probe-max.xrgb", "wb");
	if (!output || fwrite(best, 1, FRAME_BYTES, output) != FRAME_BYTES ||
		fclose(output) != 0) {
		perror("write probe frame");
		return 1;
	}
	printf("probe frames=%u best_sequence=%u best_buffer=%u score=%u rows=%u bytes=%u\n",
		captured, candidate_sequence, best_buffer, best_score, best_rows,
		(unsigned)FRAME_BYTES);

	free(storage);
	munmap(native, NATIVE_BYTES);
	munmap((void *)control, CONTROL_BYTES);
	close(fd);
	return 0;
}
