// SPDX-License-Identifier: GPL-3.0-or-later
// Capture abrupt native-frame content loss and the exact FPGA command buffers.

#define _GNU_SOURCE

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
#define FRAME_PIXELS (WIDTH * HEIGHT)
#define FRAME_BYTES (FRAME_PIXELS * 4u)
#define NATIVE_BYTES (NATIVE_OFFSET + 3u * FRAME_BYTES)
#define MAX_EVENTS 8u

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

static unsigned nonblack_pixels(const uint32_t *pixels)
{
	unsigned count = 0;
	for (unsigned i = 0; i < FRAME_PIXELS; ++i)
		if ((pixels[i] & 0x00ffffffu) != 0) count++;
	return count;
}

static int capture_event(unsigned index, const uint32_t *previous,
	const uint32_t *current, const uint8_t *command_region,
	volatile const uint32_t *control, uint32_t sequence,
	unsigned previous_nonblack, unsigned current_nonblack)
{
	char path[128];
	snprintf(path, sizeof(path), "/tmp/am2r-corruption-%u-pre.xrgb", index);
	if (write_blob(path, previous, FRAME_BYTES)) return -1;
	snprintf(path, sizeof(path), "/tmp/am2r-corruption-%u-event.xrgb", index);
	if (write_blob(path, current, FRAME_BYTES)) return -1;
	snprintf(path, sizeof(path), "/tmp/am2r-corruption-%u-commands-fd.bin", index);
	if (write_blob(path, command_region, COMMAND_BYTES)) return -1;
	snprintf(path, sizeof(path), "/tmp/am2r-corruption-%u-commands-fe.bin", index);
	if (write_blob(path, command_region + COMMAND_BYTES, COMMAND_BYTES)) return -1;

	uint32_t command_phys = control[2];
	uint32_t command_count = control[3] & 0xffffu;
	uint32_t completed_after = control[6];
	if (completed_after == sequence && command_count <= COMMAND_BYTES / 64u &&
	    (command_phys == 0x23fd0000u || command_phys == 0x23fe0000u)) {
		const void *commands = command_region +
			(command_phys - COMMAND_REGION_PHYS);
		snprintf(path, sizeof(path),
			 "/tmp/am2r-corruption-%u-commands.bin", index);
		if (write_blob(path, commands, command_count * 64u)) return -1;
	}

	snprintf(path, sizeof(path), "/tmp/am2r-corruption-%u.txt", index);
	FILE *metadata = fopen(path, "w");
	if (!metadata) {
		perror(path);
		return -1;
	}
	fprintf(metadata,
		"sequence=%u\ncompleted_after=%u\ncommand_phys=%08x\n"
		"command_count=%u\nprevious_nonblack=%u\ncurrent_nonblack=%u\n",
		sequence, completed_after, command_phys, command_count,
		previous_nonblack, current_nonblack);
	if (fclose(metadata) != 0) return -1;
	return 0;
}

int main(int argc, char **argv)
{
	unsigned seconds = 30;
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
	uint32_t *previous = storage;
	uint32_t *current = storage + FRAME_PIXELS;
	uint32_t *pending_post = storage + 2u * FRAME_PIXELS;
	uint32_t previous_sequence = control[6];
	unsigned previous_nonblack = 0;
	unsigned minimum_nonblack = FRAME_PIXELS;
	unsigned maximum_nonblack = 0;
	unsigned largest_drop = 0;
	unsigned have_previous = 0;
	unsigned captured = 0;
	unsigned events = 0;
	int pending_event = -1;
	uint64_t deadline = monotonic_ns() + (uint64_t)seconds * 1000000000ULL;

	while (monotonic_ns() < deadline) {
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
		unsigned current_nonblack = nonblack_pixels(current);
		if (current_nonblack < minimum_nonblack)
			minimum_nonblack = current_nonblack;
		if (current_nonblack > maximum_nonblack)
			maximum_nonblack = current_nonblack;
		if (have_previous && previous_nonblack > current_nonblack &&
		    previous_nonblack - current_nonblack > largest_drop)
			largest_drop = previous_nonblack - current_nonblack;

		if (pending_event >= 0) {
			char path[128];
			snprintf(path, sizeof(path),
				 "/tmp/am2r-corruption-%d-post.xrgb", pending_event);
			memcpy(pending_post, current, FRAME_BYTES);
			if (write_blob(path, pending_post, FRAME_BYTES)) return 1;
			pending_event = -1;
		}

		// AM2R impact corruption removes a large portion of the room for one
		// native frame. The deliberately broad 15% threshold captures nearby
		// candidates too; offline command decoding distinguishes the fault from
		// ordinary sprite animation without guessing at the room's brightness.
		if (have_previous && events < MAX_EVENTS &&
		    previous_nonblack >= 3000u && current_nonblack >= 100u &&
		    (uint64_t)current_nonblack * 100u <
			(uint64_t)previous_nonblack * 85u) {
			if (capture_event(events, previous, current, command_region,
				control, sequence, previous_nonblack, current_nonblack))
				return 1;
			printf("corruption event=%u sequence=%u buffer=%u nonblack=%u->%u\n",
				events, sequence, buffer, previous_nonblack,
				current_nonblack);
			pending_event = (int)events;
			events++;
		}

		uint32_t *swap = previous;
		previous = current;
		current = swap;
		previous_nonblack = current_nonblack;
		have_previous = 1;
		previous_sequence = sequence;
		captured++;
	}

	printf("corruption-probe frames=%u events=%u nonblack_min=%u "
	       "nonblack_max=%u largest_drop=%u\n", captured, events,
	       minimum_nonblack, maximum_nonblack, largest_drop);
	free(storage);
	munmap(command_region, COMMAND_REGION_BYTES);
	munmap(native, NATIVE_BYTES);
	munmap((void *)control, CONTROL_BYTES);
	close(fd);
	return events ? 0 : 3;
}
