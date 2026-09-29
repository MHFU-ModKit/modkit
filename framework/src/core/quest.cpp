/*
 * Quest monster list (mhfu/quest.h): read and retag the active quest's big monsters, and the
 * buildTargets wrapper that fires MHFU_EVENT_QUEST_TARGETS_BUILDING and finalises an ADD.
 */
#include "mhfu/quest.h"
#include "mhfu/hooks.h"
#include "mhfu/memory.h"
#include "mhfu/log.h"
#include "addresses.gen.h"
#include "internal.h"

#define OWNER "mhfu_quest"

/* QUEST.RECORDS points at the parsed quest data (rec_base); its list A names the quest's big
 * monsters. Fabricated records and headers go in its free tail (data ends near +0x1B84). */

/* The species-specific QUEST_RECORD fields; only Tigrex is known. */
typedef struct {
    mhfu_monster_type_t id;
    uint8_t  model_idx;
    uint32_t hp;
    uint16_t rec_id;
    uint16_t flags;
} species_record_t;

static const species_record_t SPECIES[] = {
    { MHFU_MONSTER_TIGREX, 0x04, 0x6D, 0xF692, 0x0960 },
};

static const species_record_t *species_lookup(mhfu_monster_type_t id)
{
    for (unsigned i = 0; i < sizeof(SPECIES) / sizeof(SPECIES[0]); i++)
        if (SPECIES[i].id == id) return &SPECIES[i];
    return 0;
}

static uint32_t recbase(mhfu_quest_t q) { return mhfu_mem_read_u32(q + MHFU_QUEST_RECORDS); }

static uint32_t list_a(mhfu_quest_t q)
{
    if (!mhfu_mem_valid(q)) return 0;
    uint32_t recb = recbase(q);
    if (!mhfu_mem_valid(recb)) return 0;
    uint32_t la = recb + mhfu_mem_read_u32(recb + MHFU_QUEST_DATA_LIST_A);
    return mhfu_mem_valid(la) ? la : 0;
}

/* Pending ADDs of the current build: group 0 keeps the quest's own monsters and each ADD
 * takes one more group, so the target array bounds them. Each has a scratch record and
 * mon-header, back to back, in QUEST_DATA's tail. */
#define MAX_PENDING_ADD  ((int)MHFU_QUEST_TARGETS_COUNT - 1)
#define SCRATCH_SLOT_STRIDE (MHFU_QUEST_RECORD_SIZE + MHFU_QUEST_MONSTER_HEADER_SIZE)
static_assert(MHFU_QUEST_DATA_SCRATCH_HEADER - MHFU_QUEST_DATA_SCRATCH_RECORD
              == MHFU_QUEST_RECORD_SIZE, "a scratch slot is a record, then its header");

static volatile uint32_t g_add_quest = 0;
static volatile int      g_add_n     = 0;
static volatile uint32_t g_add_rec_abs[MAX_PENDING_ADD] = { 0 };  /* each fabricated record */
static volatile uint32_t g_add_id[MAX_PENDING_ADD]      = { 0 };

/* --- public API --- */

extern "C" int mhfu_quest_monster_count(mhfu_quest_t q)
{
    uint32_t la = list_a(q);
    if (!la) return 0;
    int n = 0;
    for (uint32_t node = la; mhfu_mem_read_u32(node + MHFU_QUEST_LIST_NODE_HEADER) != 0 && n < 8;
         node += MHFU_QUEST_LIST_NODE_SIZE) n++;
    return n;
}

extern "C" int mhfu_quest_has(mhfu_quest_t q, mhfu_monster_type_t id)
{
    uint32_t la = list_a(q), recb = recbase(q);
    if (!la || !mhfu_mem_valid(recb)) return 0;
    for (uint32_t node = la; mhfu_mem_read_u32(node + MHFU_QUEST_LIST_NODE_HEADER) != 0; node += MHFU_QUEST_LIST_NODE_SIZE) {
        uint32_t roff = mhfu_mem_read_u32(node + MHFU_QUEST_LIST_NODE_RECORD);
        if (roff && (mhfu_mem_read_u16(recb + roff) & 0xFF) == (uint16_t)id) return 1;
    }
    return 0;
}

extern "C" int mhfu_quest_first_monster(mhfu_quest_t q)
{
    uint32_t la = list_a(q), recb = recbase(q);
    if (!la || !mhfu_mem_valid(recb)) return -1;
    uint32_t roff = mhfu_mem_read_u32(la + MHFU_QUEST_LIST_NODE_RECORD);
    if (!roff) return -1;
    return (int)(mhfu_mem_read_u16(recb + roff) & 0xFF);
}

extern "C" mhfu_hook_rc_t mhfu_quest_replace_monster(mhfu_quest_t q,
                                                     mhfu_monster_type_t from,
                                                     mhfu_monster_type_t to)
{
    const species_record_t *sp = species_lookup(to);
    if (!sp) { mhfu_log("[quest] replace: species 0x%02x record layout unknown",
                        (unsigned)to); return MHFU_HOOK_BADARG; }
    uint32_t la = list_a(q), recb = recbase(q);
    if (!la || !mhfu_mem_valid(recb)) return MHFU_HOOK_BADARG;

    for (uint32_t node = la; mhfu_mem_read_u32(node + MHFU_QUEST_LIST_NODE_HEADER) != 0; node += MHFU_QUEST_LIST_NODE_SIZE) {
        uint32_t hoff = mhfu_mem_read_u32(node + MHFU_QUEST_LIST_NODE_HEADER);
        uint32_t roff = mhfu_mem_read_u32(node + MHFU_QUEST_LIST_NODE_RECORD);
        if (!roff) continue;
        uint32_t rec = recb + roff;
        if ((mhfu_mem_read_u16(rec) & 0xFF) != (uint16_t)from) continue;
        mhfu_mem_write_u16(rec + MHFU_QUEST_RECORD_EM_ID, (uint16_t)to);
        mhfu_mem_write_u8 (rec + MHFU_QUEST_RECORD_MODEL, sp->model_idx);
        mhfu_mem_write_u32(rec + MHFU_QUEST_RECORD_HP_PERCENT, sp->hp);
        mhfu_mem_write_u16(rec + MHFU_QUEST_RECORD_ID, sp->rec_id);
        mhfu_mem_write_u16(rec + MHFU_QUEST_RECORD_FLAGS, sp->flags);
        mhfu_mem_write_u16(recb + hoff + MHFU_QUEST_MONSTER_HEADER_EM_ID, (uint16_t)to);
        mhfu_log("[quest] replaced 0x%02x -> 0x%02x", (unsigned)from, (unsigned)to);
        return MHFU_HOOK_OK;
    }
    return MHFU_HOOK_BADARG;
}

/* Called from a MHFU_EVENT_QUEST_TARGETS_BUILDING subscriber: fabricates a record and
 * mon-header in rec_base's tail and appends a list-A node, so the loading screen loads the
 * model natively; the postfix then gives it its own target group. */
extern "C" mhfu_hook_rc_t mhfu_quest_add_monster(mhfu_quest_t q, mhfu_monster_type_t id,
                                                 float x, float z)
{
    const species_record_t *sp = species_lookup(id);
    if (!sp) { mhfu_log("[quest] add: species 0x%02x layout unknown", (unsigned)id);
               return MHFU_HOOK_BADARG; }
    uint32_t la = list_a(q), recb = recbase(q);
    if (!la || !mhfu_mem_valid(recb)) return MHFU_HOOK_BADARG;

    /* The prefix clears g_add_quest each build; bind the pending list to this quest. */
    if (g_add_quest != q) { g_add_quest = q; g_add_n = 0; }
    int slot = g_add_n;
    if (slot >= MAX_PENDING_ADD) {
        mhfu_log("[quest] add: the quest holds %d target groups; no room for another",
                 (int)MHFU_QUEST_TARGETS_COUNT);
        return MHFU_HOOK_NOSPACE;
    }

    /* the quest's first big monster is the template */
    uint32_t src = 0;
    for (uint32_t node = la; mhfu_mem_read_u32(node + MHFU_QUEST_LIST_NODE_HEADER) != 0; node += MHFU_QUEST_LIST_NODE_SIZE) {
        uint32_t roff = mhfu_mem_read_u32(node + MHFU_QUEST_LIST_NODE_RECORD);
        if (roff) { src = recb + roff; break; }
    }
    if (!src) return MHFU_HOOK_BADARG;

    uint32_t rec_off = MHFU_QUEST_DATA_SCRATCH_RECORD + (uint32_t)slot * SCRATCH_SLOT_STRIDE;
    uint32_t hdr_off = MHFU_QUEST_DATA_SCRATCH_HEADER + (uint32_t)slot * SCRATCH_SLOT_STRIDE;
    uint32_t rec = recb + rec_off;
    uint32_t hdr = recb + hdr_off;

    /* clone the record, then retag the species fields */
    for (uint32_t o = 0; o < MHFU_QUEST_RECORD_SIZE; o += 4)
        mhfu_mem_write_u32(rec + o, mhfu_mem_read_u32(src + o));
    mhfu_mem_write_u16(rec + MHFU_QUEST_RECORD_EM_ID, (uint16_t)id);
    mhfu_mem_write_u8 (rec + MHFU_QUEST_RECORD_MODEL, sp->model_idx);
    mhfu_mem_write_u32(rec + MHFU_QUEST_RECORD_HP_PERCENT, sp->hp);
    mhfu_mem_write_u16(rec + MHFU_QUEST_RECORD_ID, sp->rec_id);
    mhfu_mem_write_u16(rec + MHFU_QUEST_RECORD_FLAGS, sp->flags);
    if (x != 0.0f) mhfu_mem_write_f32(rec + MHFU_QUEST_RECORD_SPAWN_X, x);
    if (z != 0.0f) mhfu_mem_write_f32(rec + MHFU_QUEST_RECORD_SPAWN_Z, z);

    /* mon-header { u16 emId; u16 0; u8 0xFF x12 } */
    mhfu_mem_write_u32(hdr + MHFU_QUEST_MONSTER_HEADER_EM_ID, (uint32_t)id);
    mhfu_mem_write_u32(hdr + MHFU_QUEST_MONSTER_HEADER_FILL, 0xFFFFFFFFu);
    mhfu_mem_write_u32(hdr + MHFU_QUEST_MONSTER_HEADER_FILL + 4, 0xFFFFFFFFu);
    mhfu_mem_write_u32(hdr + MHFU_QUEST_MONSTER_HEADER_FILL + 8, 0xFFFFFFFFu);

    /* append a node over the end marker, then re-terminate */
    uint32_t end = la;
    while (mhfu_mem_read_u32(end + MHFU_QUEST_LIST_NODE_HEADER) != 0) end += MHFU_QUEST_LIST_NODE_SIZE;
    mhfu_mem_write_u32(end + MHFU_QUEST_LIST_NODE_FLAG, 1);
    mhfu_mem_write_u32(end + MHFU_QUEST_LIST_NODE_RESERVED, 0);
    mhfu_mem_write_u32(end + MHFU_QUEST_LIST_NODE_HEADER, hdr_off);
    mhfu_mem_write_u32(end + MHFU_QUEST_LIST_NODE_RECORD, rec_off);
    mhfu_mem_write_u32(end + MHFU_QUEST_LIST_NODE_SIZE + MHFU_QUEST_LIST_NODE_HEADER, 0);   /* new END */

    g_add_rec_abs[slot] = rec;
    g_add_id[slot]      = (uint32_t)id;
    g_add_n             = slot + 1;

    mhfu_log("[quest] add 0x%02x queued: record @0x%08X", (unsigned)id, (unsigned)rec);
    return MHFU_HOOK_OK;
}

/* --- the buildTargets wrapper; buildTargets(quest a0, 2) --- */

static void targets_pre(mhfu_regs_t *regs)
{
    g_add_quest = 0;             /* arm fresh each build */
    g_add_n     = 0;
    mhfu_quest_ctx_t ctx;
    ctx.event_id = MHFU_EVENT_QUEST_TARGETS_BUILDING;
    ctx.quest    = mhfu_mem_valid(regs->a0) ? regs->a0 : 0;
    mhfu_event_fire(MHFU_EVENT_QUEST_TARGETS_BUILDING, &ctx);
}

/* buildTargets put every list-A monster in group 0. Keep group 0 at count 1, give each
 * pending ADD a group of its own and set the group count to 1 + n, as a native quest does;
 * each group gets its own engine manager, so each added monster can deal damage. */
static void targets_post(mhfu_regs_t *regs)
{
    uint32_t quest = regs->a0;
    if (!g_add_quest || g_add_quest != quest || !mhfu_mem_valid(quest)) return;
    int n = g_add_n;
    if (n <= 0) { g_add_quest = 0; return; }
    if (n > MAX_PENDING_ADD) n = MAX_PENDING_ADD;
    uint32_t t0 = quest + MHFU_QUEST_TARGETS;

    mhfu_mem_write_u16(t0 + MHFU_QUEST_TARGET_COUNT, 1);
    for (int k = 0; k < n; k++) {
        uint32_t tk = t0 + (uint32_t)(k + 1) * MHFU_QUEST_TARGET_SIZE;
        mhfu_mem_write_u32(tk + MHFU_QUEST_TARGET_RECORD, g_add_rec_abs[k]);
        mhfu_mem_write_u32(tk + MHFU_QUEST_TARGET_EM_ID, g_add_id[k]);
        mhfu_mem_write_u8 (tk + MHFU_QUEST_TARGET_MEMBER_FLAGS, 1);
        mhfu_mem_write_u16(tk + MHFU_QUEST_TARGET_COUNT, 1);
    }
    mhfu_mem_write_u32(quest + MHFU_QUEST_GROUP_COUNT, (uint32_t)(1 + n));

    mhfu_log("[quest] add finalized: %d extra group(s), %d in all", n, 1 + n);
    g_add_quest = 0;
}

/* The registry calls this once, for the first QUEST_TARGETS_BUILDING subscriber. The call site
 * is EBOOT code, so the wrapper is queued for the JIT-cold title/menu window. */
extern "C" int mhfu_quest_install_targets(void)
{
    mhfu_hook_rc_t rc = mhfu_hook_call(MHFU_BUILD_TARGETS_CALL, MHFU_BUILD_TARGETS,
                                       targets_pre, targets_post, OWNER);
    mhfu_log("[quest] buildTargets wrapper %s (rc=%d)",
             rc == MHFU_HOOK_OK ? "queued" : "FAILED", (int)rc);
    return rc;
}
