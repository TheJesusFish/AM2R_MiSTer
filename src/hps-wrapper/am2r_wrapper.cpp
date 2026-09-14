// SPDX-License-Identifier: GPL-3.0-or-later

#include "am2r_wrapper.h"

#include <errno.h>
#include <dirent.h>
#include <fcntl.h>
#include <limits.h>
#include <sched.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/prctl.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#include <vector>

#include "am2r_savestate.h"
#include "am2r_joy_shm.h"
#include "file_io.h"
#include "fpga_io.h"
#include "frame_timer.h"
#include "input.h"
#include "lib/miniz/miniz.h"
#include "menu.h"
#include "osd.h"
#include "support/arcade/mra_loader.h"
#include "user_io.h"
#include "video.h"

extern char **environ;
uint32_t build_joy_mask(int player);

// Standard MiSTer T[...] actions are set and cleared inside one HandleUI()
// call, before the wrapper loop can poll them.  The build-tree user_io.cpp
// copy calls this hook at the status-update boundary so Save/Load remain
// ordinary momentary core-OSD commands.
static volatile sig_atomic_t gOsdSaveSlot = -1;
static volatile sig_atomic_t gOsdLoadSlot = -1;
static volatile sig_atomic_t gOsdResetRequested = 0;

extern "C" void am2r_user_io_status_event(const char *opt, uint32_t value, int ex)
{
    if (ex || value != 1 || !opt) return;
    // Menu-originated reset options include the label after the bit range.
    // Internal Main_MiSTer initialization writes use the literal "[0]" and
    // must not restart the game process.
    if (!strncmp(opt, "[0],", 4)) {
        gOsdResetRequested = 1;
        return;
    }
    int slot = (int)user_io_status_get("[7:6]");
    // Menu entries pass p + 1, so opt continues with ",Save state;..."
    // rather than ending after the bracket. Literal programmatic calls may
    // still end there; the closing bracket keeps this prefix match exact.
    if (!strncmp(opt, "[8]", 3)) gOsdSaveSlot = slot;
    if (!strncmp(opt, "[9]", 3)) gOsdLoadSlot = slot;
}

namespace {

constexpr const char *kCoreName = "AM2R";
constexpr const char *kRuntimeRoot = "/media/fat/games/am2r";
constexpr const char *kRuntimeBinary = "/media/fat/games/am2r/bin/butterscotch";
constexpr const char *kArchivePath = "/media/fat/games/am2r/AM2R.zip";
constexpr const char *kSavePath = "/media/fat/saves/AM2R";
constexpr const char *kConfigPath = "/media/fat/saves/AM2R/config.ini";
constexpr const char *kLegacySavePath = "/media/fat/games/am2r/saves";
constexpr const char *kLogPath = "/media/fat/games/am2r/logs/last-run.log";
constexpr const char *kRuntimeDirectory = "/tmp/am2r-runtime";
constexpr const char *kSessionLogPath = "/tmp/am2r-session.log";
constexpr const char *kStampPath = "/tmp/am2r-runtime/.archive-stamp";
constexpr const char *kMenuExec = "/media/fat/MiSTer";
constexpr const char *kTestPlaybackTrigger = "/tmp/am2r-test-playback";
constexpr const char *kStateRoot = "/media/fat/savestates/AM2R";
constexpr const char *kLegacyStateRoot = "/media/fat/savestates/am2r";
constexpr const char *kDmtcpCoordinator = "/media/fat/games/am2r/dmtcp/bin/dmtcp_coordinator";
constexpr const char *kDmtcpCommand = "/media/fat/games/am2r/dmtcp/bin/dmtcp_command";
constexpr const char *kDmtcpLaunch = "/media/fat/games/am2r/dmtcp/bin/dmtcp_launch";
constexpr const char *kDmtcpRestart = "/media/fat/games/am2r/dmtcp/bin/dmtcp_restart";
constexpr const char *kStateBuild =
    "am2r-state-v24-main-915ca339-dmtcp-3.2.0-mister1";
uint32_t gRuntimeCrc32 = 0;

constexpr const char *kRequiredArchiveFiles[] = {
    "data.win",
    "lang/english.ini",
    "lang/languages.txt",
    "musAlphaFight.ogg",
    "musAncientGuardian.ogg",
    "musArachnus.ogg",
    "musArea1A.ogg",
    "musArea1B.ogg",
    "musArea2A.ogg",
    "musArea2B.ogg",
    "musArea3A.ogg",
    "musArea3B.ogg",
    "musArea4A.ogg",
    "musArea4B.ogg",
    "musArea5A.ogg",
    "musArea5B.ogg",
    "musArea6A.ogg",
    "musArea7A.ogg",
    "musArea7B.ogg",
    "musArea7C.ogg",
    "musArea7D.ogg",
    "musArea8.ogg",
    "musCaveAmbience.ogg",
    "musCaveAmbienceA4.ogg",
    "musCredits.ogg",
    "musEris.ogg",
    "musFanfare.ogg",
    "musGammaFight.ogg",
    "musGenesis.ogg",
    "musHatchling.ogg",
    "musIntroSeq.ogg",
    "musItemAmb.ogg",
    "musItemGet.ogg",
    "musLabAmbience.ogg",
    "musMainCave.ogg",
    "musMainCave2.ogg",
    "musMetroidAppear.ogg",
    "musOmegaFight.ogg",
    "musQueen.ogg",
    "musQueenIntro.ogg",
    "musReactor.ogg",
    "musTitle.ogg",
    "musTorizoA.ogg",
    "musTorizoB.ogg",
    "musZetaFight.ogg",
};

volatile sig_atomic_t gSignal = 0;
volatile sig_atomic_t gChild = -1;
Am2rJoyShm *gJoyShm = nullptr;
uint32_t gPreviousJoyMask[AM2R_JOY_MAX_PLAYERS] = {};

struct StateControl {
    int wrapperFd = -1;
    int loadSlot = -1;
    int activeRestoreSlot = -1;
    bool loadRequested = false;
    bool freshRestartRequested = false;
    int coordinatorPort = 0;
    char sessionDirectory[PATH_MAX] = {};
    char tempDirectory[PATH_MAX] = {};
    uint32_t previousSaveTrigger = 0;
    uint32_t previousLoadTrigger = 0;
    uint64_t saveRequestNs[AM2R_STATE_SLOT_COUNT] = {};
    uint64_t loadRequestNs[AM2R_STATE_SLOT_COUNT] = {};
};

uint64_t monotonic_ns()
{
    timespec now = {};
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return 0;
    return (uint64_t)now.tv_sec * 1000000000ULL + (uint64_t)now.tv_nsec;
}

double elapsed_ms(uint64_t start)
{
    uint64_t now = monotonic_ns();
    return start && now >= start ? (double)(now - start) / 1000000.0 : -1.0;
}

void log_line(FILE *file, const char *format, ...)
{
    if (!file) return;
    va_list args;
    va_start(args, format);
    vfprintf(file, format, args);
    va_end(args);
    fputc('\n', file);
    fflush(file);
}

bool send_state_message(int fd, Am2rStateMessageType type, int slot)
{
    if (fd < 0) return false;
    Am2rStateMessage message = {};
    message.magic = AM2R_STATE_MAGIC;
    message.type = (uint32_t)type;
    message.slot = slot;
    message.pid = (int32_t)getpid();
    sockaddr_un address = {};
    address.sun_family = AF_UNIX;
    strncpy(address.sun_path, AM2R_STATE_RUNNER_SOCKET, sizeof(address.sun_path) - 1);
    return sendto(fd, &message, sizeof(message), MSG_NOSIGNAL,
                  (sockaddr *)&address, sizeof(address)) == (ssize_t)sizeof(message);
}

bool request_runner_state(StateControl &state, Am2rStateMessageType type,
                          int slot, const char *source, FILE *log)
{
    const bool save = type == AM2R_STATE_REQUEST_SAVE;
    if (send_state_message(state.wrapperFd, type, slot)) {
        uint64_t requested = monotonic_ns();
        if (slot >= 0 && slot < AM2R_STATE_SLOT_COUNT) {
            if (save) state.saveRequestNs[slot] = requested;
            else state.loadRequestNs[slot] = requested;
        }
        log_line(log, "savestate_osd_%s slot=%d source=%s",
                 save ? "save" : "load", slot + 1, source);
        InfoMessage(save ? "Saving state" : "Loading state", 900, "AM2R");
        return true;
    }
    log_line(log, "savestate_osd_%s_failed slot=%d source=%s errno=%d",
             save ? "save" : "load", slot + 1, source, errno);
    InfoMessage(save ? "Save state unavailable" : "Load state unavailable",
                1600, "AM2R");
    return false;
}

bool checkpoint_slot(StateControl &state, int slot, FILE *log);
enum StateSlotStatus {
    STATE_SLOT_MISSING,
    STATE_SLOT_INCOMPATIBLE,
    STATE_SLOT_READY,
};

StateSlotStatus state_slot_status(int slot);
bool stop_dmtcp(StateControl &state, FILE *log);

void handle_state_events(StateControl &state, pid_t active, FILE *log)
{
    if (state.wrapperFd < 0) return;
    for (;;) {
        Am2rStateMessage message = {};
        ssize_t count = recv(state.wrapperFd, &message, sizeof(message), MSG_DONTWAIT);
        if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) break;
        if (count != (ssize_t)sizeof(message) || message.magic != AM2R_STATE_MAGIC) {
            if (count <= 0) break;
            continue;
        }
        if (message.slot < 0 || message.slot >= AM2R_STATE_SLOT_COUNT) continue;

        switch ((Am2rStateMessageType)message.type) {
        case AM2R_STATE_SNAPSHOT_READY: {
            log_line(log, "savestate_quiesced slot=%d pid=%d frame=%d",
                     message.slot + 1, message.pid, message.frame);
            checkpoint_slot(state, message.slot, log);
            break;
        }
        case AM2R_STATE_REQUEST_LOAD: {
            StateSlotStatus slotStatus = state_slot_status(message.slot);
            if (slotStatus == STATE_SLOT_READY) {
                state.loadSlot = message.slot;
                state.loadRequested = true;
                log_line(log, "savestate_load_requested slot=%d active=%d frame=%d",
                         message.slot + 1, active, message.frame);
            } else {
                send_state_message(state.wrapperFd, AM2R_STATE_LOAD_MISSING, message.slot);
                if (slotStatus == STATE_SLOT_INCOMPATIBLE) {
                    log_line(log, "savestate_incompatible slot=%d runtime_crc32=%08x",
                             message.slot + 1, gRuntimeCrc32);
                    InfoMessage("Save state is from another AM2R build", 2200, "AM2R");
                } else {
                    log_line(log, "savestate_missing slot=%d", message.slot + 1);
                    InfoMessage("Save state slot is empty", 1600, "AM2R");
                }
            }
            break;
        }
        case AM2R_STATE_RESTORED:
            log_line(log, "savestate_restored slot=%d pid=%d frame=%d elapsed_ms=%.1f",
                     message.slot + 1, message.pid, message.frame,
                     elapsed_ms(state.loadRequestNs[message.slot]));
            state.loadRequestNs[message.slot] = 0;
            state.activeRestoreSlot = -1;
            break;
        case AM2R_STATE_SNAPSHOT_ERROR:
            log_line(log, "savestate_error slot=%d pid=%d frame=%d errno=%d",
                     message.slot + 1, message.pid, message.frame, message.error);
            break;
        default:
            break;
        }
    }
}

void handle_state_osd(StateControl &state, pid_t active, FILE *log)
{
    uint32_t save = user_io_status_get("[8]");
    uint32_t load = user_io_status_get("[9]");
    int slot = (int)user_io_status_get("[7:6]");
    if (gOsdSaveSlot >= 0) {
        int eventSlot = gOsdSaveSlot;
        gOsdSaveSlot = -1;
        request_runner_state(state, AM2R_STATE_REQUEST_SAVE, eventSlot,
                             "event", log);
    }
    if (gOsdLoadSlot >= 0) {
        int eventSlot = gOsdLoadSlot;
        gOsdLoadSlot = -1;
        request_runner_state(state, AM2R_STATE_REQUEST_LOAD, eventSlot,
                             "event", log);
    }
    if (save && !state.previousSaveTrigger) {
        request_runner_state(state, AM2R_STATE_REQUEST_SAVE, slot, "poll", log);
    }
    if (load && !state.previousLoadTrigger) {
        request_runner_state(state, AM2R_STATE_REQUEST_LOAD, slot, "poll", log);
    }
    if (gOsdResetRequested) {
        gOsdResetRequested = 0;
        if (!state.freshRestartRequested) {
            state.loadRequested = false;
            state.loadSlot = -1;
            state.activeRestoreSlot = -1;
            state.freshRestartRequested = true;
            log_line(log, "reset_osd_requested pid=%d", active);
            InfoMessage("Resetting AM2R", 900, "AM2R");
            if (active > 0) kill(active, SIGTERM);
        }
    }
    state.previousSaveTrigger = save;
    state.previousLoadTrigger = load;
}

bool make_directory(const char *path)
{
    if (mkdir(path, 0755) == 0 || errno == EEXIST) return true;
    return false;
}

bool create_joy_shm(FILE *log)
{
	unlink(AM2R_JOY_SHM_PATH);
	int fd = open(AM2R_JOY_SHM_PATH, O_RDWR | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
	if (fd < 0 || ftruncate(fd, sizeof(Am2rJoyShm)) != 0) {
		int savedError = errno;
		if (fd >= 0) close(fd);
		log_line(log, "joy_shm_create_failed errno=%d", savedError);
		errno = savedError;
		return false;
	}
	gJoyShm = (Am2rJoyShm *)mmap(nullptr, sizeof(Am2rJoyShm),
		PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
	close(fd);
	if (gJoyShm == MAP_FAILED) {
		gJoyShm = nullptr;
		log_line(log, "joy_shm_map_failed errno=%d", errno);
		unlink(AM2R_JOY_SHM_PATH);
		return false;
	}
	memset(gJoyShm, 0, sizeof(*gJoyShm));
	gJoyShm->magic = AM2R_JOY_SHM_MAGIC;
	gJoyShm->version = AM2R_JOY_SHM_VERSION;
	memset(gPreviousJoyMask, 0, sizeof(gPreviousJoyMask));
	setenv(AM2R_JOY_SHM_ENV, AM2R_JOY_SHM_PATH, 1);
	log_line(log, "joy_shm=%s", AM2R_JOY_SHM_PATH);
	return true;
}

void publish_joy_masks(StateControl &state, FILE *log)
{
	if (!gJoyShm) return;
	constexpr uint32_t kSaveStateButton = 1u << 13;
	for (int player = 0; player < AM2R_JOY_MAX_PLAYERS; ++player) {
		uint32_t mask = build_joy_mask(player);
		if (player == 0 && (mask & kSaveStateButton) &&
		    !(gPreviousJoyMask[player] & kSaveStateButton)) {
			int slot = (int)user_io_status_get("[7:6]");
			request_runner_state(state, AM2R_STATE_REQUEST_SAVE, slot,
			                     "controller", log);
		}
		// The extra core action is a frontend command, not an AM2R game button.
		gJoyShm->joy_mask[player] = mask & ~kSaveStateButton;
		if (mask != gPreviousJoyMask[player]) {
			log_line(log, "joy player=%d mask=%08x", player + 1, mask);
			gPreviousJoyMask[player] = mask;
		}
	}
	// The three-bit menu value selects a symmetric whole-pixel inset. The
	// runner applies it only to edge UI; game layers remain native 320x240.
	gJoyShm->crt_ui_inset = user_io_status_get("[26:24]") * 2u;
	__sync_synchronize();
}

void cleanup_joy_shm()
{
	if (gJoyShm) {
		memset(gJoyShm->joy_mask, 0, sizeof(gJoyShm->joy_mask));
		__sync_synchronize();
		munmap(gJoyShm, sizeof(Am2rJoyShm));
		gJoyShm = nullptr;
	}
	unsetenv(AM2R_JOY_SHM_ENV);
	unlink(AM2R_JOY_SHM_PATH);
}

bool copy_legacy_save_file(const char *source, const char *destination, FILE *log)
{
    struct stat destinationStat = {};
    if (stat(destination, &destinationStat) == 0) return true;
    if (errno != ENOENT) return false;

    int input = open(source, O_RDONLY | O_CLOEXEC);
    if (input < 0) return false;
    struct stat sourceStat = {};
    if (fstat(input, &sourceStat) != 0 || !S_ISREG(sourceStat.st_mode)) {
        close(input);
        return true;
    }

    char temporary[PATH_MAX] = {};
    snprintf(temporary, sizeof(temporary), "%s.migrate-%d", destination, (int)getpid());
    unlink(temporary);
    int output = open(temporary, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0644);
    if (output < 0) {
        close(input);
        return false;
    }

    bool okay = true;
    char buffer[16384];
    for (;;) {
        ssize_t count = read(input, buffer, sizeof(buffer));
        if (count == 0) break;
        if (count < 0) {
            if (errno == EINTR) continue;
            okay = false;
            break;
        }
        for (ssize_t offset = 0; offset < count;) {
            ssize_t written = write(output, buffer + offset, (size_t)(count - offset));
            if (written < 0 && errno == EINTR) continue;
            if (written <= 0) {
                okay = false;
                break;
            }
            offset += written;
        }
        if (!okay) break;
    }
    if (okay && fsync(output) != 0) okay = false;
    if (close(output) != 0) okay = false;
    close(input);
    if (okay && rename(temporary, destination) == 0) {
        log_line(log, "save_migrated source=%s destination=%s", source, destination);
        return true;
    }
    unlink(temporary);
    return false;
}

bool migrate_legacy_saves(FILE *log, char *error, size_t errorSize)
{
    DIR *directory = opendir(kLegacySavePath);
    if (!directory) return errno == ENOENT;
    bool okay = true;
    for (dirent *entry = readdir(directory); entry; entry = readdir(directory)) {
        if (!strcmp(entry->d_name, ".") || !strcmp(entry->d_name, "..")) continue;
        char source[PATH_MAX] = {};
        char destination[PATH_MAX] = {};
        snprintf(source, sizeof(source), "%s/%s", kLegacySavePath, entry->d_name);
        snprintf(destination, sizeof(destination), "%s/%s", kSavePath, entry->d_name);
        if (!copy_legacy_save_file(source, destination, log)) {
            snprintf(error, errorSize, "Cannot migrate existing AM2R save %s: %s",
                     entry->d_name, strerror(errno));
            okay = false;
            break;
        }
    }
    closedir(directory);
    return okay;
}

bool quarantine_empty_config(FILE *log, char *error, size_t errorSize)
{
    struct stat config = {};
    if (lstat(kConfigPath, &config) != 0) {
        if (errno == ENOENT) return true;
        snprintf(error, errorSize, "Cannot inspect AM2R config: %s", strerror(errno));
        return false;
    }
    if (!S_ISREG(config.st_mode) || config.st_size != 0) return true;

    char quarantine[PATH_MAX] = {};
    snprintf(quarantine, sizeof(quarantine), "%s.corrupt-empty-%lld-%d",
             kConfigPath, (long long)time(nullptr), (int)getpid());
    if (rename(kConfigPath, quarantine) != 0) {
        snprintf(error, errorSize, "Cannot quarantine empty AM2R config: %s",
                 strerror(errno));
        return false;
    }
    log_line(log, "config_empty_quarantined source=%s destination=%s",
             kConfigPath, quarantine);
    return true;
}

bool prepare_directories(FILE *log, char *error, size_t errorSize)
{
    const char *paths[] = {
        kRuntimeRoot,
        "/media/fat/games/am2r/bin",
        "/media/fat/saves",
        kSavePath,
        "/media/fat/games/am2r/logs",
        "/media/fat/savestates",
        kRuntimeDirectory,
        "/tmp/am2r-runtime/lang",
    };
    for (const char *path : paths) {
        if (make_directory(path)) continue;
        snprintf(error, errorSize, "Cannot create %s: %s", path, strerror(errno));
        return false;
    }

    struct stat standardState = {};
    struct stat legacyState = {};
    if (strcmp(kStateRoot, kLegacyStateRoot) && stat(kStateRoot, &standardState) != 0 &&
        errno == ENOENT && stat(kLegacyStateRoot, &legacyState) == 0 &&
        S_ISDIR(legacyState.st_mode)) {
        if (rename(kLegacyStateRoot, kStateRoot) != 0) {
            snprintf(error, errorSize, "Cannot migrate AM2R savestates: %s", strerror(errno));
            return false;
        }
        log_line(log, "savestate_directory_migrated source=%s destination=%s",
                 kLegacyStateRoot, kStateRoot);
    }
    if (!make_directory(kStateRoot)) {
        snprintf(error, errorSize, "Cannot create %s: %s", kStateRoot, strerror(errno));
        return false;
    }
    return migrate_legacy_saves(log, error, errorSize) &&
           quarantine_empty_config(log, error, errorSize);
}

bool file_exists(const char *path)
{
    struct stat st = {};
    return stat(path, &st) == 0 && S_ISREG(st.st_mode);
}

bool remove_ephemeral_tree(const char *path)
{
    const char *tmpPrefix = "/tmp/am2r-dmtcp-";
    const size_t stateRootLength = strlen(kStateRoot);
    const bool stateScratch = path &&
        strncmp(path, kStateRoot, stateRootLength) == 0 &&
        strncmp(path + stateRootLength, "/.session-", 10) == 0;
    if (!path || (strncmp(path, tmpPrefix, strlen(tmpPrefix)) != 0 && !stateScratch))
        return false;
    struct stat st = {};
    if (lstat(path, &st) != 0) return errno == ENOENT;
    if (!S_ISDIR(st.st_mode)) return unlink(path) == 0;
    DIR *directory = opendir(path);
    if (!directory) return false;
    bool okay = true;
    for (dirent *entry = readdir(directory); entry; entry = readdir(directory)) {
        if (!strcmp(entry->d_name, ".") || !strcmp(entry->d_name, "..")) continue;
        char child[PATH_MAX] = {};
        snprintf(child, sizeof(child), "%s/%s", path, entry->d_name);
        if (!remove_ephemeral_tree(child)) okay = false;
    }
    closedir(directory);
    if (rmdir(path) != 0 && errno != ENOENT) okay = false;
    return okay;
}

void cleanup_ephemeral_children(const char *parent, const char *prefix, FILE *log)
{
    DIR *directory = opendir(parent);
    if (!directory) return;
    for (dirent *entry = readdir(directory); entry; entry = readdir(directory)) {
        if (strncmp(entry->d_name, prefix, strlen(prefix)) != 0) continue;
        char path[PATH_MAX] = {};
        snprintf(path, sizeof(path), "%s/%s", parent, entry->d_name);
        if (remove_ephemeral_tree(path))
            log_line(log, "savestate_scratch_removed path=%s", path);
    }
    closedir(directory);
}

void state_slot_path(int slot, char *path, size_t pathSize)
{
    snprintf(path, pathSize, "%s/slot%d.dmtcp", kStateRoot, slot + 1);
}

StateSlotStatus state_slot_status(int slot)
{
    char path[PATH_MAX] = {};
    state_slot_path(slot, path, sizeof(path));
    struct stat st = {};
    if (stat(path, &st) != 0 || !S_ISREG(st.st_mode) || st.st_size <= 4096)
        return STATE_SLOT_MISSING;
    char metadata[PATH_MAX] = {};
    snprintf(metadata, sizeof(metadata), "%s/slot%d.txt", kStateRoot, slot + 1);
    FILE *file = fopen(metadata, "r");
    if (!file) return STATE_SLOT_INCOMPATIBLE;
    char *line = nullptr;
    size_t lineCapacity = 0;
    bool buildCompatible = false;
    bool runtimeCompatible = false;
    while (getline(&line, &lineCapacity, file) >= 0) {
        line[strcspn(line, "\r\n")] = '\0';
        if (!strncmp(line, "build=", 6) && !strcmp(line + 6, kStateBuild))
            buildCompatible = true;
        unsigned int recordedCrc32 = 0;
        char trailing = '\0';
        if (sscanf(line, "runtime_crc32=%8x%c", &recordedCrc32, &trailing) == 1 &&
            recordedCrc32 == gRuntimeCrc32)
            runtimeCompatible = true;
    }
    free(line);
    fclose(file);
    return buildCompatible && runtimeCompatible ?
        STATE_SLOT_READY : STATE_SLOT_INCOMPATIBLE;
}

bool calculate_runtime_crc32(uint32_t *result)
{
    if (!result) return false;
    int file = open(kRuntimeBinary, O_RDONLY | O_CLOEXEC);
    if (file < 0) return false;
    uint8_t buffer[65536];
    mz_ulong crc = MZ_CRC32_INIT;
    bool okay = true;
    for (;;) {
        ssize_t count = read(file, buffer, sizeof(buffer));
        if (count == 0) break;
        if (count < 0) {
            if (errno == EINTR) continue;
            okay = false;
            break;
        }
        crc = mz_crc32(crc, buffer, (size_t)count);
    }
    if (close(file) != 0) okay = false;
    if (okay) *result = (uint32_t)crc;
    return okay;
}

int run_program(const std::vector<char *> &arguments, FILE *log)
{
    if (arguments.empty() || !arguments[0]) return 126;
    pid_t process = fork();
    if (process < 0) return 126;
    if (process == 0) {
        if (log) {
            int logFd = fileno(log);
            if (logFd >= 0) {
                dup2(logFd, STDOUT_FILENO);
                dup2(logFd, STDERR_FILENO);
            }
        }
        execve(arguments[0], arguments.data(), environ);
        _exit(127);
    }
    int status = 0;
    while (waitpid(process, &status, 0) < 0) {
        if (errno != EINTR) return 126;
    }
    if (WIFEXITED(status)) return WEXITSTATUS(status);
    if (WIFSIGNALED(status)) return 128 + WTERMSIG(status);
    return 126;
}

int dmtcp_command(StateControl &state, const char *command, FILE *log)
{
    char port[16] = {};
    snprintf(port, sizeof(port), "%d", state.coordinatorPort);
    std::vector<char *> arguments = {
        const_cast<char *>(kDmtcpCommand),
        const_cast<char *>("--coord-port"), port,
        const_cast<char *>(command), nullptr,
    };
    return run_program(arguments, log);
}

bool dmtcp_running(StateControl &state, bool *available = nullptr)
{
    int output[2] = {-1, -1};
    if (pipe(output) != 0) return false;
    pid_t process = fork();
    if (process < 0) {
        close(output[0]);
        close(output[1]);
        return false;
    }
    if (process == 0) {
        close(output[0]);
        dup2(output[1], STDOUT_FILENO);
        int nullError = open("/dev/null", O_WRONLY | O_CLOEXEC);
        if (nullError >= 0) {
            dup2(nullError, STDERR_FILENO);
            close(nullError);
        }
        if (output[1] > STDERR_FILENO) close(output[1]);
        char port[16] = {};
        snprintf(port, sizeof(port), "%d", state.coordinatorPort);
        char *arguments[] = {
            const_cast<char *>(kDmtcpCommand),
            const_cast<char *>("--coord-port"), port,
            const_cast<char *>("--status"), nullptr,
        };
        execve(kDmtcpCommand, arguments, environ);
        _exit(127);
    }
    close(output[1]);
    char statusText[1024] = {};
    ssize_t used = 0;
    while (used < (ssize_t)sizeof(statusText) - 1) {
        ssize_t count = read(output[0], statusText + used,
                             sizeof(statusText) - 1 - (size_t)used);
        if (count <= 0) break;
        used += count;
    }
    close(output[0]);
    int status = 0;
    while (waitpid(process, &status, 0) < 0 && errno == EINTR) {}
    bool reached = WIFEXITED(status) && WEXITSTATUS(status) == 0;
    if (available) *available = reached;
    return reached && strstr(statusText, "RUNNING=yes") != nullptr;
}

bool start_coordinator(StateControl &state, FILE *log)
{
    state.coordinatorPort++;
    if (state.coordinatorPort > 43999) state.coordinatorPort = 43000;
    char port[16] = {};
    snprintf(port, sizeof(port), "%d", state.coordinatorPort);
    std::vector<char *> arguments = {
        const_cast<char *>(kDmtcpCoordinator),
        const_cast<char *>("--daemon"),
        const_cast<char *>("--exit-on-last"),
        const_cast<char *>("--coord-port"), port,
        const_cast<char *>("--ckptdir"), state.sessionDirectory,
        const_cast<char *>("--tmpdir"), state.tempDirectory,
        const_cast<char *>("--quiet"), nullptr,
    };
    int result = run_program(arguments, log);
    if (result != 0) {
        log_line(log, "dmtcp_coordinator_failed port=%d code=%d",
                 state.coordinatorPort, result);
        return false;
    }
    for (int attempt = 0; attempt < 50; ++attempt) {
        if (dmtcp_command(state, "--status", log) == 0) {
            log_line(log, "dmtcp_coordinator_ready port=%d", state.coordinatorPort);
            return true;
        }
        usleep(20000);
    }
    log_line(log, "dmtcp_coordinator_timeout port=%d", state.coordinatorPort);
    return false;
}

bool stop_dmtcp(StateControl &state, FILE *log)
{
    if (state.coordinatorPort <= 0) return true;
    int result = dmtcp_command(state, "--kill", log);
    for (int wait = 0; wait < 200; ++wait) {
        bool available = false;
        dmtcp_running(state, &available);
        if (!available) break;
        usleep(10000);
    }
    unlink(AM2R_STATE_RUNNER_SOCKET);
    unlink(AM2R_STATE_RESUME_PATH);
    log_line(log, "dmtcp_stop port=%d code=%d", state.coordinatorPort, result);
    return result == 0;
}

bool newest_checkpoint(const StateControl &state, char *path, size_t pathSize,
                       off_t *size)
{
    DIR *directory = opendir(state.sessionDirectory);
    if (!directory) return false;
    time_t newest = 0;
    bool found = false;
    for (dirent *entry = readdir(directory); entry; entry = readdir(directory)) {
        size_t length = strlen(entry->d_name);
        if (length < 6 || strcmp(entry->d_name + length - 6, ".dmtcp")) continue;
        char candidate[PATH_MAX] = {};
        snprintf(candidate, sizeof(candidate), "%s/%s",
                 state.sessionDirectory, entry->d_name);
        struct stat st = {};
        if (stat(candidate, &st) != 0 || !S_ISREG(st.st_mode) || st.st_size <= 4096)
            continue;
        if (!found || st.st_mtime >= newest) {
            strncpy(path, candidate, pathSize - 1);
            *size = st.st_size;
            newest = st.st_mtime;
            found = true;
        }
    }
    closedir(directory);
    return found;
}

bool copy_checkpoint_atomic(const char *source, int slot, off_t expectedSize, FILE *log)
{
    char destination[PATH_MAX] = {};
    char temporary[PATH_MAX] = {};
    state_slot_path(slot, destination, sizeof(destination));
    snprintf(temporary, sizeof(temporary), "%s/slot%d.dmtcp.new",
             kStateRoot, slot + 1);

    // The DMTCP session and persistent slots are siblings on /media/fat.
    // Rename the completed image into the atomic staging name instead of
    // reading and writing the entire checkpoint a second time.  Retain the
    // copy path for an unexpected cross-filesystem deployment.
    unlink(temporary);
    bool renamed = rename(source, temporary) == 0;
    off_t copied = renamed ? expectedSize : 0;
    bool okay = true;
    if (!renamed) {
        int input = open(source, O_RDONLY | O_CLOEXEC);
        int output = open(temporary, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
        if (input < 0 || output < 0) {
            int savedError = errno;
            if (input >= 0) close(input);
            if (output >= 0) close(output);
            unlink(temporary);
            errno = savedError;
            return false;
        }

        char buffer[65536];
        for (;;) {
            ssize_t count = read(input, buffer, sizeof(buffer));
            if (count == 0) break;
            if (count < 0) { okay = false; break; }
            ssize_t offset = 0;
            while (offset < count) {
                ssize_t written = write(output, buffer + offset,
                                        (size_t)(count - offset));
                if (written <= 0) { okay = false; break; }
                offset += written;
                copied += written;
            }
            if (!okay) break;
        }
        if (okay && fsync(output) != 0) okay = false;
        if (close(input) != 0) okay = false;
        if (close(output) != 0) okay = false;
    } else {
        int image = open(temporary, O_RDONLY | O_CLOEXEC);
        if (image < 0 || fsync(image) != 0) okay = false;
        if (image >= 0 && close(image) != 0) okay = false;
    }
    if (copied != expectedSize) okay = false;
    if (okay && rename(temporary, destination) != 0) okay = false;
    if (!okay) {
        int savedError = errno ? errno : EIO;
        unlink(temporary);
        errno = savedError;
        return false;
    }

    char metadata[PATH_MAX] = {};
    char metadataTemp[PATH_MAX] = {};
    snprintf(metadata, sizeof(metadata), "%s/slot%d.txt", kStateRoot, slot + 1);
    snprintf(metadataTemp, sizeof(metadataTemp), "%s/slot%d.txt.new", kStateRoot, slot + 1);
    FILE *file = fopen(metadataTemp, "w");
    if (file) {
        fprintf(file, "format=3\nbuild=%s\nruntime_crc32=%08x\nslot=%d\nbytes=%lld\ncompression=none\nepoch=%lld\n",
                kStateBuild, gRuntimeCrc32, slot + 1,
                (long long)copied, (long long)time(nullptr));
        fflush(file);
        fsync(fileno(file));
        fclose(file);
        rename(metadataTemp, metadata);
    }
    log_line(log, "savestate_committed slot=%d bytes=%lld install=%s path=%s",
             slot + 1, (long long)copied, renamed ? "rename" : "copy", destination);
    return true;
}

void release_quiesced_runner(int error, FILE *log)
{
    int marker = open(AM2R_STATE_RESUME_PATH,
                      O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (marker >= 0) {
        char value[24] = {};
        int length = snprintf(value, sizeof(value), "%d\n", error);
        write(marker, value, (size_t)length);
        fsync(marker);
        close(marker);
    } else {
        log_line(log, "savestate_resume_marker_failed errno=%d", errno);
    }
}

bool checkpoint_slot(StateControl &state, int slot, FILE *log)
{
    uint64_t started = state.saveRequestNs[slot];
    if (!started) started = monotonic_ns();
    int error = 0;
    char source[PATH_MAX] = {};
    off_t size = 0;
    int result = dmtcp_command(state, "--bcheckpoint", log);
    if (result != 0) {
        error = EIO;
        log_line(log, "savestate_checkpoint_failed slot=%d code=%d", slot + 1, result);
    } else {
        bool complete = false;
        bool observedBusy = false;
        for (int wait = 0; wait < 1800; ++wait) {
            bool running = dmtcp_running(state);
            if (!running) observedBusy = true;
            if (observedBusy && running &&
                newest_checkpoint(state, source, sizeof(source), &size)) {
                complete = true;
                break;
            }
            usleep(100000);
        }
        if (!complete) {
            error = ETIMEDOUT;
            log_line(log, "savestate_image_timeout slot=%d", slot + 1);
        } else if (!copy_checkpoint_atomic(source, slot, size, log)) {
            error = errno ? errno : EIO;
            log_line(log, "savestate_copy_failed slot=%d errno=%d", slot + 1, error);
        }
    }
    release_quiesced_runner(error, log);
    log_line(log, "savestate_save_complete slot=%d elapsed_ms=%.1f result=%d",
             slot + 1, elapsed_ms(started), error);
    state.saveRequestNs[slot] = 0;
    if (error == 0)
        InfoMessage("Save state written", 1200, "AM2R");
    else
        InfoMessage("Save state failed", 1800, "AM2R");
    return error == 0;
}

int create_state_socket(FILE *log)
{
    unlink(AM2R_STATE_WRAPPER_SOCKET);
    int socketFd = socket(AF_UNIX, SOCK_DGRAM, 0);
    if (socketFd < 0) return -1;
    sockaddr_un address = {};
    address.sun_family = AF_UNIX;
    strncpy(address.sun_path, AM2R_STATE_WRAPPER_SOCKET, sizeof(address.sun_path) - 1);
    if (bind(socketFd, (sockaddr *)&address, sizeof(address)) != 0) {
        log_line(log, "savestate_bind_failed errno=%d", errno);
        close(socketFd);
        return -1;
    }
    int flags = fcntl(socketFd, F_GETFL, 0);
    if (flags >= 0) fcntl(socketFd, F_SETFL, flags | O_NONBLOCK);
    return socketFd;
}

bool archive_stamp_matches(const struct stat &archiveStat)
{
    FILE *stamp = fopen(kStampPath, "r");
    if (!stamp) return false;
    long long size = -1;
    long long mtime = -1;
    int read = fscanf(stamp, "%lld %lld", &size, &mtime);
    fclose(stamp);
    return read == 2 && size == (long long)archiveStat.st_size &&
           mtime == (long long)archiveStat.st_mtime &&
           file_exists("/tmp/am2r-runtime/data.win");
}

bool write_archive_stamp(const struct stat &archiveStat)
{
    FILE *stamp = fopen(kStampPath, "w");
    if (!stamp) return false;
    int result = fprintf(stamp, "%lld %lld\n", (long long)archiveStat.st_size,
                         (long long)archiveStat.st_mtime);
    return fclose(stamp) == 0 && result > 0;
}

bool safe_archive_prefix(const char *prefix)
{
    if (!prefix) return false;
    if (prefix[0] == '/' || strstr(prefix, "..") || strchr(prefix, '\\')) return false;
    return true;
}

bool prepare_game_archive(FILE *log, char *error, size_t errorSize)
{
    struct stat archiveStat = {};
    if (stat(kArchivePath, &archiveStat) != 0 || !S_ISREG(archiveStat.st_mode)) {
        snprintf(error, errorSize, "Missing /media/fat/games/am2r/AM2R.zip");
        return false;
    }

    if (archive_stamp_matches(archiveStat)) {
        log_line(log, "archive_cache=hit bytes=%lld", (long long)archiveStat.st_size);
        return true;
    }

    mz_zip_archive zip = {};
    if (!mz_zip_reader_init_file(&zip, kArchivePath, 0)) {
        snprintf(error, errorSize, "Cannot read AM2R.zip: %s",
                 mz_zip_get_error_string(mz_zip_get_last_error(&zip)));
        return false;
    }

    char prefix[PATH_MAX] = {};
    bool foundData = false;
    const mz_uint fileCount = mz_zip_reader_get_num_files(&zip);
    for (mz_uint i = 0; i < fileCount; ++i) {
        char name[PATH_MAX] = {};
        mz_zip_reader_get_filename(&zip, i, name, sizeof(name));
        const char *base = strrchr(name, '/');
        base = base ? base + 1 : name;
        if (strcmp(base, "data.win")) continue;
        size_t prefixLength = (size_t)(base - name);
        if (prefixLength >= sizeof(prefix)) continue;
        memcpy(prefix, name, prefixLength);
        prefix[prefixLength] = 0;
        foundData = safe_archive_prefix(prefix);
        if (foundData) break;
    }

    if (!foundData) {
        snprintf(error, errorSize, "AM2R.zip has no safe data.win root");
        mz_zip_reader_end(&zip);
        return false;
    }

    for (const char *relative : kRequiredArchiveFiles) {
        char member[PATH_MAX] = {};
        char destination[PATH_MAX] = {};
        snprintf(member, sizeof(member), "%s%s", prefix, relative);
        snprintf(destination, sizeof(destination), "%s/%s", kRuntimeDirectory, relative);
        int index = mz_zip_reader_locate_file(&zip, member, nullptr, MZ_ZIP_FLAG_CASE_SENSITIVE);
        if (index < 0) {
            snprintf(error, errorSize, "AM2R.zip missing %s", relative);
            mz_zip_reader_end(&zip);
            return false;
        }
        if (!mz_zip_reader_extract_to_file(&zip, (mz_uint)index, destination, 0)) {
            snprintf(error, errorSize, "AM2R.zip failed CRC/extract for %s", relative);
            mz_zip_reader_end(&zip);
            return false;
        }
    }

    mz_zip_reader_end(&zip);
    if (!write_archive_stamp(archiveStat)) {
        snprintf(error, errorSize, "Cannot write AM2R archive cache stamp");
        return false;
    }
    log_line(log, "archive_cache=extracted members=%zu bytes=%lld prefix=%s",
             sizeof(kRequiredArchiveFiles) / sizeof(kRequiredArchiveFiles[0]),
             (long long)archiveStat.st_size, prefix[0] ? prefix : "(flat)");
    return true;
}

void copy_session_log()
{
    int input = open(kSessionLogPath, O_RDONLY | O_CLOEXEC);
    if (input < 0) return;
    int output = open(kLogPath, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (output < 0) {
        close(input);
        return;
    }
    char buffer[16384];
    for (;;) {
        ssize_t count = read(input, buffer, sizeof(buffer));
        if (count <= 0) break;
        ssize_t offset = 0;
        while (offset < count) {
            ssize_t written = write(output, buffer + offset, (size_t)(count - offset));
            if (written <= 0) break;
            offset += written;
        }
    }
    close(output);
    close(input);
}

void signal_handler(int signal)
{
    gSignal = signal;
    if (gChild > 0) kill((pid_t)gChild, signal);
}

void install_signal_handlers()
{
    struct sigaction action = {};
    action.sa_handler = signal_handler;
    sigemptyset(&action.sa_mask);
    sigaction(SIGINT, &action, nullptr);
    sigaction(SIGHUP, &action, nullptr);
    sigaction(SIGTERM, &action, nullptr);
}

void show_error(const char *message)
{
    fprintf(stderr, "AM2R wrapper: %s\n", message);
    InfoMessage(message, 3500, "AM2R");
    for (int i = 0; i < 3500; ++i) {
        user_io_poll();
        frame_timer();
        input_poll(0);
        HandleUI();
        OsdUpdate();
        usleep(1000);
    }
}

[[noreturn]] void return_to_menu()
{
    // The pinned Main_MiSTer fpga loader restarts the current executable after
    // programming the requested core.  That gives this wrapper one short
    // second invocation with argv[1] == "menu.rbf"; the startup path below
    // immediately hands that already-loaded core to the stock MiSTer binary.
    fpga_load_rbf("menu.rbf");
    _exit(1);
}

pid_t spawn_runtime(StateControl &state, int session, const char *testPlayback,
                    int restoreSlot, FILE *log)
{
    pid_t child = fork();
    if (child != 0) return child;

    close(state.wrapperFd);
    prctl(PR_SET_PDEATHSIG, SIGTERM);
    cpu_set_t all;
    CPU_ZERO(&all);
    CPU_SET(0, &all);
    CPU_SET(1, &all);
    sched_setaffinity(0, sizeof(all), &all);

    int nullInput = open("/dev/null", O_RDONLY | O_CLOEXEC);
    if (nullInput >= 0) {
        dup2(nullInput, STDIN_FILENO);
        close(nullInput);
    }
    dup2(session, STDOUT_FILENO);
    dup2(session, STDERR_FILENO);
    if (session > STDERR_FILENO) close(session);

    // Main_MiSTer keeps a number of device, log, input, and control
    // descriptors open.  The forked game does not own any of them, and
    // allowing dmtcp_launch to inherit them makes those unrelated endpoints
    // part of every checkpoint.  In particular, non-stream sockets cannot be
    // reconstructed reliably by DMTCP on the MiSTer kernel.  Keep only the
    // deliberately prepared standard descriptors; the runner opens its own
    // framebuffer, input, shared-controller, GPU, and audio endpoints.
    long maximumFd = sysconf(_SC_OPEN_MAX);
    if (maximumFd < 0) maximumFd = 1024;
    for (int fd = STDERR_FILENO + 1; fd < maximumFd; ++fd) close(fd);

    if (chdir(kRuntimeDirectory) != 0) _exit(126);

    char port[16] = {};
    snprintf(port, sizeof(port), "%d", state.coordinatorPort);
    std::vector<char *> args;
    if (restoreSlot >= 0) {
        char checkpoint[PATH_MAX] = {};
        state_slot_path(restoreSlot, checkpoint, sizeof(checkpoint));
        args = {
            const_cast<char *>(kDmtcpRestart),
            const_cast<char *>("--join-coordinator"),
            const_cast<char *>("--coord-port"), port,
            const_cast<char *>("--ckptdir"), state.sessionDirectory,
            const_cast<char *>("--tmpdir"), state.tempDirectory,
            const_cast<char *>("--no-strict-checking"),
            const_cast<char *>("--quiet"), checkpoint, nullptr,
        };
        execve(kDmtcpRestart, args.data(), environ);
    } else {
        args = {
            const_cast<char *>(kDmtcpLaunch),
            const_cast<char *>("--join-coordinator"),
            const_cast<char *>("--coord-port"), port,
            const_cast<char *>("--ckptdir"), state.sessionDirectory,
            const_cast<char *>("--tmpdir"), state.tempDirectory,
            // Uncompressed DMTCP images trade disk space for short save/load
            // pauses.  DMTCP itself documents the compressed path as adding
            // seconds; restart auto-detects both old gzip and new raw images.
            const_cast<char *>("--no-gzip"),
            const_cast<char *>("--quiet"),
            const_cast<char *>(kRuntimeBinary),
            const_cast<char *>("/tmp/am2r-runtime/data.win"),
            const_cast<char *>("--renderer"),
            const_cast<char *>("software"),
            const_cast<char *>("--disable-log-colours"),
            const_cast<char *>("--save-folder"),
            const_cast<char *>(kSavePath),
        };
        if (access("/tmp/am2r-profile-gml", F_OK) == 0) {
            args.push_back(const_cast<char *>("--profile-gml-scripts"));
            args.push_back(const_cast<char *>("120"));
        }
        if (access("/tmp/am2r-trace-projectiles", F_OK) == 0) {
            args.push_back(const_cast<char *>("--trace-collisions"));
            args.push_back(const_cast<char *>("oDoor"));
            args.push_back(const_cast<char *>("--trace-collisions"));
            args.push_back(const_cast<char *>("oMAlpha"));
            args.push_back(const_cast<char *>("--trace-collisions"));
            args.push_back(const_cast<char *>("oMAlphaShell"));
            args.push_back(const_cast<char *>("--trace-instance-lifecycles"));
            args.push_back(const_cast<char *>("oBeam"));
            args.push_back(const_cast<char *>("--trace-instance-lifecycles"));
            args.push_back(const_cast<char *>("oMissile"));
        }
        if (testPlayback && testPlayback[0] != '\0') {
            args.push_back(const_cast<char *>("--seed"));
            args.push_back(const_cast<char *>("1"));
            args.push_back(const_cast<char *>("--playback-inputs"));
            args.push_back(const_cast<char *>(testPlayback));
        }
        args.push_back(nullptr);
        execve(kDmtcpLaunch, args.data(), environ);
    }
    log_line(log, "runtime_exec_failed errno=%d", errno);
    _exit(127);
}

int run_child(FILE *wrapperLog)
{
    if (prctl(PR_SET_CHILD_SUBREAPER, 1) != 0) {
        log_line(wrapperLog, "error=savestate_subreaper errno=%d", errno);
        return 126;
    }
    int staleStatus = 0;
    while (waitpid(-1, &staleStatus, WNOHANG) > 0) {}

    char testPlayback[PATH_MAX] = {};
    FILE *testTrigger = fopen(kTestPlaybackTrigger, "r");
    if (testTrigger) {
        if (fgets(testPlayback, sizeof(testPlayback), testTrigger)) {
            testPlayback[strcspn(testPlayback, "\r\n")] = '\0';
            if (strncmp(testPlayback, "/media/fat/games/", 17) != 0 ||
                access(testPlayback, R_OK) != 0) {
                log_line(wrapperLog, "test_playback_rejected path=%s", testPlayback);
                testPlayback[0] = '\0';
            } else {
                log_line(wrapperLog, "test_playback=%s", testPlayback);
            }
        }
        fclose(testTrigger);
        unlink(kTestPlaybackTrigger);
    }

    StateControl state;
    cleanup_ephemeral_children("/tmp", "am2r-dmtcp-", wrapperLog);
    cleanup_ephemeral_children(kStateRoot, ".session-", wrapperLog);
    state.coordinatorPort = 43000 + ((int)getpid() % 900);
    snprintf(state.sessionDirectory, sizeof(state.sessionDirectory),
             "%s/.session-%d", kStateRoot, (int)getpid());
    snprintf(state.tempDirectory, sizeof(state.tempDirectory),
             "/tmp/am2r-dmtcp-%d", (int)getpid());
	if (!make_directory(state.sessionDirectory) || !make_directory(state.tempDirectory)) {
        log_line(wrapperLog, "error=savestate_directory errno=%d", errno);
        remove_ephemeral_tree(state.tempDirectory);
        remove_ephemeral_tree(state.sessionDirectory);
		return 126;
	}
	if (!create_joy_shm(wrapperLog)) {
		remove_ephemeral_tree(state.tempDirectory);
		remove_ephemeral_tree(state.sessionDirectory);
		return 126;
	}
    unlink(AM2R_STATE_RESUME_PATH);
    unlink(AM2R_STATE_RUNNER_SOCKET);
    state.wrapperFd = create_state_socket(wrapperLog);
	if (state.wrapperFd < 0 || !start_coordinator(state, wrapperLog)) {
        if (state.wrapperFd >= 0) close(state.wrapperFd);
		unlink(AM2R_STATE_WRAPPER_SOCKET);
		cleanup_joy_shm();
        remove_ephemeral_tree(state.tempDirectory);
        remove_ephemeral_tree(state.sessionDirectory);
        return 126;
    }

    int session = open(kSessionLogPath, O_WRONLY | O_CREAT | O_TRUNC | O_APPEND, 0644);
	if (session < 0) {
        stop_dmtcp(state, wrapperLog);
        close(state.wrapperFd);
		unlink(AM2R_STATE_WRAPPER_SOCKET);
		cleanup_joy_shm();
        remove_ephemeral_tree(state.tempDirectory);
        remove_ephemeral_tree(state.sessionDirectory);
        log_line(wrapperLog, "error=cannot_open_session_log errno=%d", errno);
        return 126;
    }

    pid_t child = spawn_runtime(state, session, testPlayback, -1, wrapperLog);
	if (child < 0) {
        close(session);
        stop_dmtcp(state, wrapperLog);
        close(state.wrapperFd);
		unlink(AM2R_STATE_WRAPPER_SOCKET);
		cleanup_joy_shm();
        remove_ephemeral_tree(state.tempDirectory);
        remove_ephemeral_tree(state.sessionDirectory);
        return 126;
    }
    gChild = child;
    log_line(wrapperLog, "child_pid=%d runtime=%s dmtcp_port=%d",
             child, kRuntimeBinary, state.coordinatorPort);

    cpu_set_t wrapperCpu;
    CPU_ZERO(&wrapperCpu);
    CPU_SET(0, &wrapperCpu);
    sched_setaffinity(0, sizeof(wrapperCpu), &wrapperCpu);

    int status = 0;
    for (;;) {
        handle_state_events(state, child, wrapperLog);
        if (state.loadRequested) {
            state.loadRequested = false;
            InfoMessage("Loading save state", 1000, "AM2R");
            stop_dmtcp(state, wrapperLog);
        }

        pid_t result = waitpid(-1, &status, WNOHANG);
        if (result > 0 && result != child) {
            log_line(wrapperLog, "auxiliary_reaped pid=%d", result);
            continue;
        }
        if (result == child) {
            if (WIFSIGNALED(status))
                log_line(wrapperLog, "active_exit pid=%d signal=%d", child, WTERMSIG(status));
            else if (WIFEXITED(status))
                log_line(wrapperLog, "active_exit pid=%d code=%d", child, WEXITSTATUS(status));

            if (state.freshRestartRequested) {
                state.freshRestartRequested = false;
                stop_dmtcp(state, wrapperLog);
                unlink(AM2R_STATE_RESUME_PATH);
                unlink(AM2R_STATE_RUNNER_SOCKET);
                if (!start_coordinator(state, wrapperLog)) {
                    status = 126 << 8;
                    break;
                }
                child = spawn_runtime(state, session, nullptr, -1, wrapperLog);
                if (child < 0) {
                    status = 126 << 8;
                    break;
                }
                gChild = child;
                log_line(wrapperLog, "reset_restarted pid=%d port=%d",
                         child, state.coordinatorPort);
                continue;
            }
            if (state.loadSlot >= 0) {
                int restoreSlot = state.loadSlot;
                state.loadSlot = -1;
                unlink(AM2R_STATE_RESUME_PATH);
                unlink(AM2R_STATE_RUNNER_SOCKET);
                if (!start_coordinator(state, wrapperLog)) {
                    status = 126 << 8;
                    break;
                }
                child = spawn_runtime(state, session, nullptr, restoreSlot, wrapperLog);
                if (child < 0) {
                    status = 126 << 8;
                    break;
                }
                gChild = child;
                state.activeRestoreSlot = restoreSlot;
                release_quiesced_runner(0, wrapperLog);
                log_line(wrapperLog, "savestate_restart slot=%d pid=%d port=%d",
                         restoreSlot + 1, child, state.coordinatorPort);
                continue;
            }
            if (state.activeRestoreSlot >= 0) {
                int failedSlot = state.activeRestoreSlot;
                state.activeRestoreSlot = -1;
                log_line(wrapperLog, "savestate_restart_failed slot=%d", failedSlot + 1);
                InfoMessage("Save state is corrupt or incompatible", 2200, "AM2R");
                if (!start_coordinator(state, wrapperLog)) {
                    status = 126 << 8;
                    break;
                }
                child = spawn_runtime(state, session, nullptr, -1, wrapperLog);
                if (child < 0) {
                    status = 126 << 8;
                    break;
                }
                gChild = child;
                log_line(wrapperLog, "savestate_recovery_new_game pid=%d port=%d",
                         child, state.coordinatorPort);
                continue;
            }
            break;
        }
        if (result < 0 && errno != EINTR) {
            status = 126 << 8;
            break;
        }
        if (is_fpga_ready(1)) {
            user_io_poll();
            frame_timer();
			input_poll(0);
			publish_joy_masks(state, wrapperLog);
            HandleUI();
            OsdUpdate();
            handle_state_osd(state, child, wrapperLog);
        }
        usleep(1000);
    }

    gChild = -1;
    stop_dmtcp(state, wrapperLog);
    int auxiliaryStatus = 0;
    pid_t auxiliary = -1;
    while ((auxiliary = waitpid(-1, &auxiliaryStatus, WNOHANG)) > 0)
        log_line(wrapperLog, "auxiliary_reaped pid=%d", auxiliary);
    close(session);
    close(state.wrapperFd);
    unlink(AM2R_STATE_WRAPPER_SOCKET);
    unlink(AM2R_STATE_RUNNER_SOCKET);
	unlink(AM2R_STATE_RESUME_PATH);
	cleanup_joy_shm();
    remove_ephemeral_tree(state.tempDirectory);
    remove_ephemeral_tree(state.sessionDirectory);
    copy_session_log();

    if (WIFEXITED(status)) return WEXITSTATUS(status);
    if (WIFSIGNALED(status)) return 128 + WTERMSIG(status);
    return status;
}

} // namespace

int am2r_wrapper_run(int argc, char *argv[])
{
    FILE *wrapperLog = fopen("/tmp/am2r-wrapper.log", "a");
    if (wrapperLog) setvbuf(wrapperLog, nullptr, _IOLBF, 0);
    log_line(wrapperLog, "wrapper=MiSTer_AM2R argc=%d pid=%d", argc, getpid());

    const bool handingToMenu = argc > 1 && !strcasecmp(argv[1], "menu.rbf");
    if (handingToMenu) {
        // user_io_init() notices MiSTer.ini's stock `main` and execs it before
        // returning.  Clear the snapshot subreaper policy before entering that
        // framework path, and reap anything already inherited by this process.
        int inheritedStatus = 0;
        while (waitpid(-1, &inheritedStatus, WNOHANG) > 0) {}
        prctl(PR_SET_CHILD_SUBREAPER, 0);
        log_line(wrapperLog, "handoff=stock_menu");
    }

    if (!is_fpga_ready(1)) {
        log_line(wrapperLog, "error=fpga_not_ready");
        if (wrapperLog) fclose(wrapperLog);
        return 1;
    }

    FindStorage();
    user_io_init(argc > 1 ? argv[1] : kCoreName, argc > 2 ? argv[2] : nullptr);
	// This core owns its native 240p timing. Explicitly clear any framebuffer
	// route inherited from the previously loaded core, as 3s-mister-arm does
	// for its native-video mode.
	set_vga_fb(0);
	video_fb_enable(0);
	log_line(wrapperLog, "video_route=native vga_fb=0 fb_enable=0");
    mgl_get()->done = 1;
    install_signal_handlers();

    // Reap any stale child inherited through an executable handoff. This also
    // makes recovery from an interrupted development build self-cleaning.
    int staleStatus = 0;
    while (waitpid(-1, &staleStatus, WNOHANG) > 0) {}

    if (handingToMenu) {
        if (wrapperLog) fclose(wrapperLog);
        app_restart("menu.rbf", nullptr, kMenuExec);
        _exit(1);
    }

    const char *reportedCore = user_io_get_core_name();
    if (!reportedCore || strcasecmp(reportedCore, kCoreName)) {
        char error[128] = {};
        snprintf(error, sizeof(error), "Expected AM2R core, found %s",
                 reportedCore ? reportedCore : "unknown");
        show_error(error);
        if (wrapperLog) fclose(wrapperLog);
        return_to_menu();
    }

    char error[256] = {};
    if (!prepare_directories(wrapperLog, error, sizeof(error)) || !file_exists(kRuntimeBinary) ||
        !file_exists(kDmtcpCoordinator) || !file_exists(kDmtcpCommand) ||
        !file_exists(kDmtcpLaunch) || !file_exists(kDmtcpRestart) ||
        !prepare_game_archive(wrapperLog, error, sizeof(error))) {
        if (!error[0] && !file_exists(kRuntimeBinary))
            snprintf(error, sizeof(error), "Missing /media/fat/games/am2r/bin/butterscotch");
        else if (!error[0])
            snprintf(error, sizeof(error), "Missing /media/fat/games/am2r/dmtcp runtime");
        log_line(wrapperLog, "error=%s", error);
        show_error(error);
        if (wrapperLog) fclose(wrapperLog);
        return_to_menu();
    }

    if (!calculate_runtime_crc32(&gRuntimeCrc32)) {
        snprintf(error, sizeof(error), "Cannot fingerprint AM2R runtime: %s", strerror(errno));
        log_line(wrapperLog, "error=%s", error);
        show_error(error);
        if (wrapperLog) fclose(wrapperLog);
        return_to_menu();
    }

    log_line(wrapperLog, "core=%s archive=%s runtime_crc32=%08x",
             reportedCore, kArchivePath, gRuntimeCrc32);
    int exitCode = run_child(wrapperLog);
    log_line(wrapperLog, "child_exit=%d signal=%d", exitCode, (int)gSignal);
    if (wrapperLog) fclose(wrapperLog);
    return_to_menu();
}
