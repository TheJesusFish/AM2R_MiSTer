// SPDX-License-Identifier: GPL-3.0-or-later
// Low-overhead repeating-input helper for AM2R hardware pacing tests.

#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#include "../src/hps-wrapper/am2r_joy_shm.h"

static int parse_ulong(const char *text, unsigned long limit, unsigned long *out)
{
	char *end = NULL;
	errno = 0;
	unsigned long value = strtoul(text, &end, 0);
	if (errno || !end || *end || value > limit) return 0;
	*out = value;
	return 1;
}

static void delay_ms(unsigned long milliseconds)
{
	struct timespec delay = {
		.tv_sec = (time_t)(milliseconds / 1000),
		.tv_nsec = (long)((milliseconds % 1000) * 1000000UL),
	};
	while (nanosleep(&delay, &delay) != 0 && errno == EINTR) {}
}

int main(int argc, char **argv)
{
	unsigned long mask_a, hold_a, mask_b, hold_b, cycles;
	if (argc != 6 ||
	    !parse_ulong(argv[1], UINT32_MAX, &mask_a) ||
	    !parse_ulong(argv[2], 60000, &hold_a) ||
	    !parse_ulong(argv[3], UINT32_MAX, &mask_b) ||
	    !parse_ulong(argv[4], 60000, &hold_b) ||
	    !parse_ulong(argv[5], 100000, &cycles)) {
		fprintf(stderr, "usage: %s <mask-a> <hold-a-ms> <mask-b> <hold-b-ms> <cycles>\n", argv[0]);
		return 2;
	}

	int fd = open(AM2R_JOY_SHM_PATH, O_RDWR | O_CLOEXEC);
	if (fd < 0) {
		perror("open joystick shared memory");
		return 1;
	}
	volatile Am2rJoyShm *joy = mmap(NULL, sizeof(*joy), PROT_READ | PROT_WRITE,
	                                MAP_SHARED, fd, 0);
	close(fd);
	if (joy == MAP_FAILED) {
		perror("mmap joystick shared memory");
		return 1;
	}
	if (joy->magic != AM2R_JOY_SHM_MAGIC || joy->version != AM2R_JOY_SHM_VERSION) {
		fprintf(stderr, "invalid joystick bridge protocol\n");
		munmap((void *)joy, sizeof(*joy));
		return 1;
	}

	uint32_t previous = joy->joy_mask[0];
	for (unsigned long cycle = 0; cycle < cycles; ++cycle) {
		joy->joy_mask[0] = (uint32_t)mask_a;
		__sync_synchronize();
		delay_ms(hold_a);
		joy->joy_mask[0] = (uint32_t)mask_b;
		__sync_synchronize();
		delay_ms(hold_b);
	}
	joy->joy_mask[0] = previous;
	__sync_synchronize();
	munmap((void *)joy, sizeof(*joy));
	printf("player 1 pattern 0x%08lx/%lums 0x%08lx/%lums for %lu cycles\n",
	       mask_a, hold_a, mask_b, hold_b, cycles);
	return 0;
}
