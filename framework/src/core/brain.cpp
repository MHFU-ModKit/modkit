/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* AI scripts from the memory stick (mhfu/brain.h).
 *
 *   install   stages the container (raw extra RAM, else a partition block), checks and relocates
 *             it, and points its row's header words at the image if the overlay holds them now
 *   chunk     every DATA.BIN chunk decrypt: the words of the row it holds go to the image
 *
 * The listener runs on the game thread and install on the Lua thread, so a reinstall fills the
 * record the listener is not using and then flips g_cur; the listener never reads a record that
 * is being written. Data writes only: a monster already running keeps the script it has. */
#include "mhfu/brain.h"
#include "mhfu/log.h"
#include "addresses.gen.h"
#include "internal.h"
#include "brain_logic.h"
#include "xram.h"

typedef struct {
    uint32_t     file;     /* the staged container, its image relocated */
    uint32_t     load;     /* the image's address */
    brain_view_t view;
    uint32_t     patched;  /* row words pointed at the image: the listener's count */
} brain_rec_t;

static brain_rec_t g_rec[MHFU_BRAIN_MAX][2];   /* by slot; g_cur says which is live */
static volatile uint32_t g_cur[MHFU_BRAIN_MAX];
static volatile uint32_t g_count;              /* slots in use, filled in order */
static uint32_t g_taken;                       /* partition bytes the installs keep, never freed */

static brain_rec_t *live(uint32_t slot) { return &g_rec[slot][g_cur[slot]]; }

/* row's slot; g_count when it has none yet, which is MHFU_BRAIN_MAX once every slot is taken. */
static uint32_t slot_of(uint32_t row)
{
    uint32_t slot = 0;
    while (slot < g_count && live(slot)->view.row != row) slot++;
    return slot;
}

static uint32_t row_addr(uint32_t row) { return MHFU_SCRIPT_HEADERS + row * MHFU_SPECIES_STRIDE; }

/* The words of r's row inside [buf, buf + bytes) to the image, logged when any moved. */
static void apply(brain_rec_t *r, uint32_t buf, uint32_t bytes)
{
    const uint32_t at = row_addr(r->view.row);
    uint32_t mask = 0;
    const int n = brain_patch_row((const uint8_t *)(uintptr_t)r->file, &r->view, r->load,
                                  (uint8_t *)(uintptr_t)at, at, buf, bytes, &mask);
    if (!n) return;
    r->patched += (uint32_t)n;
    mhfu_log("[brain] row %u: %d word(s) to image 0x%08X, map types 0x%05X, in 0x%08X+0x%X",
             (unsigned)r->view.row, n, (unsigned)r->load, (unsigned)mask, (unsigned)buf,
             (unsigned)bytes);
}

static void on_chunk(uint32_t buf, uint32_t bytes)
{
    const uint32_t n = g_count;
    for (uint32_t i = 0; i < n; i++) apply(live(i), buf, bytes);
}

/* The container into its route's memory, at install, not at the first quest-load patch: the read
 * needs ms0, which a load closes, and a few KB beside the resident Lua slab is no new footprint.
 * Its address and *size, 0 when not staged; *route says where. */
static uint32_t stage(const char *path, uint32_t *size, int *route)
{
    if (!mhfu_xram_mode) mhfu_xram_init();
    const brain_stage_t plan = brain_stage_plan(mhfu_xram_mode == XRAM_RAW, g_taken);
    *route = plan.route;
    if (plan.route == BRAIN_STAGE_RAW) return mhfu_xram_stage_file(path, size);
    uint32_t taken = 0;
    const uint32_t at = mhfu_xram_stage_part(path, plan.budget, size, &taken);
    g_taken += at ? taken : 0;
    return at;
}

static int fail(const char *path, int rc)
{
    mhfu_log("[brain] %s: %s", path, brain_error_text(rc));
    return rc;
}

extern "C" int mhfu_brain_install(const char *path)
{
    static int s_listening;
    if (!path || !*path) return MHFU_BRAIN_E_ARG;
    /* the head first: the live script again stages nothing, and a full table takes no block */
    uint8_t head[BRAIN_HEAD];
    if (mhfu_xram_peek(path, head, sizeof(head)) != sizeof(head))
        return fail(path, MHFU_BRAIN_E_STAGE);
    brain_view_t v;
    int rc = brain_head(head, sizeof(head), BRAIN_ROWS, &v);
    if (rc) return fail(path, rc);
    uint32_t slot = slot_of(v.row);
    if (slot < g_count && brain_same(&live(slot)->view, &v)) {
        mhfu_log("[brain] row %u id %08X unchanged", (unsigned)v.row, (unsigned)v.id);
        return (int)v.row;
    }
    if (slot == MHFU_BRAIN_MAX) return fail(path, MHFU_BRAIN_E_FULL);

    uint32_t size = 0;
    int route = 0;
    const uint32_t file = stage(path, &size, &route);   /* a failed install keeps it */
    if (!file) return fail(path, MHFU_BRAIN_E_STAGE);
    rc = brain_check((const uint8_t *)(uintptr_t)file, size, BRAIN_ROWS, &v);
    if (rc) return fail(path, rc);

    slot = slot_of(v.row);   /* the file may have changed since the head was read */
    if (slot == MHFU_BRAIN_MAX) return fail(path, MHFU_BRAIN_E_FULL);
    const uint32_t load = file + v.image;
    rc = brain_relocate((uint8_t *)(uintptr_t)file, &v, load);
    if (rc) return fail(path, rc);
    if (!s_listening) {
        if (mhfu_ai_on_chunk(on_chunk) != 0) return fail(path, MHFU_BRAIN_E_HOOK);
        s_listening = 1;
    }

    const int fresh = slot == g_count;
    brain_rec_t old = {};
    if (!fresh) old = *live(slot);
    const uint32_t next = fresh ? 0 : 1 - g_cur[slot];
    brain_rec_t *r = &g_rec[slot][next];
    r->file = file;
    r->load = load;
    r->view = v;
    r->patched = 0;
    __asm__ __volatile__("" ::: "memory");   /* the record, then the flip */
    g_cur[slot] = next;
    __asm__ __volatile__("" ::: "memory");
    if (fresh) g_count = slot + 1;

    /* the row now: back to native what the replaced install pointed at, then to this image */
    if (!fresh)
        brain_unpatch_row((const uint8_t *)(uintptr_t)old.file, &old.view, old.load,
                          (uint8_t *)(uintptr_t)row_addr(v.row));
    mhfu_log("[brain] row %u id %08X image 0x%08X %u bytes, staged %s, partition kept %u of %u",
             (unsigned)v.row, (unsigned)v.id, (unsigned)load, (unsigned)v.size,
             brain_stage_name(route), (unsigned)g_taken, (unsigned)BRAIN_STAGE_PART_MAX);
    apply(r, row_addr(v.row), 4 * BRAIN_WORDS);
    return (int)v.row;
}

extern "C" const char *mhfu_brain_error(int rc) { return brain_error_text(rc); }

extern "C" int mhfu_brain_status(int row, mhfu_brain_status_t *out)
{
    if (!out || row < 0) return 0;
    const uint32_t n = g_count;
    for (uint32_t i = 0; i < n; i++) {
        const brain_rec_t *r = live(i);
        if (r->view.row != (uint32_t)row) continue;
        out->id = r->view.id;
        out->image = r->load;
        out->size = r->view.size;
        out->headers = r->view.npatches;
        out->patched = r->patched;
        out->words = brain_row_words((const uint8_t *)(uintptr_t)row_addr((uint32_t)row), r->load,
                                     r->view.size);
        return 1;
    }
    return 0;
}
