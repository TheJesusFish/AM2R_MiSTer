// SPDX-License-Identifier: GPL-3.0-or-later
// Toggle AM2R's Video source row through the real MiSTer core OSD.

#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <linux/uinput.h>
#include <stdio.h>
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
	    emit(fd, EV_SYN, SYN_REPORT, 0) || sleep_ms(300)) return -1;
	return 0;
}

int main(void)
{
	int fd = open("/dev/uinput", O_WRONLY | O_NONBLOCK | O_CLOEXEC);
	if (fd < 0) {
		perror("open /dev/uinput");
		return 1;
	}
	if (ioctl(fd, UI_SET_EVBIT, EV_KEY) || ioctl(fd, UI_SET_EVBIT, EV_SYN) ||
	    ioctl(fd, UI_SET_KEYBIT, KEY_F12) || ioctl(fd, UI_SET_KEYBIT, KEY_DOWN) ||
	    ioctl(fd, UI_SET_KEYBIT, KEY_ENTER)) {
		perror("configure uinput keyboard");
		close(fd);
		return 1;
	}

	struct uinput_user_dev device;
	memset(&device, 0, sizeof(device));
	strncpy(device.name, "AM2R video-source QA keyboard", UINPUT_MAX_NAME_SIZE - 1);
	device.id.bustype = BUS_USB;
	device.id.vendor = 0x1209;
	device.id.product = 0xa2f4;
	device.id.version = 1;
	if (write(fd, &device, sizeof(device)) != (ssize_t)sizeof(device) ||
	    ioctl(fd, UI_DEV_CREATE)) {
		perror("create uinput keyboard");
		close(fd);
		return 1;
	}

	int failed = sleep_ms(1000) || pulse(fd, KEY_F12) || sleep_ms(800) ||
	             pulse(fd, KEY_DOWN) || pulse(fd, KEY_ENTER) ||
	             sleep_ms(800) || pulse(fd, KEY_F12);
	ioctl(fd, UI_DEV_DESTROY);
	close(fd);
	return failed ? 1 : 0;
}
