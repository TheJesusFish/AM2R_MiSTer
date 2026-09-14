// SPDX-License-Identifier: GPL-3.0-or-later
// Deterministic real-controller stress sequence for AM2R performance QA.

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
	while (nanosleep(&delay, &delay) != 0) if (errno != EINTR) return -1;
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

static int sync_event(int fd) { return emit(fd, EV_SYN, SYN_REPORT, 0); }

static int set_key(int fd, unsigned short code, bool down)
{
	return emit(fd, EV_KEY, code, down ? 1 : 0) || sync_event(fd);
}

static int set_hat_y(int fd, int value)
{
	return emit(fd, EV_ABS, ABS_HAT0Y, value) || sync_event(fd);
}

static int pulse(int fd, unsigned short code, unsigned long hold_ms)
{
	return set_key(fd, code, true) || sleep_ms(hold_ms) || set_key(fd, code, false);
}

static int fire_burst(int fd, unsigned short fire, unsigned long duration_ms)
{
	for (unsigned long elapsed = 0; elapsed < duration_ms; elapsed += 250) {
		if (pulse(fd, fire, 55) || sleep_ms(195)) return -1;
	}
	return 0;
}

static int phase(int fd, const char *name, unsigned short fire,
	unsigned short aim, int hat_y, unsigned long duration_ms)
{
	printf("PHASE %s %lu ms\n", name, duration_ms);
	fflush(stdout);
	if (hat_y && set_hat_y(fd, hat_y)) return -1;
	if (aim && set_key(fd, aim, true)) return -1;
	int result = fire_burst(fd, fire, duration_ms);
	if (aim && set_key(fd, aim, false)) result = -1;
	if (hat_y && set_hat_y(fd, 0)) result = -1;
	if (sleep_ms(1000)) result = -1;
	return result;
}

int main(int argc, char **argv)
{
	bool new_mapping = false;
	bool map_only = false;
	bool map_hold = false;
	unsigned long initial_delay_ms = 30000;
	for (int i = 1; i < argc; ++i) {
		if (!strcmp(argv[i], "--new")) new_mapping = true;
		else if (!strcmp(argv[i], "--map-only")) map_only = true;
		else if (!strcmp(argv[i], "--map-hold")) { map_only = true; map_hold = true; }
		else {
			char *end = NULL;
			errno = 0;
			initial_delay_ms = strtoul(argv[i], &end, 0);
			if (errno || !end || *end || initial_delay_ms > 120000) goto usage;
		}
	}

	int fd = open("/dev/uinput", O_WRONLY | O_NONBLOCK | O_CLOEXEC);
	if (fd < 0) { perror("open /dev/uinput"); return 1; }
	if (ioctl(fd, UI_SET_EVBIT, EV_KEY) || ioctl(fd, UI_SET_EVBIT, EV_ABS) ||
	    ioctl(fd, UI_SET_EVBIT, EV_SYN)) goto ioctl_error;
	const unsigned short buttons[] = {
		BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST,
		BTN_SELECT, BTN_START, BTN_TL, BTN_TR,
	};
	for (size_t i = 0; i < sizeof(buttons) / sizeof(buttons[0]); ++i)
		if (ioctl(fd, UI_SET_KEYBIT, buttons[i])) goto ioctl_error;
	if (ioctl(fd, UI_SET_ABSBIT, ABS_HAT0X) || ioctl(fd, UI_SET_ABSBIT, ABS_HAT0Y))
		goto ioctl_error;

	struct uinput_user_dev device;
	memset(&device, 0, sizeof(device));
	// Reuse the controller identity already mapped by MiSTer during earlier QA;
	// a new name would open the framework's binding wizard instead of gameplay.
	strncpy(device.name, "AM2R hardware QA controller", UINPUT_MAX_NAME_SIZE - 1);
	device.id.bustype = BUS_USB;
	device.id.vendor = 0x045e;
	device.id.product = 0x028e;
	device.id.version = 0x0110;
	device.absmin[ABS_HAT0X] = device.absmin[ABS_HAT0Y] = -1;
	device.absmax[ABS_HAT0X] = device.absmax[ABS_HAT0Y] = 1;
	if (write(fd, &device, sizeof(device)) != (ssize_t)sizeof(device) ||
	    ioctl(fd, UI_DEV_CREATE)) goto ioctl_error;

	// Old staged defaults: Fire=A/east and the single Aim action=R.
	// Requested defaults: Fire=X/west, Aim Up=R, and Aim Down=L.
	unsigned short fire = new_mapping ? BTN_WEST : BTN_EAST;
	unsigned short aim_up = BTN_TR;
	unsigned short aim_down = new_mapping ? BTN_TL : BTN_TR;
	printf("controller ready; mapping=%s sequence in %lu ms\n",
	       new_mapping ? "new" : "old", initial_delay_ms);
	fflush(stdout);
	if (sleep_ms(initial_delay_ms) ||
	    (!map_only && phase(fd, "straight", fire, 0, 0, 6000)) ||
	    (!map_only && phase(fd, "vertical-up", fire, 0, -1, 6000)) ||
	    (!map_only && phase(fd, "vertical-down", fire, 0, 1, 6000)) ||
	    (!map_only && phase(fd, "diagonal-up", fire, aim_up, 0, 6000)) ||
	    (!map_only && phase(fd, "diagonal-down", fire, aim_down, new_mapping ? 0 : 1, 6000)))
		goto io_error;

	printf("PHASE map-toggle 16000 ms\n");
	fflush(stdout);
	if (map_hold) {
		if (pulse(fd, BTN_START, 100) || sleep_ms(60000)) goto io_error;
	} else {
		for (int i = 0; i < 8; ++i) {
			if (pulse(fd, BTN_START, 100) || sleep_ms(1900)) goto io_error;
		}
	}
	printf("sequence complete\n");
	fflush(stdout);
	if (sleep_ms(3000)) goto io_error;
	ioctl(fd, UI_DEV_DESTROY);
	close(fd);
	return 0;

usage:
	fprintf(stderr, "usage: %s [--new] [--map-only|--map-hold] [initial-delay-ms]\n", argv[0]);
	return 2;
ioctl_error:
	perror("configure uinput controller");
io_error:
	ioctl(fd, UI_DEV_DESTROY);
	close(fd);
	return 1;
}
