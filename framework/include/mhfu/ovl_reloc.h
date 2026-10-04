/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* MWo3 overlay relocator: moves a position-dependent overlay (em*.ovl, stage*.ovl) to another
 * address. */
#ifndef MHFU_OVL_RELOC_H
#define MHFU_OVL_RELOC_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    char     magic[4];          /* "MWo3" */
    uint32_t overlay_id;
    uint32_t load_address;       /* address of file offset 0 */
    uint32_t text_size;          /* text follows this header */
    uint32_t data_size;          /* follows text */
    uint32_t bss_size;           /* follows data; the footprint ends with it */
    uint32_t static_init_start;  /* ctor list [start, end), inside data */
    uint32_t static_init_end;
    char     name[32];
} mhfu_ovl_header_t;

typedef struct {
    int      ok;                 /* 1 = success */
    uint32_t n_jump;             /* j/jal targets moved */
    uint32_t n_hi;               /* luis moved (a 64 KB delta leaves every %lo as it is) */
    uint32_t n_data;             /* data words moved */
    uint32_t new_base;           /* load_address + delta */
} mhfu_ovl_reloc_stats_t;

/* Moves image (header, text and data: image_size >= 64 + text + data) in place by delta, a
 * multiple of 64 KB, as mhfu.reloc does: every reference into the footprint [load, bss end),
 * and the header's load address and ctor list. References below the load address stay (under
 * an em overlay lies game_sub's bss). Takes about one byte per instruction and 1 KB per branch
 * depth from the heap. stats.ok is 0, the image untouched, on a bad header or delta, a lui that
 * forms addresses both in and out of the footprint, a jump the move puts out of reach, or no
 * memory. */
mhfu_ovl_reloc_stats_t mhfu_ovl_relocate(void *image, uint32_t image_size, int32_t delta);

#ifdef __cplusplus
}
#endif

#endif /* MHFU_OVL_RELOC_H */
