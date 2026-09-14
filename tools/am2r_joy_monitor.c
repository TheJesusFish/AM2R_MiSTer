// SPDX-License-Identifier: GPL-3.0-or-later
// Observe the live Main_MiSTer-to-AM2R controller bridge during hardware QA.

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

static uint64_t now_ms(void)
{
	struct timespec value;
	clock_gettime(CLOCK_MONOTONIC, &value);
	return (uint64_t)value.tv_sec * 1000u + (uint64_t)value.tv_nsec / 1000000u;
}

int main(int argc, char **argv)
{
	char *end = NULL;
	unsigned long duration_ms = 45000;
	if (argc > 2) {
		fprintf(stderr, "usage: %s [duration-ms]\n", argv[0]);
		return 2;
	}
	if (argc == 2) {
		errno = 0;
		duration_ms = strtoul(argv[1], &end, 0);
		if (errno || !end || *end || duration_ms > 120000) return 2;
	}

	int fd = -1;
	uint64_t deadline = now_ms() + 15000;
	while (fd < 0 && now_ms() < deadline) {
		fd = open(AM2R_JOY_SHM_PATH, O_RDONLY | O_CLOEXEC);
		if (fd < 0) usleep(10000);
	}
	if (fd < 0) {
		perror("open joystick shared memory");
		return 1;
	}
	volatile const Am2rJoyShm *joy = mmap(NULL, sizeof(*joy), PROT_READ,
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

	uint64_t start = now_ms();
	uint32_t previous = UINT32_MAX;
	deadline = start + duration_ms;
	while (now_ms() < deadline) {
		__sync_synchronize();
		uint32_t mask = joy->joy_mask[0];
		if (mask != previous) {
			printf("%6llu ms mask=0x%08x\n",
			       (unsigned long long)(now_ms() - start), mask);
			fflush(stdout);
			previous = mask;
		}
		usleep(1000);
	}
	munmap((void *)joy, sizeof(*joy));
	return 0;
}
