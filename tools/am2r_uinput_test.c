// SPDX-License-Identifier: GPL-3.0-or-later
// Create a short-lived Xbox-compatible Linux controller for MiSTer hardware QA.

#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <linux/uinput.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define QA_CORE_MAP_PATH "/media/fat/config/inputs/AM2R_input_045e_028e_v3.map"

static bool qa_map_installed;

static void remove_qa_map(void)
{
	if (qa_map_installed) unlink(QA_CORE_MAP_PATH);
}

static int install_qa_map(unsigned int action_index, uint32_t event_code)
{
	uint32_t map[32] = {0};
	if (action_index >= sizeof(map) / sizeof(map[0])) return -1;
	map[action_index] = event_code;
	if (mkdir("/media/fat/config/inputs", 0755) && errno != EEXIST) {
		perror("create controller-map directory");
		return -1;
	}

	int fd = open(QA_CORE_MAP_PATH,
	              O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
	if (fd < 0) {
		if (errno == EEXIST)
			fprintf(stderr, "%s: refusing to replace an existing controller map\n",
			        QA_CORE_MAP_PATH);
		else
			perror("create temporary core controller map");
		return -1;
	}
	qa_map_installed = true;
	if (write(fd, map, sizeof(map)) != (ssize_t)sizeof(map) || fsync(fd)) {
		perror("write temporary core controller map");
		close(fd);
		remove_qa_map();
		return -1;
	}
	if (close(fd)) {
		perror("close temporary core controller map");
		remove_qa_map();
		return -1;
	}
	return 0;
}

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

static int sync_event(int fd)
{
	return emit(fd, EV_SYN, SYN_REPORT, 0);
}

static int pulse_button(int fd, unsigned short button, unsigned long hold_ms)
{
	if (emit(fd, EV_KEY, button, 1) || sync_event(fd) || sleep_ms(hold_ms) ||
	    emit(fd, EV_KEY, button, 0) || sync_event(fd)) return -1;
	return 0;
}

static int parse_delay(const char *text, unsigned long *value)
{
	char *end = NULL;
	errno = 0;
	unsigned long parsed = strtoul(text, &end, 0);
	if (errno || !end || *end || parsed > 60000) return -1;
	*value = parsed;
	return 0;
}

int main(int argc, char **argv)
{
	unsigned long initial_delay_ms = 12000;
	bool gameplay = false;
	bool traverse = false;
	bool bindings = false;
	bool bindable_save = false;
	bool mapped_save = false;
	bool install_bindable_map = false;
	bool start_only = false;
	bool jump_loop = false;
	bool left_fall = false;
	bool right_walk = false;
	bool left_walk = false;
	bool left_traverse = false;
	bool right_traverse = false;
	bool metroid_test = false;
	bool continue_save = false;
	bool select_save = false;
	bool have_delay = false;
	for (int i = 1; i < argc; ++i) {
		if (!strcmp(argv[i], "--gameplay")) {
			if (gameplay) goto usage;
			gameplay = true;
		} else if (!strcmp(argv[i], "--traverse")) {
			if (traverse) goto usage;
			traverse = true;
		} else if (!strcmp(argv[i], "--bindings")) {
			if (bindings) goto usage;
			bindings = true;
		} else if (!strcmp(argv[i], "--bindable-save")) {
			if (bindable_save) goto usage;
			bindable_save = true;
		} else if (!strcmp(argv[i], "--mapped-save")) {
			if (mapped_save) goto usage;
			mapped_save = true;
		} else if (!strcmp(argv[i], "--install-bindable-map")) {
			if (install_bindable_map) goto usage;
			install_bindable_map = true;
		} else if (!strcmp(argv[i], "--start-only")) {
			if (start_only) goto usage;
			start_only = true;
		} else if (!strcmp(argv[i], "--jump-loop")) {
			if (jump_loop) goto usage;
			jump_loop = true;
		} else if (!strcmp(argv[i], "--left-fall")) {
			if (left_fall) goto usage;
			left_fall = true;
		} else if (!strcmp(argv[i], "--right-walk")) {
			if (right_walk) goto usage;
			right_walk = true;
		} else if (!strcmp(argv[i], "--left-walk")) {
			if (left_walk) goto usage;
			left_walk = true;
		} else if (!strcmp(argv[i], "--left-traverse")) {
			if (left_traverse) goto usage;
			left_traverse = true;
		} else if (!strcmp(argv[i], "--right-traverse")) {
			if (right_traverse) goto usage;
			right_traverse = true;
		} else if (!strcmp(argv[i], "--metroid-test")) {
			if (metroid_test) goto usage;
			metroid_test = true;
		} else if (!strcmp(argv[i], "--continue-save")) {
			if (continue_save) goto usage;
			continue_save = true;
		} else if (!strcmp(argv[i], "--select-save")) {
			if (select_save) goto usage;
			select_save = true;
		} else {
			if (have_delay || parse_delay(argv[i], &initial_delay_ms)) goto usage;
			have_delay = true;
		}
	}
	if ((traverse && !gameplay) ||
	    ((bindings || bindable_save || mapped_save || install_bindable_map || start_only || jump_loop || left_fall || right_walk || left_walk || left_traverse || right_traverse || metroid_test || continue_save || select_save) &&
	     (gameplay || traverse)) ||
	    ((int)bindings + (int)bindable_save + (int)mapped_save +
	     (int)install_bindable_map + (int)start_only +
	     (int)jump_loop + (int)left_fall + (int)right_walk + (int)left_walk +
	     (int)left_traverse + (int)right_traverse + (int)metroid_test +
	     (int)continue_save + (int)select_save > 1)) goto usage;

	if (install_bindable_map) {
		// Install before launching the core so Main reads this controller's
		// saved per-core mapping on its first event. The otherwise unused QA
		// button makes the observed core bit unambiguous.
		if (install_qa_map(13, BTN_TRIGGER_HAPPY1)) return 1;
		qa_map_installed = false;
		printf("installed persistent QA Save State map at %s\n", QA_CORE_MAP_PATH);
		return 0;
	}

	if (bindable_save || jump_loop) {
		// Core actions begin at map index 4. Jump is action 2 (index 5), and
		// Save State is action 10 (index 13). Use an otherwise unmapped test
		// button: common controller profiles reserve stick clicks for the OSD.
		if (install_qa_map(jump_loop ? 5 : 13,
		                   jump_loop ? BTN_EAST : BTN_TRIGGER_HAPPY1)) return 1;
		atexit(remove_qa_map);
	}

	int fd = open("/dev/uinput", O_WRONLY | O_NONBLOCK | O_CLOEXEC);
	if (fd < 0) {
		perror("open /dev/uinput");
		return 1;
	}
	if (ioctl(fd, UI_SET_EVBIT, EV_KEY) || ioctl(fd, UI_SET_EVBIT, EV_ABS) ||
	    ioctl(fd, UI_SET_EVBIT, EV_SYN)) {
		perror("configure uinput event classes");
		close(fd);
		return 1;
	}

	const unsigned short buttons[] = {
		BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST,
		BTN_SELECT, BTN_START, BTN_TL, BTN_TR, BTN_THUMBL,
		BTN_TRIGGER_HAPPY1, KEY_Z,
	};
	for (size_t i = 0; i < sizeof(buttons) / sizeof(buttons[0]); ++i) {
		if (ioctl(fd, UI_SET_KEYBIT, buttons[i])) {
			perror("configure uinput button");
			close(fd);
			return 1;
		}
	}
	if (ioctl(fd, UI_SET_ABSBIT, ABS_HAT0X) ||
	    ioctl(fd, UI_SET_ABSBIT, ABS_HAT0Y) ||
	    ioctl(fd, UI_SET_ABSBIT, ABS_RZ)) {
		perror("configure uinput d-pad");
		close(fd);
		return 1;
	}

	struct uinput_user_dev device;
	memset(&device, 0, sizeof(device));
	strncpy(device.name, "AM2R hardware QA controller", UINPUT_MAX_NAME_SIZE - 1);
	device.id.bustype = BUS_USB;
	device.id.vendor = 0x045e;
	device.id.product = 0x028e;
	device.id.version = 0x0110;
	device.absmin[ABS_HAT0X] = -1;
	device.absmax[ABS_HAT0X] = 1;
	device.absmin[ABS_HAT0Y] = -1;
	device.absmax[ABS_HAT0Y] = 1;
	device.absmin[ABS_RZ] = 0;
	device.absmax[ABS_RZ] = 255;
	device.absflat[ABS_RZ] = 8;
	if (write(fd, &device, sizeof(device)) != (ssize_t)sizeof(device) ||
	    ioctl(fd, UI_DEV_CREATE)) {
		perror("create uinput controller");
		close(fd);
		return 1;
	}

	printf("controller ready; %s sequence in %lu ms\n",
	       jump_loop ? "jump-loop" :
	       mapped_save ? "existing-map save-state" :
	       bindable_save ? "bindable save-state" :
	       start_only ? "start-only" :
	       left_fall ? "left-fall" :
	       right_walk ? "right-walk" :
	       left_walk ? "left-walk" :
	       left_traverse ? "left-traverse" :
	       right_traverse ? "right-traverse" :
	       metroid_test ? "metroid-test" :
	       continue_save ? "continue-save" :
	       select_save ? "select-save" :
	       (bindings ? "bindings" : (gameplay ? "gameplay" : "boot")),
	       initial_delay_ms);
	fflush(stdout);
	if (sleep_ms(initial_delay_ms)) goto io_error;
	if (continue_save) {
		// Assign the hot-plugged controller without forwarding that edge, enter
		// the save selector, choose slot A, and confirm Continue. Do not append
		// gameplay movement: callers can inspect the exact loaded save point.
		if (pulse_button(fd, BTN_SOUTH, 120) || sleep_ms(700) ||
		    pulse_button(fd, BTN_START, 250) || sleep_ms(7000) ||
		    pulse_button(fd, BTN_SOUTH, 220) || sleep_ms(1400) ||
		    pulse_button(fd, BTN_SOUTH, 220) || sleep_ms(5000)) goto io_error;
	} else if (select_save) {
		// Continue from a save selector that is already visible. The first Walk
		// edge is consumed while assigning this short-lived controller to P1;
		// AM2R's menu confirms with the Jump/A action, not Walk/B.
		if (pulse_button(fd, BTN_SOUTH, 120) || sleep_ms(1000) ||
		    pulse_button(fd, BTN_EAST, 220) || sleep_ms(1800) ||
		    pulse_button(fd, BTN_EAST, 220) || sleep_ms(5000)) goto io_error;
	} else if (metroid_test) {
		// The save owns missiles but starts with the beam selected. Arm them with
		// the dedicated Missiles/Y action (Weapon Select/Select is a different
		// game action), then aim upward and fire distinct edges. Face left first
		// because the repeatable evade route leaves the Alpha there.
		if (pulse_button(fd, BTN_SOUTH, 120) || sleep_ms(300) ||
		    pulse_button(fd, BTN_NORTH, 180) || sleep_ms(400) ||
		    emit(fd, EV_ABS, ABS_HAT0X, -1) || sync_event(fd) ||
		    sleep_ms(240) || emit(fd, EV_ABS, ABS_HAT0X, 0) || sync_event(fd) ||
		    sleep_ms(300) || emit(fd, EV_KEY, BTN_TR, 1) || sync_event(fd))
			goto io_error;
		for (int shot = 0; shot < 10; ++shot) {
			printf("missile vertical %d/10\n", shot + 1);
			fflush(stdout);
			if (pulse_button(fd, BTN_WEST, 120) || sleep_ms(600))
				goto io_error;
		}
		if (emit(fd, EV_KEY, BTN_TR, 0) ||
		    sync_event(fd) || sleep_ms(3000)) goto io_error;
	} else if (jump_loop) {
		// Assign the newly created controller before the measured sequence, then
		// let MiSTer's transient controller-map notification disappear. BTN_SOUTH
		// is present in the global Xbox map but intentionally has no core action
		// in the temporary one-entry Jump map.
		if (pulse_button(fd, BTN_SOUTH, 120) || sleep_ms(7000)) goto io_error;
		for (int jump = 0; jump < 12; ++jump) {
			printf("jump %d/12\n", jump + 1);
			fflush(stdout);
			if (pulse_button(fd, BTN_EAST, 180) || sleep_ms(1000))
				goto io_error;
		}
	} else if (left_traverse || right_traverse) {
		if (pulse_button(fd, BTN_SOUTH, 120) || sleep_ms(1500) ||
		    emit(fd, EV_ABS, ABS_HAT0X, right_traverse ? 1 : -1) || sync_event(fd)) goto io_error;
		for (int jump = 0; jump < 12; ++jump) {
			// Main_MiSTer's Xbox compatibility map presents BTN_EAST as
			// core A (Jump), while BTN_SOUTH is core B (Walk).
			if (pulse_button(fd, BTN_EAST, 600) || sleep_ms(250))
				goto io_error;
		}
		if (emit(fd, EV_ABS, ABS_HAT0X, 0) || sync_event(fd) ||
		    sleep_ms(5000)) goto io_error;
	} else if (right_walk || left_walk) {
		if (pulse_button(fd, BTN_SOUTH, 120) || sleep_ms(1500) ||
		    emit(fd, EV_ABS, ABS_HAT0X, right_walk ? 1 : -1) || sync_event(fd) ||
		    sleep_ms(10000) || emit(fd, EV_ABS, ABS_HAT0X, 0) ||
		    sync_event(fd) || sleep_ms(5000)) goto io_error;
	} else if (left_fall) {
		// Assign the hot-plugged controller, then walk away from the room's
		// right wall and cross the central pit without jumping.
		if (pulse_button(fd, BTN_SOUTH, 120) || sleep_ms(1500) ||
		    emit(fd, EV_ABS, ABS_HAT0X, -1) || sync_event(fd) ||
		    sleep_ms(100)) goto io_error;
		for (int jump = 0; jump < 5; ++jump) {
			if (pulse_button(fd, BTN_EAST, 220) || sleep_ms(500))
				goto io_error;
		}
		if (emit(fd, EV_ABS, ABS_HAT0X, 0) || sync_event(fd) ||
		    sleep_ms(6000)) goto io_error;
	} else if (start_only) {
		// Assign the temporary controller without forwarding the assignment
		// button, then send exactly one Start edge to the core.
		if (pulse_button(fd, BTN_SOUTH, 120) || sleep_ms(1000) ||
		    pulse_button(fd, BTN_START, 200) || sleep_ms(1000)) goto io_error;
		printf("Start-only pulse sent\n");
		fflush(stdout);
	} else if (mapped_save) {
		// The pre-launch map binds action 10 to the otherwise unused QA button.
		// Assign this hot-plugged controller to P1, then pulse that exact input.
		if (pulse_button(fd, BTN_SOUTH, 200) || sleep_ms(1000)) goto io_error;
		printf("pulse mapped Save State expected core mask 0x%08x\n", 1u << 13);
		fflush(stdout);
		if (pulse_button(fd, BTN_TRIGGER_HAPPY1, 600) || sleep_ms(15000))
			goto io_error;
		printf("mapped save-state pulse sent\n");
		fflush(stdout);
	} else if (bindable_save) {
		// A newly hot-plugged controller is assigned to P1 by its first standard
		// button press. The per-core map intentionally leaves that button blank.
		if (pulse_button(fd, BTN_SOUTH, 200) || sleep_ms(1000)) goto io_error;
		printf("pulse Save State expected core mask 0x%08x\n", 1u << 13);
		fflush(stdout);
		if (pulse_button(fd, BTN_TRIGGER_HAPPY1, 600) || sleep_ms(8000)) goto io_error;
		printf("bindable save-state pulse sent\n");
		fflush(stdout);
	} else if (bindings) {
		// Probe Start on its own first so a title-menu transition cannot be
		// mistaken for a face-button confirmation. The ordered sweep below
		// then validates every standard default binding.
		printf("probe Start         expected core mask 0x%08x\n", 1u << 11);
		fflush(stdout);
		if (pulse_button(fd, BTN_START, 600) || sleep_ms(2500)) goto io_error;

		// These physical controls exercise Main_MiSTer's requested jn defaults
		// and the core's J1 order end-to-end. Morph is intentionally unbound.
		struct {
			const char *name;
			unsigned short code;
			unsigned int expected_mask;
		} pulses[] = {
			{"Fire",          BTN_WEST,   1u << 4},
			{"Jump",          BTN_EAST,   1u << 5},
			{"Missiles",      BTN_NORTH,  1u << 6},
			{"Walk",          BTN_SOUTH,  1u << 7},
			{"Aim Up",        BTN_TR,     1u << 8},
			{"Aim Down",      BTN_TL,     1u << 9},
			{"Weapon Select", BTN_SELECT, 1u << 10},
			{"Start",         BTN_START,  1u << 11},
		};
		for (size_t i = 0; i < sizeof(pulses) / sizeof(pulses[0]); ++i) {
			printf("pulse %-13s expected core mask 0x%08x\n",
			       pulses[i].name, pulses[i].expected_mask);
			fflush(stdout);
			if (pulse_button(fd, pulses[i].code, 600) || sleep_ms(700))
				goto io_error;
		}
		printf("binding sequence sent\n");
		fflush(stdout);
		if (sleep_ms(1000)) goto io_error;
		printf("exit chord Start+Weapon Select expected core mask 0x%08x\n",
		       (1u << 10) | (1u << 11));
		fflush(stdout);
		if (emit(fd, EV_KEY, BTN_START, 1) ||
		    emit(fd, EV_KEY, BTN_SELECT, 1) || sync_event(fd) ||
		    sleep_ms(800) || emit(fd, EV_KEY, BTN_START, 0) ||
		    emit(fd, EV_KEY, BTN_SELECT, 0) || sync_event(fd) ||
		    sleep_ms(1000)) goto io_error;
	} else if (!gameplay) {
		// MiSTer consumes the first button from a newly hot-plugged controller
		// while assigning it to P1.  Assign with Walk first so the following
		// Start pulse actually reaches the core during automated boot tests.
		if (pulse_button(fd, BTN_SOUTH, 120) || sleep_ms(1000) ||
		    pulse_button(fd, BTN_START, 350)) goto io_error;
		printf("Start pulse sent\n");
		fflush(stdout);
	}

	// MiSTer's default Xbox layout maps the east face button to core A and the
	// south face button to core B. Use east here so the sequence confirms menu
	// choices as well as proving that the game-facing A mapping survives the
	// Main_MiSTer bridge.
	if (bindings || bindable_save || mapped_save || start_only || jump_loop || left_fall || right_walk || left_walk || left_traverse || right_traverse || metroid_test || continue_save || select_save) {
		// The semantic binding sequence above is complete.
	} else if (traverse) {
		if (emit(fd, EV_ABS, ABS_HAT0X, 1) || sync_event(fd)) goto io_error;
		for (int i = 0; i < 8; ++i) {
			if (sleep_ms(1200) || pulse_button(fd, BTN_EAST, 180)) goto io_error;
		}
		if (sleep_ms(600) || emit(fd, EV_ABS, ABS_HAT0X, 0) || sync_event(fd) ||
		    sleep_ms(1000)) goto io_error;
	} else {
		for (int i = 0; i < 3; ++i) {
			if (sleep_ms(1800) || pulse_button(fd, BTN_EAST, 220)) goto io_error;
		}
		if (sleep_ms(3000) || emit(fd, EV_ABS, ABS_HAT0X, 1) || sync_event(fd) ||
		    sleep_ms(800) || pulse_button(fd, BTN_EAST, 220) || sleep_ms(1200) ||
		    emit(fd, EV_ABS, ABS_HAT0X, 0) || sync_event(fd) || sleep_ms(1500)) goto io_error;
	}

	if (!bindings && !bindable_save && !mapped_save && !start_only && !jump_loop && !left_fall && !right_walk && !left_walk && !left_traverse && !metroid_test && !continue_save && !select_save)
		printf("A/d-pad sequence sent\n");
	ioctl(fd, UI_DEV_DESTROY);
	close(fd);
	return 0;

usage:
	fprintf(stderr, "usage: %s [initial-delay-ms] [--gameplay] [--traverse] [--bindings|--bindable-save|--mapped-save|--install-bindable-map|--start-only|--jump-loop|--left-fall|--right-walk|--left-walk|--left-traverse|--right-traverse|--metroid-test|--continue-save|--select-save]\n", argv[0]);
	return 2;

io_error:
	perror("emit uinput event");
	ioctl(fd, UI_DEV_DESTROY);
	close(fd);
	return 1;
}
