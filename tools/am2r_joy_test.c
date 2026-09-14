// SPDX-License-Identifier: GPL-3.0-or-later
// Hardware-test helper for the AM2R Main_MiSTer-to-runner controller bridge.

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

static void usage(const char *program)
{
	fprintf(stderr, "usage: %s <mask> <hold-ms>\n", program);
}

int main(int argc, char **argv)
{
	if (argc != 3) {
		usage(argv[0]);
		return 2;
	}

	char *mask_end = NULL;
	char *hold_end = NULL;
	errno = 0;
	unsigned long mask_value = strtoul(argv[1], &mask_end, 0);
	unsigned long hold_ms = strtoul(argv[2], &hold_end, 0);
	if (errno || !mask_end || *mask_end || !hold_end || *hold_end ||
	    mask_value > UINT32_MAX || hold_ms > 60000) {
		usage(argv[0]);
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
	if (joy->magic != AM2R_JOY_SHM_MAGIC ||
	    joy->version != AM2R_JOY_SHM_VERSION) {
		fprintf(stderr, "invalid joystick bridge protocol\n");
		munmap((void *)joy, sizeof(*joy));
		return 1;
	}

	uint32_t previous = joy->joy_mask[0];
	joy->joy_mask[0] = (uint32_t)mask_value;
	__sync_synchronize();
	printf("player 1 mask 0x%08lx for %lu ms\n", mask_value, hold_ms);

	struct timespec delay = {
		.tv_sec = (time_t)(hold_ms / 1000),
		.tv_nsec = (long)((hold_ms % 1000) * 1000000UL),
	};
	while (nanosleep(&delay, &delay) != 0 && errno == EINTR) {}

	joy->joy_mask[0] = previous;
	__sync_synchronize();
	munmap((void *)joy, sizeof(*joy));
	return 0;
}
