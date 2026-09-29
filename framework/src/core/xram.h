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
    int            primed;
    SceOff         st_size;
    ScePspDateTime st_mtime;
    uint32_t       buf;           /* xram copy of the edited PAC, 0 = none */
    uint32_t       buf_cap;
    uint32_t       obuf;          /* xram copy of the original PAC, the species match */
    uint32_t       obuf_cap;
    uint32_t       file_size;
    int            nsubs;
    sub_ent_t      subs[MHFU_MAX_SUBS];
    uint32_t       hits;          /* in-game overwrites */
    uint32_t       applied_addr;  /* last buffer overwritten */
    /* First word where edit != orig. The 256-byte header is the same in both, and the
     * engine reloads a buffer at the same address, so this word tells pristine from done. */
    int            has_diff;
    uint32_t       diff_off;
    uint32_t       diff_orig;
    uint32_t       diff_edit;
    /* Relocate: buf holds a bigger PAC and get_subresource is redirected to it,
     * the only way past the raw buffer's fixed heap block without a disk edit. */
    int            relocate;
    uint32_t       orig_size;
    uint32_t       redirects;
    /* Relocate entries keep only the original's header in user RAM; the grown PAC
     * is staged into xram on first use, so the real PSP's volatile stays free until then. */
    char           orig_path[160];
    uint8_t        orig_hdr[256];
    int            staged;        /* staging attempted (buf valid once it succeeded) */
} inject_entry_t;

extern inject_entry_t mhfu_inject_tab[MHFU_INJECT_MAX];

/* Stages a relocate entry's grown PAC into xram; 1 once buf is set. inject.cpp. */
int mhfu_inject_stage_relocate(inject_entry_t *e);

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
