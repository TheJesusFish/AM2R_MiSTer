// SPDX-License-Identifier: GPL-3.0-or-later
// Emit a bounded sequence of keyboard keys through Linux uinput for MiSTer OSD QA.

#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <linux/uinput.h>
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
	    sleep_ms(50) || emit(fd, EV_KEY, key, 0) ||
	    emit(fd, EV_SYN, SYN_REPORT, 0) || sleep_ms(300)) return -1;
	return 0;
}

static int key_for_name(const char *name, unsigned short *key)
{
	struct key_name {
		const char *name;
		unsigned short key;
	};
	static const struct key_name names[] = {
		{"f12", KEY_F12},
		{"enter", KEY_ENTER},
		{"up", KEY_UP},
		{"down", KEY_DOWN},
		{"left", KEY_LEFT},
		{"right", KEY_RIGHT},
		{"home", KEY_HOME},
		{"esc", KEY_ESC},
	};
	for (size_t i = 0; i < sizeof(names) / sizeof(names[0]); ++i) {
		if (!strcmp(name, names[i].name)) {
			*key = names[i].key;
			return 0;
		}
	}
	return -1;
}

static int parse_wait(const char *argument, unsigned long *milliseconds)
{
	if (strncmp(argument, "wait=", 5)) return -1;
	char *end = NULL;
	errno = 0;
	unsigned long parsed = strtoul(argument + 5, &end, 10);
	if (errno || !end || *end || parsed > 60000) return -1;
	*milliseconds = parsed;
	return 0;
}

int main(int argc, char **argv)
{
	if (argc < 2) {
		fprintf(stderr,
		        "usage: %s {f12|enter|up|down|left|right|home|esc|wait=MS}...\n",
		        argv[0]);
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
		KEY_F12, KEY_ENTER, KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT,
		KEY_HOME, KEY_ESC
	};
	for (size_t i = 0; i < sizeof(keys) / sizeof(keys[0]); ++i) {
		if (ioctl(fd, UI_SET_KEYBIT, keys[i])) goto ioctl_error;
	}

	struct uinput_user_dev device;
	memset(&device, 0, sizeof(device));
	strncpy(device.name, "MiSTer OSD QA keyboard", UINPUT_MAX_NAME_SIZE - 1);
	device.id.bustype = BUS_USB;
	device.id.vendor = 0x1209;
	device.id.product = 0xa2f3;
	device.id.version = 1;
	if (write(fd, &device, sizeof(device)) != (ssize_t)sizeof(device) ||
	    ioctl(fd, UI_DEV_CREATE)) goto ioctl_error;

	// Give Main time to enumerate the short-lived keyboard before the first edge.
	if (sleep_ms(800)) goto io_error;
	for (int i = 1; i < argc; ++i) {
		unsigned long wait;
		unsigned short key;
		if (!parse_wait(argv[i], &wait)) {
			printf("wait %lu ms\n", wait);
			if (sleep_ms(wait)) goto io_error;
		} else if (!key_for_name(argv[i], &key)) {
			printf("key %s\n", argv[i]);
			if (pulse(fd, key)) goto io_error;
		} else {
			fprintf(stderr, "unknown sequence token: %s\n", argv[i]);
			ioctl(fd, UI_DEV_DESTROY);
			close(fd);
			return 2;
		}
		fflush(stdout);
	}

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
