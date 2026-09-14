// SPDX-License-Identifier: GPL-3.0-or-later
// Exercise AM2R's persistent state and reset controls through the real MiSTer OSD.

#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <linux/uinput.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>

static int sleep_ms(unsigned long milliseconds)
{
	struct timespec delay = {
		.tv_sec = (time_t)(milliseconds / 1000),
		.tv_nsec = (long)((milliseconds % 1000) * 1000000UL),
	};
	while (nanosleep(&delay, &delay) != 0) {
		if (errno != EINTR) return -1;
	}
	return 0;
}

static int emit(int fd, unsigned short type, unsigned short code, int value)
{
	struct input_event event;
	memset(&event, 0, sizeof(event));
	event.type = type;
	event.code = code;
	event.value = value;
	return write(fd, &event, sizeof(event)) == (ssize_t)sizeof(event) ? 0 : -1;
}

static int pulse(int fd, unsigned short key)
{
	if (emit(fd, EV_KEY, key, 1) || emit(fd, EV_SYN, SYN_REPORT, 0) ||
	    sleep_ms(40) || emit(fd, EV_KEY, key, 0) ||
	    emit(fd, EV_SYN, SYN_REPORT, 0) || sleep_ms(260)) return -1;
	return 0;
}

int main(int argc, char **argv)
{
	bool load;
	bool inspect;
	bool reset;
	bool toggle;
	bool current_slot = false;
	unsigned int target_slot = 4;
	unsigned long initial_delay_ms = 1000;
	if (argc < 2 || argc > 5 ||
	    (strcmp(argv[1], "--save") && strcmp(argv[1], "--load") &&
	     strcmp(argv[1], "--inspect") && strcmp(argv[1], "--reset") &&
	     strcmp(argv[1], "--toggle"))) {
		fprintf(stderr, "usage: %s --save|--load|--inspect|--reset|--toggle [--current|--slot 1..4] [initial-delay-ms]\n", argv[0]);
		return 2;
	}
	load = !strcmp(argv[1], "--load");
	inspect = !strcmp(argv[1], "--inspect");
	reset = !strcmp(argv[1], "--reset");
	toggle = !strcmp(argv[1], "--toggle");
	bool have_delay = false;
	for (int i = 2; i < argc; ++i) {
		if (!strcmp(argv[i], "--current")) {
			if (current_slot) return 2;
			current_slot = true;
		} else if (!strcmp(argv[i], "--slot")) {
			char *end = NULL;
			unsigned long slot;
			if (current_slot || ++i >= argc) return 2;
			errno = 0;
			slot = strtoul(argv[i], &end, 0);
			if (errno || !end || *end || slot < 1 || slot > 4) return 2;
			target_slot = (unsigned int)slot;
		} else {
			char *end = NULL;
			errno = 0;
			initial_delay_ms = strtoul(argv[i], &end, 0);
			if (have_delay || errno || !end || *end || initial_delay_ms > 300000)
				return 2;
			have_delay = true;
		}
	}

	int fd = open("/dev/uinput", O_WRONLY | O_NONBLOCK | O_CLOEXEC);
	if (fd < 0) {
		perror("open /dev/uinput");
		return 1;
	}
	if (ioctl(fd, UI_SET_EVBIT, EV_KEY) || ioctl(fd, UI_SET_EVBIT, EV_SYN))
		goto ioctl_error;
	const unsigned short keys[] = {KEY_F12, KEY_ENTER, KEY_DOWN, KEY_HOME};
	for (size_t i = 0; i < sizeof(keys) / sizeof(keys[0]); ++i) {
		if (ioctl(fd, UI_SET_KEYBIT, keys[i])) goto ioctl_error;
	}

	struct uinput_user_dev device;
	memset(&device, 0, sizeof(device));
	strncpy(device.name, "AM2R savestate QA keyboard", UINPUT_MAX_NAME_SIZE - 1);
	device.id.bustype = BUS_USB;
	device.id.vendor = 0x1209;
	device.id.product = 0xa2f3;
	device.id.version = 1;
	if (write(fd, &device, sizeof(device)) != (ssize_t)sizeof(device) ||
	    ioctl(fd, UI_DEV_CREATE)) goto ioctl_error;

	printf("keyboard ready; %s%s through OSD in %lu ms\n",
	       toggle ? "toggling" :
	       (reset ? "resetting" : (inspect ? "inspecting " : (load ? "loading " : "saving "))),
	       reset ? "" : (current_slot ? "current slot" :
	       (target_slot == 1 ? "slot 1" : target_slot == 2 ? "slot 2" :
	       target_slot == 3 ? "slot 3" : "slot 4")), initial_delay_ms);
	fflush(stdout);
	if (toggle) {
		if (sleep_ms(initial_delay_ms) || pulse(fd, KEY_F12)) goto io_error;
		printf("OSD toggle sent\n");
		fflush(stdout);
		goto done;
	}
	if (sleep_ms(initial_delay_ms) || pulse(fd, KEY_F12) || sleep_ms(800) ||
	    pulse(fd, KEY_HOME))
		goto io_error;
	if (reset) {
		// Aspect, video source, diagnostic pattern, slot, save, load, reset.
		for (int row = 0; row < 6; ++row) {
			if (pulse(fd, KEY_DOWN)) goto io_error;
		}
		if (pulse(fd, KEY_ENTER)) goto io_error;
		printf("reset trigger sent through OSD\n");
		fflush(stdout);
		if (sleep_ms(12000)) goto io_error;
		goto done;
	}
	// A fresh core OSD starts at Aspect ratio. With the removed CRT safe-area
	// option, Savestate slot is the fourth selectable row.
	for (int row = 0; row < 3; ++row) {
		if (pulse(fd, KEY_DOWN)) goto io_error;
	}
	for (unsigned int slot = 1; !current_slot && slot < target_slot; ++slot) {
		// Core option values cycle with Enter. Left/right changes between the
		// Core and System OSD pages, so it must not be used here.
		if (pulse(fd, KEY_ENTER)) goto io_error;
	}
	if (inspect) {
		printf("%s row selected; leaving OSD visible for 20000 ms\n",
		       current_slot ? "current slot" :
		       (target_slot == 1 ? "slot 1" : target_slot == 2 ? "slot 2" :
		       target_slot == 3 ? "slot 3" : "slot 4"));
		fflush(stdout);
		if (sleep_ms(20000) || pulse(fd, KEY_F12)) goto io_error;
		goto done;
	}
	// Save state is the next row and Load state is the one after it.
	if (pulse(fd, KEY_DOWN) || (load && pulse(fd, KEY_DOWN)) ||
	    pulse(fd, KEY_ENTER)) goto io_error;
	printf("%s %s trigger sent through OSD\n",
	       current_slot ? "current slot" :
	       (target_slot == 1 ? "slot 1" : target_slot == 2 ? "slot 2" :
	       target_slot == 3 ? "slot 3" : "slot 4"), load ? "load" : "save");
	fflush(stdout);
	// Momentary T[...] actions intentionally leave the core OSD open. Close it
	// after the frontend has observed the trigger so capture can verify the
	// restored game image and subsequent input without an overlay.
	if (sleep_ms(3000) || pulse(fd, KEY_F12) || sleep_ms(12000)) goto io_error;

done:
	ioctl(fd, UI_DEV_DESTROY);
	close(fd);
	return 0;

ioctl_error:
	perror("configure uinput keyboard");
io_error:
	ioctl(fd, UI_DEV_DESTROY);
	close(fd);
	return 1;
}
