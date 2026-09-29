/*
 * Quest monster list (mhfu/quest.h): read and retag the active quest's big monsters, and the
 * buildTargets wrapper that fires MHFU_EVENT_QUEST_TARGETS_BUILDING and finalises an ADD.
 */
#include "mhfu/quest.h"
#include "mhfu/hooks.h"
#include "mhfu/memory.h"
#include "mhfu/mips.h"
#include "mhfu/log.h"
#include "addresses.gen.h"
#include "internal.h"

/* QUEST.RECORDS points at the parsed quest data (rec_base); its list A names the quest's big
 * monsters. Fabricated records and headers go in its free tail (data ends near +0x1B84). */

/* Script-resource forge for an ADDed monster, run from the streaming poll every load barrier
 * spins on, so the barrier also waits for our resources. */
#define MAX_SCRIPT_RES      2u
#define MAX_MOUNTS          6u

/* The streaming poll's first two words load its context from MHFU_STREAM_CTX_PTR. */
#define POLL_WORD0  mips_lui(MIPS_REG_V0, (uint16_t)((MHFU_STREAM_CTX_PTR + 0x8000u) >> 16))
#define POLL_WORD1  mips_lw(MIPS_REG_A0, (int16_t)(MHFU_STREAM_CTX_PTR & 0xFFFFu), MIPS_REG_V0)

/* The species-specific QUEST_RECORD fields; only Tigrex is known. */
typedef struct {
    mhfu_monster_type_t id;
    uint8_t  model_idx;
    uint32_t hp;
    uint16_t rec_id;
    uint16_t flags;
    /* The archives the native overlay-init mounts and the script resources it registers. */
    struct { uint8_t type; uint16_t id; } mounts[MAX_MOUNTS];
    uint8_t  mount_n;
    uint16_t script_res[MAX_SCRIPT_RES];
    uint8_t  script_slot[MAX_SCRIPT_RES];
} species_record_t;

static const species_record_t SPECIES[] = {
    { MHFU_MONSTER_TIGREX, 0x04, 0x6D, 0xF692, 0x0960,
      { {0xD,0x17CE},{0xE,0x17CC},{0xF,0x17CD},{0x7,0x51},{0x8,0x1517},{0x0,0x171B} }, 6,
      { 0x17DD, 0x1611 }, { 3, 4 } },
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

/* Pending ADDs of the current build, each its own target group and scratch record. */
#define MAX_PENDING_ADD  3
#define SCRATCH_SLOT_STRIDE 0x50u
static volatile uint32_t g_add_quest   = 0;
static volatile int      g_add_n       = 0;
static volatile uint32_t g_add_rec_abs[MAX_PENDING_ADD] = { 0 };  /* each fabricated record */
static volatile uint32_t g_add_id[MAX_PENDING_ADD]      = { 0 };

/* Forge state; g_script_n > 0 arms it. Nothing arms it now: an added monster's script comes
 * from the overlay relocation instead. */
static volatile int      g_script_n        = 0;
static volatile uint16_t g_script_res[MAX_SCRIPT_RES]  = { 0, 0 };
static volatile uint8_t  g_script_slot[MAX_SCRIPT_RES] = { 0, 0 };
static volatile int      g_mount_n          = 0;
static volatile uint8_t  g_mount_type[MAX_MOUNTS] = { 0 };
static volatile uint16_t g_mount_id[MAX_MOUNTS]   = { 0 };
static volatile int      g_forge_phase      = 0;   /* 0=mount 1=wait+register 2=done */

typedef int (*reg_script_fn)(uint32_t ctx, uint32_t res_id, uint32_t slot);
typedef int (*mount_res_fn)(uint32_t ctx, uint32_t type, uint32_t id, uint32_t flags);
typedef int (*mount_poll_fn)(uint32_t ctx, uint32_t type);

/* Each poll tick while loading: mount the archives, wait until they load, then register the
 * script resources, as the native overlay-init does for the primary monster. */
static void bigmon_resource_poll_prefix(uint32_t a0)
{
    (void)a0;
    if (g_script_n <= 0) return;

    static volatile int reentry = 0;
    if (reentry) return;
    reentry = 1;

    if (mhfu_mem_read_u32(MHFU_EM75_SCRIPT_STRUCT)) {        /* struct filled: done */
        if (g_forge_phase != 9) { mhfu_log("[quest] forge DONE: struct0=0x%08X",
                                  (unsigned)mhfu_mem_read_u32(MHFU_EM75_SCRIPT_STRUCT)); g_forge_phase = 9; }
        g_script_n = 0; reentry = 0; return;
    }

    uint32_t mctx = mhfu_mem_read_u32(MHFU_RESOURCE_TABLE);
    if (!mhfu_mem_valid(mctx)) { reentry = 0; return; }
    mount_res_fn  mount = (mount_res_fn)MHFU_RESOURCE_REQUEST;
    mount_poll_fn mpoll = (mount_poll_fn)MHFU_RESOURCE_LOADED;
    reg_script_fn reg   = (reg_script_fn)MHFU_SCRIPT_RESOURCE_REGISTER;

    if (g_forge_phase == 0) {                        /* mount all archives once */
        for (int i = 0; i < g_mount_n && i < (int)MAX_MOUNTS; i++)
            mount(mctx, g_mount_type[i], g_mount_id[i], 0);
        mhfu_log("[quest] forge: mounted %d archives (ctx=0x%08X)", g_mount_n, (unsigned)mctx);
        g_forge_phase = 1; reentry = 0; return;
    }
    if (g_forge_phase == 1) {                         /* wait all loaded, then register */
        int all = 1;
        for (int i = 0; i < g_mount_n && i < (int)MAX_MOUNTS; i++)
            if (mpoll(mctx, g_mount_type[i]) == 0) { all = 0; break; }
        if (!all) { reentry = 0; return; }
        uint32_t sctx = mhfu_mem_read_u32(MHFU_SCRIPT_RESOURCE_CTX);
        int r0 = -99, r1 = -99;
        if (g_script_res[0]) r0 = reg(sctx, g_script_res[0], g_script_slot[0]);
        if (g_script_n > 1 && g_script_res[1]) r1 = reg(sctx, g_script_res[1], g_script_slot[1]);
        mhfu_log("[quest] forge: archives loaded; reg(0x%04X)=%d reg(0x%04X)=%d sctx=0x%08X",
                 g_script_res[0], r0, g_script_res[1], r1, (unsigned)sctx);
        g_forge_phase = 2;
    }
    reentry = 0;
}

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
        mhfu_log("[quest] add: pending-group cap %d reached", MAX_PENDING_ADD);
        return MHFU_HOOK_BADARG;
    }

    /* the quest's first big monster is the template */
    uint32_t src = 0;
    for (uint32_t node = la; mhfu_mem_read_u32(node + MHFU_QUEST_LIST_NODE_HEADER) != 0; node += MHFU_QUEST_LIST_NODE_SIZE) {
        uint32_t roff = mhfu_mem_read_u32(node + MHFU_QUEST_LIST_NODE_RECORD);
        if (roff) { src = recb + roff; break; }
    }
    if (!src) return MHFU_HOOK_BADARG;

    uint32_t rec = recb + MHFU_QUEST_DATA_SCRATCH_RECORD + (uint32_t)slot * SCRATCH_SLOT_STRIDE;
    uint32_t hdr = recb + MHFU_QUEST_DATA_SCRATCH_HEADER + (uint32_t)slot * SCRATCH_SLOT_STRIDE;

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
    uint32_t rec_off = MHFU_QUEST_DATA_SCRATCH_RECORD + (uint32_t)slot * SCRATCH_SLOT_STRIDE;
    uint32_t hdr_off = MHFU_QUEST_DATA_SCRATCH_HEADER + (uint32_t)slot * SCRATCH_SLOT_STRIDE;
    uint32_t end = la;
    while (mhfu_mem_read_u32(end + MHFU_QUEST_LIST_NODE_HEADER) != 0) end += MHFU_QUEST_LIST_NODE_SIZE;
    mhfu_mem_write_u32(end + MHFU_QUEST_LIST_NODE_FLAG, 1);
    mhfu_mem_write_u32(end + MHFU_QUEST_LIST_NODE_RESERVED, 0);
    mhfu_mem_write_u32(end + MHFU_QUEST_LIST_NODE_HEADER, hdr_off);
    mhfu_mem_write_u32(end + MHFU_QUEST_LIST_NODE_RECORD, rec_off);
    mhfu_mem_write_u32(end + MHFU_QUEST_LIST_NODE_SIZE + MHFU_QUEST_LIST_NODE_HEADER, 0);   /* new END */

    g_add_quest        = q;
    g_add_rec_abs[slot] = rec;
    g_add_id[slot]      = (uint32_t)id;
    g_add_n            = slot + 1;

    /* forge stays disarmed */
    g_script_n   = 0;
    g_mount_n    = 0;
    g_forge_phase = 9;

    mhfu_log("[quest] add 0x%02x queued: record @0x%08X (forge DISARMED; script via relocation+bind)",
             (unsigned)id, (unsigned)rec);
    return MHFU_HOOK_OK;
}

/* --- framework-owned buildTargets wrapper (prefix + postfix) --- */

static void quest_targets_prefix(uint32_t quest)
{
    g_add_quest   = 0;             /* arm fresh each build */
    g_add_n       = 0;
    g_script_n    = 0;
    g_mount_n     = 0;
    g_forge_phase = 0;
    mhfu_quest_ctx_t ctx;
    ctx.event_id = MHFU_EVENT_QUEST_TARGETS_BUILDING;
    ctx.quest    = mhfu_mem_valid(quest) ? quest : 0;
    mhfu_event_fire(MHFU_EVENT_QUEST_TARGETS_BUILDING, &ctx);
}

/* buildTargets put every list-A monster in group 0. Keep group 0 at count 1, give each
 * pending ADD a group of its own and set the group count to 1 + n, as a native quest does;
 * each group gets its own engine manager, so each added monster can deal damage. */
static void quest_targets_postfix(uint32_t quest)
{
    if (!g_add_quest || g_add_quest != quest || !mhfu_mem_valid(quest)) return;
    if (g_add_n <= 0) { g_add_quest = 0; return; }
    uint32_t t0 = quest + MHFU_QUEST_TARGETS;

    mhfu_mem_write_u16(t0 + MHFU_QUEST_TARGET_COUNT, 1);
    for (int k = 0; k < g_add_n; k++) {
        uint32_t tk = t0 + (uint32_t)(k + 1) * MHFU_QUEST_TARGET_SIZE;
        mhfu_mem_write_u32(tk + MHFU_QUEST_TARGET_RECORD, g_add_rec_abs[k]);
        mhfu_mem_write_u32(tk + MHFU_QUEST_TARGET_EM_ID, g_add_id[k]);
        mhfu_mem_write_u8 (tk + MHFU_QUEST_TARGET_MEMBER_FLAGS, 1);
        mhfu_mem_write_u16(tk + MHFU_QUEST_TARGET_COUNT, 1);
    }
    mhfu_mem_write_u32(quest + MHFU_QUEST_GROUP_COUNT, (uint32_t)(1 + g_add_n));

    mhfu_log("[quest] add finalized: %d extra group(s), +0x67C=%d (groups 1..%d filled)",
             g_add_n, 1 + g_add_n, g_add_n);
    g_add_quest = 0;
}

extern "C" void mhfu_quest_init(void)
{
    if (mhfu_event_count(MHFU_EVENT_QUEST_TARGETS_BUILDING) == 0) return;

    /* Own stub (prefix, buildTargets, postfix): the generic call wrapper takes one helper.
     * The call site is EBOOT code, so it is queued for the JIT-cold title/menu window. */
    uint32_t *s = mhfu_cave_alloc(20);
    if (!s) { mhfu_log("[quest] cave exhausted; no buildTargets wrapper"); return; }
    uint32_t pre  = (uint32_t)(uintptr_t)&quest_targets_prefix;
    uint32_t post = (uint32_t)(uintptr_t)&quest_targets_postfix;
    int i = 0;
    s[i++] = mips_addiu(MIPS_REG_SP, MIPS_REG_SP, -0x20);
    s[i++] = mips_sw(MIPS_REG_RA, 0x18, MIPS_REG_SP);
    s[i++] = mips_sw(MIPS_REG_A0, 0x10, MIPS_REG_SP);
    s[i++] = mips_lw(MIPS_REG_A0, 0x10, MIPS_REG_SP);
    s[i++] = mips_jal(pre);            s[i++] = MIPS_NOP;
    s[i++] = mips_lw(MIPS_REG_A0, 0x10, MIPS_REG_SP);
    s[i++] = mips_jal(MHFU_BUILD_TARGETS);   s[i++] = MIPS_NOP;
    s[i++] = mips_lw(MIPS_REG_A0, 0x10, MIPS_REG_SP);
    s[i++] = mips_jal(post);           s[i++] = MIPS_NOP;
    s[i++] = mips_lw(MIPS_REG_RA, 0x18, MIPS_REG_SP);
    s[i++] = mips_addiu(MIPS_REG_SP, MIPS_REG_SP, 0x20);
    s[i++] = mips_jr(MIPS_REG_RA);
    s[i++] = MIPS_NOP;
    while (i < 20) s[i++] = MIPS_NOP;
    mhfu_hook_flush_caches();

    mhfu_hook_word_when_quiet(MHFU_BUILD_TARGETS_CALL, mips_jal(MHFU_BUILD_TARGETS),
                               mips_jal((uint32_t)(uintptr_t)s), "framework");
    mhfu_log("[quest] buildTargets prefix+postfix wrapper queued");

    /* Entry detour on the streaming poll, for any quest: one J over its first word (the
     * original second word runs in the delay slot); the stub calls the forge, then replays
     * the poll: POLL_WORD0/1; lw t9,0(a0); lw t9,0x38(t9); jr t9. */
    uint32_t *w = mhfu_cave_alloc(16);
    if (w) {
        uint32_t h = (uint32_t)(uintptr_t)&bigmon_resource_poll_prefix;
        int j = 0;
        w[j++] = mips_addiu(MIPS_REG_SP, MIPS_REG_SP, -0x20);
        w[j++] = mips_sw(MIPS_REG_RA, 0x18, MIPS_REG_SP);
        w[j++] = mips_jal(h);  w[j++] = MIPS_NOP;
        w[j++] = mips_lw(MIPS_REG_RA, 0x18, MIPS_REG_SP);
        w[j++] = mips_addiu(MIPS_REG_SP, MIPS_REG_SP, 0x20);
        w[j++] = POLL_WORD0;
        w[j++] = POLL_WORD1;
        w[j++] = mips_lw(MIPS_REG_T9, 0x0, MIPS_REG_A0);
        w[j++] = mips_lw(MIPS_REG_T9, 0x38, MIPS_REG_T9);
        w[j++] = mips_jr(MIPS_REG_T9);
        w[j++] = MIPS_NOP;
        while (j < 16) w[j++] = MIPS_NOP;
        mhfu_hook_flush_caches();
        mhfu_hook_rc_t pr = mhfu_hook_word_when_quiet(
            MHFU_STREAM_POLL, POLL_WORD0, mips_j((uint32_t)(uintptr_t)w), "framework");
        mhfu_log("[quest] poll-fn entry detour queued @ 0x%08X stub=0x%08X (rc=%d)",
                 MHFU_STREAM_POLL, (unsigned)(uintptr_t)w, (int)pr);
    } else {
        mhfu_log("[quest] cave exhausted; no poll-fn detour");
    }
}
