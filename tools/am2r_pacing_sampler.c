// SPDX-License-Identifier: GPL-3.0-or-later
// Correlate AM2R game motion, FPGA render completion, and native vblank.

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

#define GPU_CONTROL_PHYS UINT32_C(0x23ff0000)
#define GPU_CONTROL_BYTES 4096u
#define GPU_SUBMITTED_SEQUENCE_WORD 1u
#define GPU_COMPLETED_SEQUENCE_WORD 6u
#define GPU_COMPLETION_WORD 7u
#define GPU_VBLANK_COUNTER_WORD 16u
#define GPU_VBLANK_MAGIC_WORD 17u
#define GPU_SCANOUT_FRAME_WORD 18u
#define GPU_NATIVE_FRAME_WORD 19u
#define GPU_VBLANK_MAGIC UINT32_C(0x56424c4b)

static uint64_t monotonic_ns(void)
{
	struct timespec now;
	clock_gettime(CLOCK_MONOTONIC, &now);
	return (uint64_t)now.tv_sec * UINT64_C(1000000000) + (uint64_t)now.tv_nsec;
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
	const int hardware_only = argc == 3;
	if (!hardware_only && argc != 7) {
		fprintf(stderr,
		        "usage: %s duration-ms interval-us\n"
		        "       %s pid frame-address character-instance camera-instance duration-ms interval-us\n",
		        argv[0],
		        argv[0]);
		return 2;
	}
	char *end = NULL;
	long pid = 0;
	uintptr_t frame_address = 0;
	uintptr_t character = 0;
	uintptr_t camera = 0;
	if (!hardware_only) {
		pid = strtol(argv[1], &end, 0);
		if (!end || *end || pid <= 0) return 2;
		frame_address = (uintptr_t)strtoull(argv[2], &end, 0);
		if (!end || *end || !frame_address) return 2;
		character = (uintptr_t)strtoull(argv[3], &end, 0);
		if (!end || *end || !character) return 2;
		camera = (uintptr_t)strtoull(argv[4], &end, 0);
		if (!end || *end || !camera) return 2;
	}
	long duration_ms = strtol(argv[hardware_only ? 1 : 5], &end, 0);
	if (!end || *end || duration_ms <= 0) return 2;
	long interval_us = strtol(argv[hardware_only ? 2 : 6], &end, 0);
	if (!end || *end || interval_us <= 0) return 2;

	int process_fd = -1;
	if (!hardware_only) {
		char process_path[64];
		snprintf(process_path, sizeof(process_path), "/proc/%ld/mem", pid);
		process_fd = open(process_path, O_RDONLY | O_CLOEXEC);
		if (process_fd < 0) {
			perror(process_path);
			return 1;
		}
	}
	int memory_fd = open("/dev/mem", O_RDONLY | O_SYNC | O_CLOEXEC);
	if (memory_fd < 0) {
		perror("/dev/mem");
		if (process_fd >= 0) close(process_fd);
		return 1;
	}
	volatile const uint32_t *control = mmap(NULL, GPU_CONTROL_BYTES, PROT_READ,
	                                         MAP_SHARED, memory_fd,
	                                         GPU_CONTROL_PHYS);
	if (control == MAP_FAILED) {
		perror("mmap GPU control");
		close(memory_fd);
		if (process_fd >= 0) close(process_fd);
		return 1;
	}
	if (control[GPU_VBLANK_MAGIC_WORD] != GPU_VBLANK_MAGIC) {
		fprintf(stderr, "FPGA vblank status magic is unavailable\n");
		munmap((void *)control, GPU_CONTROL_BYTES);
		close(memory_fd);
		close(process_fd);
		return 1;
	}

	const uintptr_t x_offset = 28;
	const uintptr_t y_offset = 32;
	uint64_t started = monotonic_ns();
	uint64_t deadline = started + (uint64_t)duration_ms * UINT64_C(1000000);
	uint64_t next = started;
	puts("elapsed_ns,frame,character_x,character_y,camera_x,camera_y,vblank,submitted,completed,completion,scanout_frame,native_frame");
	while (next < deadline) {
		int32_t frame = 0;
		float character_x = 0, character_y = 0, camera_x = 0, camera_y = 0;
		if (!hardware_only &&
		    (read_int32(process_fd, frame_address, &frame) ||
		     read_float(process_fd, character + x_offset, &character_x) ||
		     read_float(process_fd, character + y_offset, &character_y) ||
		     read_float(process_fd, camera + x_offset, &camera_x) ||
		     read_float(process_fd, camera + y_offset, &camera_y))) {
			fprintf(stderr, "process read failed: %s\n", strerror(errno));
			munmap((void *)control, GPU_CONTROL_BYTES);
			close(memory_fd);
			if (process_fd >= 0) close(process_fd);
			return 1;
		}
		__sync_synchronize();
		uint32_t vblank = control[GPU_VBLANK_COUNTER_WORD];
		uint32_t submitted = control[GPU_SUBMITTED_SEQUENCE_WORD];
		uint32_t completed = control[GPU_COMPLETED_SEQUENCE_WORD];
		uint32_t completion = control[GPU_COMPLETION_WORD];
		uint32_t scanout_frame = control[GPU_SCANOUT_FRAME_WORD];
		uint32_t native_frame = control[GPU_NATIVE_FRAME_WORD];
		uint64_t now = monotonic_ns();
		printf("%" PRIu64 ",%" PRId32 ",%.9g,%.9g,%.9g,%.9g,%" PRIu32
		       ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32
		       ",%" PRIu32 "\n",
		       now - started, frame, character_x, character_y, camera_x, camera_y,
		       vblank, submitted, completed, completion, scanout_frame, native_frame);
		next += (uint64_t)interval_us * UINT64_C(1000);
		struct timespec target = {
			.tv_sec = (time_t)(next / UINT64_C(1000000000)),
			.tv_nsec = (long)(next % UINT64_C(1000000000)),
		};
		clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &target, NULL);
	}

	munmap((void *)control, GPU_CONTROL_BYTES);
	close(memory_fd);
	if (process_fd >= 0) close(process_fd);
	return 0;
}
