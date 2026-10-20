/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* brain.cpp's pure parts: checking a stick container, relocating its image and pointing a species
 * row's header words at it. Free of PSP headers, so the host test compiles them
 * (tests/brain_host.cpp). The layout is docs/formats/ai_script.md's Container section. */
#ifndef MHFU_CORE_BRAIN_LOGIC_H
#define MHFU_CORE_BRAIN_LOGIC_H

#include <stdint.h>
#include <string.h>

#include "mhfu/brain.h"
#include "addresses.gen.h"

#define BRAIN_MAGIC   0x4941484Du  /* 'MHAI' */
#define BRAIN_VERSION 1
#define BRAIN_HEAD    0x40
#define BRAIN_ROWS    MHFU_SPECIES_TABLE_COUNT   /* species rows */
#define BRAIN_WORDS   MHFU_SCRIPT_HEADERS_COUNT  /* header words in a row, one per map type */

/* Where a container is staged: the raw window in PPSSPP's 64 MB mode, else a user-partition block
 * (a real PSP, PPSSPP's 32 MB mode), which a quest exit leaves alone as it does volatile memory.
 * The installs keep at most BRAIN_STAGE_PART_MAX bytes of blocks, 256 KB: four times the largest
 * container (em1's, 63 KB), a ninth of what a real PSP has free in-game. */
enum { BRAIN_STAGE_RAW = 1, BRAIN_STAGE_PART };
#define BRAIN_STAGE_PART_MAX 0x40000u

typedef struct {
    int      route;    /* BRAIN_STAGE_* */
    uint32_t budget;   /* partition bytes a block may take now; 0 for the raw route */
} brain_stage_t;

/* The route for `raw` (xram's RAW mode) when the installs keep `taken` bytes of blocks already. */
static inline brain_stage_t brain_stage_plan(int raw, uint32_t taken)
{
    brain_stage_t p = {BRAIN_STAGE_RAW, 0};
    if (raw) return p;
    p.route = BRAIN_STAGE_PART;
    p.budget = taken < BRAIN_STAGE_PART_MAX ? BRAIN_STAGE_PART_MAX - taken : 0;
    return p;
}

static inline const char *brain_stage_name(int route)
{
    return route == BRAIN_STAGE_RAW ? "raw" : "partition";
}

/* The container's header fields. */
enum {
    BRAIN_AT_VERSION = 0x04,
    BRAIN_AT_ROW     = 0x06,
    BRAIN_AT_ID      = 0x08,
    BRAIN_AT_LOAD    = 0x0C,
    BRAIN_AT_IMAGE   = 0x10,   /* offset, size */
    BRAIN_AT_RELOCS  = 0x18,   /* offset, count */
    BRAIN_AT_PATCHES = 0x20,   /* offset, count */
};
#define BRAIN_PATCH_BYTES 8  /* {u32 native header address, u32 image offset} */

static inline uint16_t brain_u16(const uint8_t *p)
{
    uint16_t v;
    memcpy(&v, p, 2);
    return v;
}

static inline uint32_t brain_u32(const uint8_t *p)
{
    uint32_t v;
    memcpy(&v, p, 4);
    return v;
}

static inline void brain_put(uint8_t *p, uint32_t v) { memcpy(p, &v, 4); }

/* Where a checked container's parts are: offsets from the file's first byte. */
typedef struct {
    uint32_t row, id;
    uint32_t image, size;       /* offset and bytes */
    uint32_t relocs, nrelocs;   /* offset and count */
    uint32_t patches, npatches;
} brain_view_t;

static inline int brain_inside(uint32_t file_size, uint32_t off, uint32_t n, uint32_t each)
{
    return (uint64_t)off + (uint64_t)n * each <= file_size;
}

/* 0 when f starts with a stick container's head for a row below `rows` (n bytes read, at least
 * BRAIN_HEAD of them), else a MHFU_BRAIN_E_*; v takes the head's fields. Where its sections lie
 * is for brain_check, which needs the whole file. */
static inline int brain_head(const uint8_t *f, uint32_t n, uint32_t rows, brain_view_t *v)
{
    if (n < BRAIN_HEAD) return MHFU_BRAIN_E_SHORT;
    if (brain_u32(f) != BRAIN_MAGIC) return MHFU_BRAIN_E_MAGIC;
    if (brain_u16(f + BRAIN_AT_VERSION) != BRAIN_VERSION) return MHFU_BRAIN_E_VERSION;
    if (brain_u32(f + BRAIN_AT_LOAD) != 0) return MHFU_BRAIN_E_LOAD;
    v->row = brain_u16(f + BRAIN_AT_ROW);
    if (v->row >= rows) return MHFU_BRAIN_E_ROW;
    v->id = brain_u32(f + BRAIN_AT_ID);
    v->image = brain_u32(f + BRAIN_AT_IMAGE);
    v->size = brain_u32(f + BRAIN_AT_IMAGE + 4);
    v->relocs = brain_u32(f + BRAIN_AT_RELOCS);
    v->nrelocs = brain_u32(f + BRAIN_AT_RELOCS + 4);
    v->patches = brain_u32(f + BRAIN_AT_PATCHES);
    v->npatches = brain_u32(f + BRAIN_AT_PATCHES + 4);
    return 0;
}

/* Two heads of one script: the same row, image hash and size, and counts of relocs and patches.
 * A reinstall of it changes nothing, so it stages nothing. */
static inline int brain_same(const brain_view_t *a, const brain_view_t *b)
{
    return a->row == b->row && a->id == b->id && a->size == b->size && a->nrelocs == b->nrelocs
           && a->npatches == b->npatches;
}

/* 0 when f, n bytes read from a file, is a stick container for a row below `rows`, else a
 * MHFU_BRAIN_E_*. Everything the other functions read from f lies inside it, and inside the image
 * for a reloc word and a header: a 4-aligned word, a header with an address. */
static inline int brain_check(const uint8_t *f, uint32_t n, uint32_t rows, brain_view_t *v)
{
    const int rc = brain_head(f, n, rows, v);
    if (rc) return rc;
    if (v->image < BRAIN_HEAD || v->image % 4 || !brain_inside(n, v->image, v->size, 1)
        || !brain_inside(n, v->relocs, v->nrelocs, 4)
        || !brain_inside(n, v->patches, v->npatches, BRAIN_PATCH_BYTES))
        return MHFU_BRAIN_E_SECTION;
    for (uint32_t i = 0; i < v->nrelocs; i++) {
        const uint32_t at = brain_u32(f + v->relocs + 4 * i);
        if (at % 4 || !brain_inside(v->size, at, 1, 4)) return MHFU_BRAIN_E_RELOC;
    }
    if (!v->npatches) return MHFU_BRAIN_E_PATCH;
    for (uint32_t i = 0; i < v->npatches; i++) {
        const uint8_t *e = f + v->patches + BRAIN_PATCH_BYTES * i;
        const uint32_t at = brain_u32(e + 4);
        if (!brain_u32(e) || at % 4 || at >= v->size) return MHFU_BRAIN_E_PATCH;
    }
    return 0;
}

/* The image's pointer words written for `load` (each had base 0), and `load` in the header; a
 * file read fresh, which brain_check passed. MHFU_BRAIN_E_LOAD for one placed already, so a
 * second pass cannot add `load` twice. */
static inline int brain_relocate(uint8_t *f, const brain_view_t *v, uint32_t load)
{
    if (brain_u32(f + BRAIN_AT_LOAD) != 0) return MHFU_BRAIN_E_LOAD;
    for (uint32_t i = 0; i < v->nrelocs; i++) {
        uint8_t *w = f + v->image + brain_u32(f + v->relocs + 4 * i);
        brain_put(w, brain_u32(w) + load);
    }
    brain_put(f + BRAIN_AT_LOAD, load);
    return 0;
}

/* The row's BRAIN_WORDS words, in `row` and at address `at`: each lying wholly inside the chunk
 * [buf, buf + bytes) that holds a header's native address becomes load + its image offset (each
 * word on its own, so a row split over two chunks takes two calls). How many changed, their map
 * types as bits ORed into *mask unless it is 0. f is the relocated container at `load`. */
static inline int brain_patch_row(const uint8_t *f, const brain_view_t *v, uint32_t load,
                                  uint8_t *row, uint32_t at, uint32_t buf, uint32_t bytes,
                                  uint32_t *mask)
{
    int n = 0;
    for (uint32_t k = 0; k < BRAIN_WORDS; k++) {
        const uint64_t word = (uint64_t)at + 4 * k;
        if (word < buf || word + 4 > (uint64_t)buf + bytes) continue;
        const uint32_t w = brain_u32(row + 4 * k);
        for (uint32_t i = 0; i < v->npatches; i++) {
            const uint8_t *e = f + v->patches + BRAIN_PATCH_BYTES * i;
            if (brain_u32(e) != w) continue;
            brain_put(row + 4 * k, load + brain_u32(e + 4));
            if (mask) *mask |= 1u << k;
            n++;
            break;
        }
    }
    return n;
}

/* brain_patch_row's inverse over the whole row, for the install that is replaced: a word that
 * holds load + a header's image offset goes back to that header's native address. How many. */
static inline int brain_unpatch_row(const uint8_t *f, const brain_view_t *v, uint32_t load,
                                    uint8_t *row)
{
    int n = 0;
    for (uint32_t k = 0; k < BRAIN_WORDS; k++) {
        const uint32_t w = brain_u32(row + 4 * k);
        for (uint32_t i = 0; i < v->npatches; i++) {
            const uint8_t *e = f + v->patches + BRAIN_PATCH_BYTES * i;
            if (load + brain_u32(e + 4) != w) continue;
            brain_put(row + 4 * k, brain_u32(e));
            n++;
            break;
        }
    }
    return n;
}

/* Bit k set when the row's word k lies in the image [load, load + size). */
static inline uint32_t brain_row_words(const uint8_t *row, uint32_t load, uint32_t size)
{
    uint32_t mask = 0;
    for (uint32_t k = 0; k < BRAIN_WORDS; k++) {
        const uint32_t w = brain_u32(row + 4 * k);
        if (w >= load && w - load < size) mask |= 1u << k;
    }
    return mask;
}

static inline const char *brain_error_text(int rc)
{
    switch (rc) {
    case MHFU_BRAIN_E_ARG:     return "no path";
    case MHFU_BRAIN_E_STAGE:   return "not staged (unreadable, or no room left to keep it)";
    case MHFU_BRAIN_E_SHORT:   return "shorter than a container's header";
    case MHFU_BRAIN_E_MAGIC:   return "not an MHAI container";
    case MHFU_BRAIN_E_VERSION: return "a container version this does not read";
    case MHFU_BRAIN_E_LOAD:    return "not a stick container (its load address is not 0)";
    case MHFU_BRAIN_E_ROW:     return "its row is no species row";
    case MHFU_BRAIN_E_SECTION: return "a section lies outside the file";
    case MHFU_BRAIN_E_RELOC:   return "a relocation lies outside the image";
    case MHFU_BRAIN_E_PATCH:   return "a header patch is missing, misplaced or has no address";
    case MHFU_BRAIN_E_FULL:    return "every row slot is in use";
    case MHFU_BRAIN_E_HOOK:    return "the chunk listener could not be registered";
    default:                   return "unknown error";
    }
}

#endif /* MHFU_CORE_BRAIN_LOGIC_H */
