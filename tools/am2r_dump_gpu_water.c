// SPDX-License-Identifier: GPL-3.0-or-later
// Read-only diagnostic for the submitted AM2R GPU command buffer on MiSTer.

#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/mman.h>
#include <unistd.h>

#define CONTROL_PHYS 0x23ff0000u
#define CONTROL_BYTES 4096u
#define COMMAND_BYTES 65536u

typedef struct { uint64_t word[8]; } Command;

int main(void)
{
	int fd = open("/dev/mem", O_RDONLY | O_SYNC | O_CLOEXEC);
	if (fd < 0) { perror("open /dev/mem"); return 1; }
	volatile uint32_t *control = mmap(NULL, CONTROL_BYTES, PROT_READ,
	                                  MAP_SHARED, fd, CONTROL_PHYS);
	if (control == MAP_FAILED) { perror("mmap control"); close(fd); return 1; }
	uint32_t command_phys = control[2];
	uint32_t command_count = control[3];
	uint32_t submitted_sequence = control[1];
	uint32_t completed_sequence = control[6];
	if ((command_phys != 0x23fe0000u && command_phys != 0x23fd0000u) ||
	    command_count > COMMAND_BYTES / sizeof(Command)) {
		fprintf(stderr, "invalid command metadata address=%08x count=%u\n",
		        command_phys, command_count);
		munmap((void *)control, CONTROL_BYTES); close(fd); return 1;
	}
	const volatile Command *commands = mmap(NULL, COMMAND_BYTES, PROT_READ,
	                                        MAP_SHARED, fd, command_phys);
	if (commands == MAP_FAILED) {
		perror("mmap commands"); munmap((void *)control, CONTROL_BYTES);
		close(fd); return 1;
	}
	printf("GPU_COMMAND_BUFFER address=%08x count=%u submitted=%u completed=%u\n",
	       command_phys, command_count, submitted_sequence, completed_sequence);
	unsigned matches = 0;
	for (uint32_t index = 0; index < command_count; ++index) {
		uint64_t w0 = commands[index].word[0];
		uint32_t opcode = (uint32_t)w0 & 0xffu;
		uint32_t width = (uint32_t)(w0 >> 16) & 0xffffu;
		uint32_t height = (uint32_t)(w0 >> 32) & 0xffffu;
		uint32_t tint = (uint32_t)commands[index].word[6];
		if (opcode != 2 || height > 2 ||
		    (tint & 0x00ffffffu) != 0x00ffffffu) continue;
		uint64_t w1 = commands[index].word[1];
		uint64_t w2 = commands[index].word[2];
		uint64_t w4 = commands[index].word[4];
		uint64_t w5 = commands[index].word[5];
		printf("WATER_ROW command=%u size=%ux%u source=%08x stride=%u dst=%d,%d "
		       "uv=%08x,%08x step=%08x,%08x tint=%08x\n",
		       index, width, height, (uint32_t)w1, (uint32_t)(w1 >> 32),
		       (int16_t)w2, (int16_t)(w2 >> 16),
		       (uint32_t)w4, (uint32_t)(w4 >> 32),
		       (uint32_t)w5, (uint32_t)(w5 >> 32), tint);
		matches++;
	}
	printf("WATER_ROW_COUNT value=%u\n", matches);
	munmap((void *)commands, COMMAND_BYTES);
	munmap((void *)control, CONTROL_BYTES);
	close(fd);
	return 0;
}
