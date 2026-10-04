/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/*
 * Private to inject.cpp, xram.cpp and volatile_interposer.cpp: the injected-file
 * table and the extra RAM its buffers live in.
 */
#ifndef MHFU_CORE_XRAM_H
#define MHFU_CORE_XRAM_H

#include <stdint.h>
#include <pspkerneltypes.h>    /* SceOff, ScePspDateTime */

#ifdef __cplusplus
extern "C" {
#endif

#define MHFU_INJECT_MAX   8
#define MHFU_MAX_SUBS     8

typedef struct {
    uint32_t off;     /* sub offset within the PAC */
    uint32_t size;
    uint32_t magic;   /* first u32 of the sub */
} sub_ent_t;

typedef struct {
    int            used;
    uint32_t       file_id;
    char           path[160];
    int            primed;        /* st_size and st_mtime hold the file read last */
    SceOff         st_size;
    ScePspDateTime st_mtime;
    /* xram blocks: kept for the entry's next read, since a bump allocator cannot free them */
    uint32_t       buf;           /* the edited PAC, or a relocate entry's grown one */
    uint32_t       buf_cap;
    uint32_t       obuf;          /* the original PAC, the species match */
    uint32_t       obuf_cap;
    uint32_t       file_size;
    int            nsubs;
    sub_ent_t      subs[MHFU_MAX_SUBS];
    uint32_t       hits;          /* in-game overwrites */
    /* First word where edit != orig; set only once both are read at the same size. The
     * 256-byte header is the same in both, and the engine reloads a buffer at the same
     * address, so this word tells pristine from done. */
    int            has_diff;
    uint32_t       diff_off;
    uint32_t       diff_orig;
    uint32_t       diff_edit;
    /* Relocate: buf holds a bigger PAC and get_subresource is redirected to it,
     * the only way past the raw buffer's fixed heap block without a disk edit. */
    int            relocate;
    uint32_t       orig_size;
    uint32_t       redirects;
    /* Relocate entries keep only the original's header in user RAM; the grown PAC is staged
     * into xram on first use, so the real PSP's volatile stays free until then. */
    uint8_t        orig_hdr[256];
    int            ready;         /* buf holds the grown PAC */
    int            tried;         /* a staging attempt failed; not retried until released */
} inject_entry_t;

extern inject_entry_t mhfu_inject_tab[MHFU_INJECT_MAX];

/* A relocate entry's grown PAC now sits in [buf, buf + cap): the one place that marks it
 * ready and hands its skeleton to the joint fix. inject.cpp. */
void mhfu_inject_relocate_ready(inject_entry_t *e, uint32_t buf, uint32_t cap, uint32_t size);
/* The block under a relocate entry is gone (volatile unlocked): forget it. inject.cpp. */
void mhfu_inject_relocate_drop(inject_entry_t *e);

/* Where xram is, picked once by mhfu_xram_init. */
enum { XRAM_UNDECIDED = 0, XRAM_RAW, XRAM_VOLATILE };
extern int mhfu_xram_mode;

/* 16-byte-aligned bump allocation from xram; 0 when exhausted or the lock is busy. */
uint32_t mhfu_xram_alloc(uint32_t n);

/* Appends a line to ms0:/PSP/mhfu_brute_debug.txt; skipped while ms0 I/O is unsafe. */
void mhfu_xram_log(const char *fmt, ...);

#ifdef __cplusplus
}
#endif

#endif /* MHFU_CORE_XRAM_H */
