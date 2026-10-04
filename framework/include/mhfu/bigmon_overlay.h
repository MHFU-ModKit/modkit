/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* Load a second big-monster AI overlay (em*.ovl) at a fresh address: the engine loads every
 * em overlay to one load address, so a second family has nowhere else to go. */
#ifndef MHFU_BIGMON_OVERLAY_H
#define MHFU_BIGMON_OVERLAY_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    int      uid;          /* partition block, or -1 (extra RAM, volatile RAM, native slot) */
    int32_t  delta;        /* 64 KB aligned */
    uint32_t new_load;     /* relocated load address: the footprint starts here */
    uint32_t img_size;     /* header, text and data */
    uint32_t foot_size;    /* img_size plus bss */
    uint32_t si_start;     /* relocated static-initializer list [si_start, si_end) */
    uint32_t si_end;
} mhfu_ovl_region_t;

/* Read the overlay at path, place it (the native slot if free, else a partition block, the
 * extra-RAM window or volatile RAM), relocate it and zero its bss. Idempotent per session.
 * Returns 0, or negative. Does not wire it to the engine. */
int mhfu_ovl_load_relocated(const char *path, mhfu_ovl_region_t *out);

/* Run the relocated overlay's static initializers once, after placement and before use. */
void mhfu_ovl_run_static_inits(const mhfu_ovl_region_t *r);

#ifdef __cplusplus
}
#endif

#endif /* MHFU_BIGMON_OVERLAY_H */
