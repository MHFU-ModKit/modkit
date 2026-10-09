/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* Village NPCs of our own (mhfu/npc.h).
 *
 *   npc_add      stages the PAC in extra RAM and adds a row to the block
 *   chunk        the DATA.BIN chunk that decrypts the lobby's spawn table: the village's rows
 *                plus ours into the block, NPC_SPAWN_ROWS[village] at it
 *   brain pre    the setup call of ours: RESOURCE_TABLE row 16 + kind = its PAC; later: the turn
 *   brain post   the setup call: head and tail slots, its first clips, placed by the hunter;
 *                every call: not talkable, clip switches, the status
 *
 * KIND is 0 at the setup call (NPC_PRE_SETUP copies it in), so ours are told apart there by the
 * row NPC_PRE_SETUP is about to copy, and by INDEX and KIND after. The block lives in partition
 * memory, like the move player's; an NPC is kept past its frame only as SLOT.OBJECT, which every
 * reader checks. */
#include "mhfu/npc.h"
#include "mhfu/call.h"
#include "mhfu/hooks.h"
#include "mhfu/log.h"
#include "mhfu/memory.h"
#include "addresses.gen.h"
#include "internal.h"
#include "npc_logic.h"
#include "xram.h"

#include <pspsysmem.h>
#include <pspthreadman.h>

#define OWNER "mhfu_npc"
#define MAGIC 0x4E504353u        /* 'NPCS' */
#define RESOURCE_ROW0 16         /* NPC_MODEL_SETUP's RESOURCE_TABLE row of kind 0 */
#define RESOURCE_LOADED 2        /* RESOURCE_SLOT.FLAGS */
#define RESOURCE_OURS 0xFFFE     /* RESOURCE_SLOT.FILE_ID: any but free */
#define FRESH_US 250000u         /* the wrapper's last call: some frames at 30 Hz */

typedef struct {
    uint32_t pac;               /* fixed once added */
    float    size, right, ahead;
    uint8_t  character;
    uint8_t  _pad;
    uint16_t roots[NPC_PARTS];  /* each part's first joint */
    npc_play_t play;            /* any thread */
    volatile npc_face_t face;
    npc_anim_t anim;            /* the game thread */
    volatile uint32_t object, seen_us;
    mhfu_npc_status_t status;
} npc_slot_t;

typedef struct {
    uint32_t magic;
    volatile uint32_t count;
    uint8_t  rows[(NPC_SHIPPED + MHFU_NPC_MAX) * NPC_ROW_BYTES];
    npc_slot_t slot[MHFU_NPC_MAX];
} npc_block_t;

static npc_block_t *B;

/* Between a setup call's pre and post, on the game thread. */
static struct {
    uint32_t npc;
    int      slot;
    uint8_t  saved[MHFU_RESOURCE_SLOT_SIZE];
} g_setup;

static inline uint8_t  rd8(uint32_t a)  { return *(volatile uint8_t *)a; }
static inline uint16_t rd16(uint32_t a) { return *(volatile uint16_t *)a; }
static inline uint32_t rd32(uint32_t a) { return *(volatile uint32_t *)a; }
static inline float    rdf(uint32_t a)  { return *(volatile float *)a; }
static inline void wr8(uint32_t a, uint8_t v)   { *(volatile uint8_t *)a = v; }
static inline void wr16(uint32_t a, uint16_t v) { *(volatile uint16_t *)a = v; }
static inline void wr32(uint32_t a, uint32_t v) { *(volatile uint32_t *)a = v; }
static inline void wrf(uint32_t a, float v)     { *(volatile float *)a = v; }

static uint32_t now_us(void) { return sceKernelGetSystemTimeLow(); }

/* --- the spawn table ---------------------------------------------------------------------- */

/* Points the village at the block when its table reads as shipped, or refreshes our rows when it
 * already does; nothing otherwise. */
static void patch_table(void)
{
    const uint32_t n = B->count;
    const uint32_t at = MHFU_NPC_SPAWN_ROWS + 4 * NPC_VILLAGE;
    const uint32_t rows = rd32(at);
    const uint8_t count = rd8(MHFU_NPC_SPAWN_COUNTS + NPC_VILLAGE);
    if (!n) return;
    if (rows != (uint32_t)(uintptr_t)B->rows
        && !(rows == MHFU_NPC_SPAWN_ROWS_VILLAGE && count == NPC_SHIPPED))
        return;
    uint8_t chars[MHFU_NPC_MAX];
    float sizes[MHFU_NPC_MAX];
    for (uint32_t k = 0; k < n; k++) {
        chars[k] = B->slot[k].character;
        sizes[k] = B->slot[k].size;
    }
    const uint8_t *shipped = rows == MHFU_NPC_SPAWN_ROWS_VILLAGE
                                 ? (const uint8_t *)MHFU_NPC_SPAWN_ROWS_VILLAGE
                                 : B->rows;
    int total = npc_rows(B->rows, shipped, chars, sizes, (int)n);
    wr32(at, (uint32_t)(uintptr_t)B->rows);
    wr8(MHFU_NPC_SPAWN_COUNTS + NPC_VILLAGE, (uint8_t)total);
}

static void on_chunk(uint32_t buf, uint32_t bytes)
{
    if (npc_chunk_patches(buf, bytes, rd32(MHFU_NPC_SPAWN_ROWS + 4 * NPC_VILLAGE),
                          rd8(MHFU_NPC_SPAWN_COUNTS + NPC_VILLAGE)))
        patch_table();
}

/* --- the brain wrapper ---------------------------------------------------------------------- */

static uint32_t resource_row(void)
{
    return rd32(MHFU_RESOURCE_TABLE)
           + (RESOURCE_ROW0 + MHFU_NPC_OWN_KIND) * MHFU_RESOURCE_SLOT_SIZE;
}

/* Our slot of a running NPC, or -1. */
static int slot_of(uint32_t npc, uint16_t index)
{
    if (rd8(npc + MHFU_NPC_KIND) != MHFU_NPC_OWN_KIND) return -1;
    return npc_slot_of(index, B->count);
}

/* State 0, before NPC_PRE_SETUP: ours when the row it will copy (NPC.BEHAVIOUR holds the row
 * index, ENTITY.SECTION the stage) is in the block; its model row then gets our PAC. */
static void setup_pre(uint32_t npc)
{
    uint32_t area = mhfu_call(MHFU_STAGE_AREA_INDEX, rd32(MHFU_MAP_MANAGER_PTR), 0,
                              rd16(npc + MHFU_ENTITY_SECTION)) & 0xFFFF;
    if (area >= MHFU_NPC_SPAWN_ROWS_COUNT
        || rd32(MHFU_NPC_SPAWN_ROWS + 4 * area) != (uint32_t)(uintptr_t)B->rows)
        return;
    int k = npc_slot_of(rd8(npc + MHFU_NPC_BEHAVIOUR), B->count);
    if (k < 0) return;
    uint32_t row = resource_row();
    for (uint32_t i = 0; i < MHFU_RESOURCE_SLOT_SIZE; i++) g_setup.saved[i] = rd8(row + i);
    wr16(row + MHFU_RESOURCE_SLOT_FLAGS, RESOURCE_LOADED);
    wr16(row + MHFU_RESOURCE_SLOT_FILE_ID, RESOURCE_OURS);
    wr32(row + MHFU_RESOURCE_SLOT_DATA, B->slot[k].pac);
    g_setup.npc = npc;
    g_setup.slot = k;
}

/* YAW here, before the brain's transform reads it, so the turn shows this frame. */
static void turn(uint32_t npc, npc_slot_t *s)
{
    npc_face_t f;
    f.mode = s->face.mode;
    if (f.mode == MHFU_NPC_FACE_STILL) return;
    f.rate = s->face.rate;
    f.x = s->face.x;
    f.z = s->face.z;
    const uint32_t pos = npc + MHFU_ENTITY_POSITION;
    const uint32_t hunter = MHFU_PLAYER_ENTITY + MHFU_ENTITY_POSITION;
    wr16(npc + MHFU_ENTITY_YAW,
         npc_turn(rd16(npc + MHFU_ENTITY_YAW), &f, rdf(pos), rdf(pos + 8), rdf(hunter),
                  rdf(hunter + 8), rd16(MHFU_PLAYER_ENTITY + MHFU_ENTITY_YAW)));
}

static void brain_pre(mhfu_regs_t *r)
{
    const uint32_t npc = r->a0;
    const uint16_t index = rd16(npc + MHFU_NPC_INDEX);
    if (index < NPC_SHIPPED) return;   /* every NPC the game ships */
    if (rd8(npc + MHFU_NPC_STATE) == 0) {
        setup_pre(npc);
        return;
    }
    int k = slot_of(npc, index);
    if (k >= 0) turn(npc, &B->slot[k]);
}

/* entry on clip slots 0..SLOT_COUNT-1 (at most the three parts) over `blend` frames; a part
 * whose stream has no clip there keeps its own, or on a spawn takes the stream's first. */
static void play(uint32_t npc, uint16_t entry, uint8_t blend, int spawn)
{
    const uint32_t pack = rd32(npc + MHFU_ENTITY_ACTION_TABLE);
    uint16_t parts = rd16(npc + MHFU_ENTITY_SLOT_COUNT);
    if (!pack || entry == MHFU_NPC_NONE) return;
    if (parts > NPC_PARTS) parts = NPC_PARTS;
    for (uint32_t k = 0; k < parts; k++) {
        const uint8_t *p = (const uint8_t *)pack;
        uint32_t off = spawn ? npc_first_clip(p, 2 * k, entry) : npc_clip(p, 2 * k, entry);
        if (off)
            mhfu_call(MHFU_CLIP_SET, npc + MHFU_ENTITY_CLIP_BLOCKS, pack + off, 0, blend, k);
    }
}

/* S4 and the place: the brain has just built the model. */
static void setup_post(uint32_t npc, int k)
{
    npc_slot_t *s = &B->slot[k];
    const uint32_t joints = rd32(npc + MHFU_ENTITY_JOINTS);
    /* CLIP_SET reads part k's first joint from here; the builder leaves only the skeleton's
     * roots, so the tail's is 0 */
    for (int j = 1; j < NPC_PARTS; j++) {
        uint32_t at = npc + MHFU_ENTITY_SLOT_ROOTS + 4 * (j - 1);
        if (!rd32(at)) wr32(at, joints + s->roots[j] * MHFU_JOINT_SIZE);
    }
    if (rd16(npc + MHFU_ENTITY_SLOT_COUNT) > NPC_PARTS)
        wr16(npc + MHFU_ENTITY_SLOT_COUNT, NPC_PARTS);   /* the severed tail has no clip */
    for (int j = 0; j < MHFU_NPC_ANIM_LATCH_COUNT; j++) wr8(npc + MHFU_NPC_ANIM_LATCH + j, 1);
    play(npc, npc_anim_spawn(&s->anim, &s->play), 0, 1);

    const uint32_t hunter = MHFU_PLAYER_ENTITY + MHFU_ENTITY_POSITION;
    const uint16_t hyaw = rd16(MHFU_PLAYER_ENTITY + MHFU_ENTITY_YAW);
    float x, z;
    npc_hunter_point(rdf(hunter), rdf(hunter + 8), hyaw, s->right, s->ahead, &x, &z);
    wrf(npc + MHFU_ENTITY_POSITION, x);
    wrf(npc + MHFU_ENTITY_POSITION + 4, rdf(hunter + 4));
    wrf(npc + MHFU_ENTITY_POSITION + 8, z);
    wr16(npc + MHFU_ENTITY_YAW, hyaw);

    s->status.frames = 0;
    s->object = npc;
}

/* Every frame of ours: after the brain, which zeroes BUSY and plays the clips. */
static void frame(uint32_t npc, npc_slot_t *s)
{
    wr8(npc + MHFU_NPC_BUSY, 1);
    const uint32_t flags = npc + MHFU_ENTITY_CLIP_BLOCKS + MHFU_CLIP_BLOCK_FLAGS;
    uint16_t entry = npc_anim_step(&s->anim, &s->play, rd16(flags) & 1);
    if (entry != MHFU_NPC_NONE) play(npc, entry, s->anim.blend, 0);

    mhfu_npc_status_t *st = &s->status;
    const uint32_t pos = npc + MHFU_ENTITY_POSITION;
    const uint32_t hunter = MHFU_PLAYER_ENTITY + MHFU_ENTITY_POSITION;
    st->frames++;
    st->entry = s->anim.entry;
    st->playing = (uint8_t)(rd16(flags) & 1);
    st->yaw = rd16(npc + MHFU_ENTITY_YAW);
    st->pos.x = rdf(pos);
    st->pos.y = rdf(pos + 4);
    st->pos.z = rdf(pos + 8);
    const float dx = rdf(hunter) - st->pos.x, dz = rdf(hunter + 8) - st->pos.z;
    st->dist = sqrtf(dx * dx + dz * dz);
    s->object = npc;
    s->seen_us = now_us();
}

static void brain_post(mhfu_regs_t *r)
{
    const uint32_t npc = r->a0;
    const uint16_t index = rd16(npc + MHFU_NPC_INDEX);
    if (index < NPC_SHIPPED) return;
    if (g_setup.npc == npc) {
        uint32_t row = resource_row();
        for (uint32_t i = 0; i < MHFU_RESOURCE_SLOT_SIZE; i++) wr8(row + i, g_setup.saved[i]);
        g_setup.npc = 0;
        if (rd8(npc + MHFU_NPC_STATE) != 0 && slot_of(npc, index) == g_setup.slot)
            setup_post(npc, g_setup.slot);
    }
    int k = slot_of(npc, index);
    if (k >= 0) frame(npc, &B->slot[k]);
}

/* --- setup ------------------------------------------------------------------------------------ */

/* Low, as em_vhook's: High lands in the extra-RAM window. */
static int block_init(void)
{
    if (B) return 0;
    SceUID blk = sceKernelAllocPartitionMemory(2, OWNER, PSP_SMEM_Low, sizeof(npc_block_t) + 64, 0);
    if (blk < 0) {
        mhfu_log("[%s] partition alloc FAILED (%d)", OWNER, (int)blk);
        return -1;
    }
    uintptr_t at = ((uintptr_t)sceKernelGetBlockHeadAddr(blk) + 63) & ~(uintptr_t)63;
    if (at + sizeof(npc_block_t) > MHFU_USER_RAM_END) {
        mhfu_log("[%s] block at 0x%08X is in the extra-RAM window - refusing", OWNER,
                 (unsigned)at);
        sceKernelFreePartitionMemory(blk);
        return -1;
    }
    volatile uint8_t *b = (volatile uint8_t *)at;
    for (unsigned i = 0; i < sizeof(npc_block_t); i++) b[i] = 0;
    B = (npc_block_t *)at;
    B->magic = MAGIC;
    mhfu_log("[%s] block @0x%08X", OWNER, (unsigned)at);
    return 0;
}

/* The brain wrapper on NPC_VTABLE (a data write: any time) and the chunk listener, once. */
static int hooks_init(void)
{
    static int s_done;
    if (s_done) return 0;
    const uint32_t slot = MHFU_NPC_VTABLE + MHFU_OBJ_VTABLE_BRAIN;
    if (rd32(slot) != MHFU_NPC_BRAIN) {
        mhfu_log("[%s] NPC_VTABLE's brain is 0x%08X, not NPC_BRAIN - refusing", OWNER,
                 (unsigned)rd32(slot));
        return -1;
    }
    mhfu_wrap_t w = {};
    w.pre = brain_pre;
    w.call = MHFU_NPC_BRAIN;
    w.post = brain_post;
    w.pc = MHFU_NPC_BRAIN;
    uint32_t wrapper = mhfu_wrap_build(&w);
    if (!wrapper) {
        mhfu_log("[%s] no room for the brain wrapper", OWNER);
        return -1;
    }
    mhfu_hook_rc_t rc = mhfu_hook_vtable(slot, wrapper, OWNER);
    if (rc != MHFU_HOOK_OK) {
        mhfu_log("[%s] brain swap rc=%d", OWNER, (int)rc);
        return -1;
    }
    if (mhfu_ai_on_chunk(on_chunk) != 0) return -1;
    s_done = 1;
    return 0;
}

extern "C" int mhfu_npc_add(const mhfu_npc_spec_t *spec)
{
    if (!spec || !spec->pac || block_init() != 0) return -1;
    const uint32_t k = B->count;
    if (k >= MHFU_NPC_MAX) {
        mhfu_log("[%s] full (%d)", OWNER, MHFU_NPC_MAX);
        return -1;
    }
    uint32_t size = 0;
    const uint32_t pac = mhfu_xram_stage_file(spec->pac, &size);
    if (!pac) return -2;
    uint32_t skel_size = 0;
    const uint8_t *p = (const uint8_t *)pac;
    const uint32_t skel = npc_pac_sub(p, size, MHFU_SKELETON_MAGIC, &skel_size);
    npc_slot_t *s = &B->slot[k];
    if (!skel || npc_part_roots(p + skel, skel_size, s->roots) != 0) {
        mhfu_log("[%s] %s: no skeleton the lobby can build", OWNER, spec->pac);
        return -2;
    }
    s->pac = pac;
    s->size = spec->size;
    s->right = spec->right;
    s->ahead = spec->ahead;
    s->character = spec->character;
    s->face.mode = MHFU_NPC_FACE_STILL;
    s->face.rate = 0x200;
    s->anim.then = MHFU_NPC_NONE;
    s->play.then = MHFU_NPC_NONE;
    if (hooks_init() != 0) return -3;
    B->count = k + 1;
    patch_table();   /* a lobby already up gets it now */
    mhfu_log("[%s] slot %u: %s (%uB @0x%08X), parts at joints %u/%u/%u", OWNER, (unsigned)k,
             spec->pac, (unsigned)size, (unsigned)pac, s->roots[0], s->roots[1], s->roots[2]);
    return (int)k;
}

static npc_slot_t *used(int slot)
{
    return B && slot >= 0 && (uint32_t)slot < B->count ? &B->slot[slot] : 0;
}

extern "C" uint32_t mhfu_npc_object(int slot)
{
    npc_slot_t *s = used(slot);
    if (!s) return 0;
    const uint32_t p = s->object;
    if (!mhfu_mem_valid(p) || !mhfu_mem_valid(p + MHFU_NPC_SIZE - 1)) return 0;
    if (!npc_alive(rd32(p), rd8(p + MHFU_NPC_KIND), rd16(p + MHFU_NPC_INDEX), slot,
                   now_us() - s->seen_us, FRESH_US))
        return 0;
    return p;
}

extern "C" void mhfu_npc_play(int slot, uint16_t entry, uint8_t blend, uint16_t then)
{
    npc_slot_t *s = used(slot);
    if (!s) return;
    s->play.entry = entry;
    s->play.blend = blend;
    s->play.then = then;
    s->play.seq = s->play.seq + 1;
}

extern "C" void mhfu_npc_face(int slot, int mode, float x, float z, uint16_t rate)
{
    npc_slot_t *s = used(slot);
    if (!s || mode < MHFU_NPC_FACE_STILL || mode > MHFU_NPC_FACE_HUNTER) return;
    s->face.x = x;
    s->face.z = z;
    s->face.rate = rate;
    s->face.mode = (uint8_t)mode;
}

extern "C" int mhfu_npc_status(int slot, mhfu_npc_status_t *out)
{
    npc_slot_t *s = used(slot);
    if (!s || !out) return 0;
    *out = s->status;
    out->object = mhfu_npc_object(slot);
    return 1;
}
