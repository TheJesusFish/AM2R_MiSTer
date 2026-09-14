#ifndef AM2R_JOY_SHM_H
#define AM2R_JOY_SHM_H

#include <stdint.h>

#define AM2R_JOY_SHM_PATH "/dev/shm/am2r-joy"
#define AM2R_JOY_SHM_ENV "AM2R_JOY_SHM"
#define AM2R_JOY_SHM_MAGIC 0x524a3241u /* "A2JR" little-endian */
#define AM2R_JOY_SHM_VERSION 2u
#define AM2R_JOY_MAX_PLAYERS 2

typedef struct {
	uint32_t magic;
	uint32_t version;
	uint32_t joy_mask[AM2R_JOY_MAX_PLAYERS];
	uint32_t crt_ui_inset;
} Am2rJoyShm;

#endif
