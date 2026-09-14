// SPDX-License-Identifier: GPL-3.0-or-later
// Toggle AM2R's CRT safe-area option through the real MiSTer OSD for QA.

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
	bool restore_off = false;
	bool inspect_only = false;
	unsigned long initial_delay_ms = 45000;
	if (argc >= 2 && (!strcmp(argv[1], "--off") ||
	                  !strcmp(argv[1], "--inspect"))) {
		restore_off = !strcmp(argv[1], "--off");
		inspect_only = !strcmp(argv[1], "--inspect");
		--argc;
		++argv;
	}
	if (argc == 2) {
		char *end = NULL;
		errno = 0;
		initial_delay_ms = strtoul(argv[1], &end, 0);
		if (errno || !end || *end || initial_delay_ms > 120000) return 2;
	} else if (argc != 1) {
		fprintf(stderr, "usage: %s [--off|--inspect] [initial-delay-ms]\n", argv[0]);
		return 2;
	}

	int fd = open("/dev/uinput", O_WRONLY | O_NONBLOCK | O_CLOEXEC);
	if (fd < 0) {
		perror("open /dev/uinput");
		return 1;
	}
	if (ioctl(fd, UI_SET_EVBIT, EV_KEY) || ioctl(fd, UI_SET_EVBIT, EV_SYN))
		goto ioctl_error;
	const unsigned short keys[] = {
		KEY_F12, KEY_ENTER, KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT
	};
	for (size_t i = 0; i < sizeof(keys) / sizeof(keys[0]); ++i) {
		if (ioctl(fd, UI_SET_KEYBIT, keys[i])) goto ioctl_error;
	}

	struct uinput_user_dev device;
	memset(&device, 0, sizeof(device));
	strncpy(device.name, "AM2R safe-area QA keyboard", UINPUT_MAX_NAME_SIZE - 1);
	device.id.bustype = BUS_USB;
	device.id.vendor = 0x1209;
	device.id.product = 0xa2f2;
	device.id.version = 1;
	if (write(fd, &device, sizeof(device)) != (ssize_t)sizeof(device) ||
	    ioctl(fd, UI_DEV_CREATE)) goto ioctl_error;

	printf("keyboard ready; %s CRT safe area in %lu ms\n",
	       inspect_only ? "inspecting" :
	       (restore_off ? "setting Off" : "setting 95%"), initial_delay_ms);
	fflush(stdout);
	if (sleep_ms(initial_delay_ms) || pulse(fd, KEY_F12) || sleep_ms(800))
		goto io_error;
	if (inspect_only) {
		// Opening the menu is sufficient; the enabling pass left this row selected.
	} else if (restore_off) {
		// The enabling pass leaves the selection on CRT safe area.
		if (pulse(fd, KEY_ENTER)) goto io_error;
	} else {
		// A fresh core load opens its own menu on Aspect ratio. CRT safe area is
		// the fourth selectable CONF_STR row.
		for (int row = 0; row < 3; ++row) {
			if (pulse(fd, KEY_DOWN)) goto io_error;
		}
		if (pulse(fd, KEY_ENTER)) goto io_error;
	}
	printf("CRT safe area %s; leaving OSD visible for capture\n",
	       inspect_only ? "inspection" :
	       (restore_off ? "set to Off" : "set to 95%"));
	fflush(stdout);
	if (sleep_ms(5000) || pulse(fd, KEY_F12) || sleep_ms(12000)) goto io_error;

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
