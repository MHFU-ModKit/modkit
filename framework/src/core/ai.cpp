/*
 * Big-monster AI events (mhfu/ai.h): priority chains over the action picker, the slot loops,
 * the action executor and the AI tick, plus spawn, damage and death edges.
 * Each hook is installed on its first subscriber.
 */
#include "mhfu/ai.h"
#include "mhfu/mips.h"
#include "mhfu/log.h"
#include "mhfu/entity.h"
#include "mhfu/hooks.h"
#include "mhfu/events.h"
#include "addresses.gen.h"
#include "internal.h"

#define MAX_HANDLERS 8

/* Each species' vtable slot for the action picker (shared by all four). Listed so an
 * unmapped species is never hooked by accident. */
typedef struct {
    const char *name;
    uint32_t    vtable_ptr;
    uint32_t    vt8_slot_addr;
} species_vt_t;

static const species_vt_t g_species_vts[] = {
    { "popo",     MHFU_POPO_VTABLE,     MHFU_POPO_VTABLE     + MHFU_MONSTER_VTABLE_PICK_ACTION },
    { "anteka",   MHFU_ANTEKA_VTABLE,   MHFU_ANTEKA_VTABLE   + MHFU_MONSTER_VTABLE_PICK_ACTION },
    { "tigrex",   MHFU_TIGREX_VTABLE,   MHFU_TIGREX_VTABLE   + MHFU_MONSTER_VTABLE_PICK_ACTION },
    { "giadrome", MHFU_GIADROME_VTABLE, MHFU_GIADROME_VTABLE + MHFU_MONSTER_VTABLE_PICK_ACTION },
};
#define N_SPECIES_VTS (int)(sizeof(g_species_vts) / sizeof(g_species_vts[0]))

/* EBOOT slot loop in the AI tick: $s1 = slot, $s2 = entity, $s0 = entity + 2*slot. */
#define SLOT_LOOP_WORD0       mips_sll (MIPS_REG_V0, MIPS_REG_S1, 6)
#define SLOT_LOOP_WORD1       mips_addu(MIPS_REG_V0, MIPS_REG_S2, MIPS_REG_V0)

/* The AI tick's prologue. */
#define AI_TICK_WORD0         mips_addiu(MIPS_REG_SP, MIPS_REG_SP, -0x20)
#define AI_TICK_WORD1         mips_sw   (MIPS_REG_RA, 0x0C, MIPS_REG_SP)

#define OVERLAY_AI_SIZE       0x00100000u   /* the AI overlay at MHFU_AI_OVERLAY */

/* Overlay slot loop, the one big monsters run (entity+0x288 bit 17 skips the EBOOT loop):
 * $s3 = slot, $s5 = entity, $s0 = entity + 2*slot, $s1 = slot*0xC8, $s6 = entity + slot*0x40. */
#define OVL_SLOT_LOOP_WORD0   mips_lw   (MIPS_REG_V1, MHFU_ENTITY_ACTION_LIST, MIPS_REG_S5)
#define OVL_SLOT_LOOP_WORD1   mips_addiu(MIPS_REG_A0, MIPS_REG_ZERO, 2)

/* Action executor f(entity, a1 = action id, a2, a3): picks the clip for every body slot at
 * once, so rewriting a1 is coherent where a per-slot write desyncs the body. It moves the
 * animation only; the attack is the move act_set picks. */
#define EXEC_WORD0            mips_addiu(MIPS_REG_SP, MIPS_REG_SP, -0x40)   /* its prologue */
#define EXEC_WORD1            mips_sw   (MIPS_REG_RA, 0x2C, MIPS_REG_SP)

#define AI_OWNER_TAG "mhfu_ai"

/* --- chain storage --------------------------------------------------- */

typedef struct { mhfu_ai_overlay_loaded_cb_t cb; int priority; } overlay_entry_t;
typedef struct { mhfu_bigmonster_slot_picked_cb_t cb; int priority; } slot_entry_t;
typedef struct { mhfu_bigmonster_action_input_cb_t cb; int priority; } input_entry_t;
typedef struct { mhfu_bigmonster_action_decided_cb_t cb; int priority; } action_entry_t;
typedef struct { mhfu_bigmonster_ai_step_cb_t         cb; int priority; } step_entry_t;
typedef struct { mhfu_bigmonster_spawn_cb_t cb; int priority; } bmspawn_entry_t;
typedef struct { mhfu_bigmonster_death_cb_t cb; int priority; } bmdeath_entry_t;
typedef struct { mhfu_bigmonster_damaged_cb_t cb; int priority; } bmdamage_entry_t;
typedef struct { mhfu_bigmonster_action_cb_t cb; int priority; } actionsel_entry_t;

static overlay_entry_t g_overlay_chain[MAX_HANDLERS];
static int             g_overlay_n = 0;
static slot_entry_t    g_slot_chain[MAX_HANDLERS];
static int             g_slot_n = 0;
static input_entry_t   g_input_chain[MAX_HANDLERS];
static int             g_input_n = 0;
static action_entry_t  g_action_chain[MAX_HANDLERS];
static int             g_action_n = 0;
static step_entry_t    g_step_chain[MAX_HANDLERS];
static int             g_step_n = 0;
static bmspawn_entry_t g_bmspawn_chain[MAX_HANDLERS];
static int             g_bmspawn_n = 0;
static bmdeath_entry_t g_bmdeath_chain[MAX_HANDLERS];
static int             g_bmdeath_n = 0;
static bmdamage_entry_t g_bmdamage_chain[MAX_HANDLERS];
static int              g_bmdamage_n = 0;
static actionsel_entry_t g_actionsel_chain[MAX_HANDLERS];
static int               g_actionsel_n = 0;

/* Slot the loop wrapper is processing, for ctx.slot in the action chains; stale outside it. */
static volatile uint8_t g_current_slot = 0;

/* Per registry slot: last HP and whether it holds a big monster, for the death edge. */
static uint16_t        g_last_hp[MHFU_REGISTRY_SLOTS];
static uint8_t         g_was_big[MHFU_REGISTRY_SLOTS];

static int            g_action_hook_installed  = 0;
static int            g_step_hook_installed    = 0;
static int            g_slot_hook_installed    = 0;
static int            g_overlay_hook_installed = 0;

static int            g_overlay_slot_hook_installed = 0;
static uint32_t      *g_actionsel_wrapper = 0;
static int            g_actionsel_hook_installed = 0;

/* --- chain insert/remove --------------------------------------------- */

/* Keeps the chain sorted by descending priority; both return from the caller. */
#define CHAIN_INSERT(chain, n, max, cb_, pri_)                     \
    do {                                                           \
        if (n >= max) return MHFU_HOOK_NOSPACE;                    \
        int pos_ = n;                                              \
        while (pos_ > 0 && chain[pos_ - 1].priority < pri_) {      \
            chain[pos_] = chain[pos_ - 1]; pos_--;                 \
        }                                                          \
        chain[pos_].cb = cb_; chain[pos_].priority = pri_;         \
        n++;                                                       \
    } while (0)

#define CHAIN_REMOVE(chain, n, cb_)                                \
    do {                                                           \
        for (int i_ = 0; i_ < n; i_++) {                           \
            if (chain[i_].cb == cb_) {                             \
                for (int j_ = i_; j_ < n - 1; j_++)                \
                    chain[j_] = chain[j_ + 1];                     \
                n--;                                               \
                return MHFU_HOOK_OK;                               \
            }                                                      \
        }                                                          \
        return MHFU_HOOK_BADARG;                                   \
    } while (0)

/* --- big-monster predicate ------------------------------------------- */

extern "C" int mhfu_entity_is_bigmonster(uint32_t entity_ptr)
{
    /* A species allowlist; the quest's target list would be exact. */
    if (!entity_ptr) return 0;
    uint8_t t = mhfu_entity_monster_type(entity_ptr);
    if (t == MHFU_MONSTER_TIGREX || t == MHFU_MONSTER_GIADROME) return 1;
    return 0;
}

/* --- per-species (vt8_input -> ptr) cache ---------------------------
 * Tigrex's vt[8] returns a per-run pointer for a stable input. The cache replays the last
 * pointer seen for an input, so a mod can force an action without calling vt[8] (which has
 * VFPU and RNG side effects). 96 entries cover the 54 inputs Tigrex uses. */
#define ACT_CACHE_PER_SPECIES 96

typedef struct { uint16_t input; uint16_t _pad; uint32_t ptr; } act_cache_entry_t;

static act_cache_entry_t g_act_cache[4][ACT_CACHE_PER_SPECIES];
static int               g_act_cache_n[4];

static int species_cache_index(uint8_t type)
{
    switch (type) {
        case MHFU_MONSTER_POPO:     return 0;
        case MHFU_MONSTER_ANTEKA:   return 1;
        case MHFU_MONSTER_TIGREX:   return 2;
        case MHFU_MONSTER_GIADROME: return 3;
        default:           return -1;
    }
}

static void ai_cache_remember(uint8_t type, uint16_t input, uint32_t ptr)
{
    if (ptr == 0) return;          /* the picker chose nothing */
    int sp = species_cache_index(type);
    if (sp < 0) return;
    for (int i = 0; i < g_act_cache_n[sp]; i++) {
        if (g_act_cache[sp][i].input == input) {
            g_act_cache[sp][i].ptr = ptr;
            return;
        }
    }
    if (g_act_cache_n[sp] >= ACT_CACHE_PER_SPECIES) return;   /* full: drop */
    g_act_cache[sp][g_act_cache_n[sp]].input = input;
    g_act_cache[sp][g_act_cache_n[sp]].ptr   = ptr;
    g_act_cache_n[sp]++;
}

extern "C" uint32_t mhfu_ai_action_ptr_for(uint8_t monster_type, uint16_t input)
{
    int sp = species_cache_index(monster_type);
    if (sp < 0) return 0;
    for (int i = 0; i < g_act_cache_n[sp]; i++) {
        if (g_act_cache[sp][i].input == input) return g_act_cache[sp][i].ptr;
    }
    return 0;
}

/* Top of every slot-loop iteration: returns the slot to process and records it for ctx.slot. */
extern "C" uint32_t mhfu_ai_slot_picked_dispatch_c(
    uint32_t entity, uint32_t slot)
{
    uint32_t s = slot & 0xFFu;
    g_current_slot = (uint8_t)s;
    if (g_slot_n == 0) return s;
    if (!mhfu_entity_is_bigmonster(entity)) return s;

    mhfu_bigmonster_slot_picked_ctx_t ctx;
    ctx.entity_ptr    = entity;
    ctx.monster_type  = mhfu_entity_monster_type(entity);
    ctx.original_slot = (uint8_t)s;
    ctx.action_count  = *(volatile uint16_t *)(entity + MHFU_ENTITY_SLOT_COUNT);

    uint8_t v = (uint8_t)s;
    for (int i = 0; i < g_slot_n; i++) v = g_slot_chain[i].cb(&ctx, v);

    /* Clamp a mod's answer to the loop bound. */
    if (ctx.action_count == 0) return s;
    if (v >= (uint8_t)ctx.action_count) v = (uint8_t)(ctx.action_count - 1);

    g_current_slot = v;
    return (uint32_t)v;
}

/* Before vt[8]: returns the input to pass it (low 16 bits). */
extern "C" uint32_t mhfu_ai_action_input_dispatch_c(
    uint32_t entity, uint32_t vt8_input)
{
    uint32_t in = vt8_input & 0xFFFFu;
    if (g_input_n == 0) return in;
    if (!mhfu_entity_is_bigmonster(entity)) return in;

    mhfu_bigmonster_action_input_ctx_t ctx;
    ctx.entity_ptr   = entity;
    ctx.monster_type = mhfu_entity_monster_type(entity);
    ctx.slot         = g_slot_hook_installed ? g_current_slot : 0;
    ctx.vt8_input    = (uint16_t)in;

    uint16_t v = (uint16_t)in;
    for (int i = 0; i < g_input_n; i++) {
        v = g_input_chain[i].cb(&ctx, v);
        ctx.vt8_input = v;
    }
    return (uint32_t)v & 0xFFFFu;
}

/* After vt[8]: caches the pair, then threads the engine's result through the chain. */
extern "C" uint32_t mhfu_ai_action_dispatch_c(
    uint32_t entity, uint32_t vt8_input, uint32_t engine_action_id)
{
    if (!mhfu_entity_is_bigmonster(entity)) return engine_action_id;

    /* Cached with or without subscribers, so mhfu_ai_action_ptr_for works at once. */
    uint8_t type = mhfu_entity_monster_type(entity);
    ai_cache_remember(type, (uint16_t)(vt8_input & 0xFFFF), engine_action_id);

    if (g_action_n == 0) return engine_action_id;

    mhfu_bigmonster_action_decided_ctx_t ctx;
    ctx.entity_ptr   = entity;
    ctx.monster_type = type;
    ctx.slot         = g_slot_hook_installed ? g_current_slot : 0;
    ctx.vt8_input    = (uint16_t)(vt8_input & 0xFFFF);

    uint32_t v = engine_action_id;
    for (int i = 0; i < g_action_n; i++) v = g_action_chain[i].cb(&ctx, v);
    return v;
}

/* The vt[8] replacement, one basic block for the JIT: pre(entity, input) -> new input;
 * vt[8](entity, new input); post(entity, new input, result) -> v0. */
static int build_action_stub(uint32_t *stub,
                             uint32_t pre_dispatch_addr,
                             uint32_t post_dispatch_addr)
{
    int i = 0;
    stub[i++] = mips_addiu(MIPS_REG_SP, MIPS_REG_SP, -0x20);
    stub[i++] = mips_sw   (MIPS_REG_RA,  0x18, MIPS_REG_SP);
    stub[i++] = mips_sw   (MIPS_REG_A0,  0x04, MIPS_REG_SP);   /* save entity */
    stub[i++] = mips_sw   (MIPS_REG_A1,  0x08, MIPS_REG_SP);   /* save input  */
    stub[i++] = mips_jal(pre_dispatch_addr);
    stub[i++] = MIPS_NOP;
    stub[i++] = mips_sw   (MIPS_REG_V0,  0x08, MIPS_REG_SP);   /* keep new input */
    stub[i++] = mips_lw   (MIPS_REG_A0,  0x04, MIPS_REG_SP);
    stub[i++] = mips_move (MIPS_REG_A1,  MIPS_REG_V0);
    stub[i++] = mips_jal(MHFU_ACTION_PICKER);
    stub[i++] = MIPS_NOP;
    stub[i++] = mips_lw   (MIPS_REG_A0,  0x04, MIPS_REG_SP);   /* a0 = entity */
    stub[i++] = mips_lw   (MIPS_REG_A1,  0x08, MIPS_REG_SP);   /* a1 = new input */
    stub[i++] = mips_move (MIPS_REG_A2,  MIPS_REG_V0);          /* a2 = engine v0 */
    stub[i++] = mips_jal(post_dispatch_addr);
    stub[i++] = MIPS_NOP;
    stub[i++] = mips_lw   (MIPS_REG_RA,  0x18, MIPS_REG_SP);
    stub[i++] = mips_jr(MIPS_REG_RA);
    stub[i++] = mips_addiu(MIPS_REG_SP, MIPS_REG_SP, 0x20);    /* delay slot */
    return i;
}

/* A vtable swap is a data write, so the JIT cannot miss it. */
static int install_action_hook(void)
{
    if (g_action_hook_installed) return 0;

    uint32_t *stub = mhfu_cave_alloc(24);
    if (!stub) { mhfu_log("[ai] cave exhausted for action stub"); return -1; }
    int n = build_action_stub(
        stub,
        (uint32_t)(uintptr_t)&mhfu_ai_action_input_dispatch_c,
        (uint32_t)(uintptr_t)&mhfu_ai_action_dispatch_c);
    (void)n;
    mhfu_hook_flush_caches();

    int hooked = 0;
    for (int i = 0; i < N_SPECIES_VTS; i++) {
        if (mhfu_hook_vtable(g_species_vts[i].vt8_slot_addr,
                             (uint32_t)(uintptr_t)stub,
                             AI_OWNER_TAG) == MHFU_HOOK_OK) {
            hooked++;
        } else {
            mhfu_log("[ai] vt[8] swap rejected for %s @0x%08lx",
                     g_species_vts[i].name,
                     (unsigned long)g_species_vts[i].vt8_slot_addr);
        }
    }
    if (hooked == 0) return -1;
    g_action_hook_installed = 1;
    mhfu_log("[ai] action_decided installed on %d species vt[8] slots", hooked);
    return 0;
}

/* --- ai overlay loader hook ----------------------------------------- */

extern "C" int mhfu_ai_install_overlay_slot_hook_fwd(void);
extern "C" int mhfu_ai_install_overlay_action_hook_fwd(void);

/* Postfix on the overlay loader. Acts only while the executor holds its original prologue:
 * the overlay is fresh and JIT-cold (a section roam reloads it, wiping our patches), so the
 * overlay hooks are (re)patched here and the overlay-loaded chain runs. */
extern "C" void mhfu_ai_overlay_loaded_helper(uint32_t /*ctx_a0*/)
{
    uint32_t w = *(volatile uint32_t *)MHFU_ACTION_EXECUTOR;
    if (w != EXEC_WORD0) return;     /* not a fresh overlay */

    static volatile int s_fires = 0;
    s_fires++;

    /* A fresh overlay carries none of our patches. */
    g_overlay_slot_hook_installed = 0;
    g_actionsel_hook_installed    = 0;

    mhfu_ai_install_overlay_slot_hook_fwd();
    mhfu_ai_install_overlay_action_hook_fwd();

    mhfu_log("[ai] overlay-loaded fire#%d (probe=0x%08X) repatch actionsel=%d",
             s_fires, (unsigned)w, g_actionsel_hook_installed);

    if (g_overlay_n == 0) return;
    mhfu_ai_overlay_loaded_ctx_t ctx;
    ctx.dest = MHFU_AI_OVERLAY;
    ctx.src  = 0;
    ctx.size = OVERLAY_AI_SIZE;
    for (int i = 0; i < g_overlay_n; i++) g_overlay_chain[i].cb(&ctx);
}

/* Postfix on the loader's call into the per-segment loader: a few dozen calls per map load,
 * where memcpy also runs for every boot copy. Its $a0 is the loader context, so the helper
 * reads the overlay's arrival from the executor's prologue. */
static int install_overlay_hook(void)
{
    if (g_overlay_hook_installed) return 0;
    mhfu_hook_rc_t r = mhfu_hook_call(
        MHFU_OVERLAY_LOAD_CALL,
        MHFU_OVERLAY_SEGMENT_LOAD,
        (void (*)(uint32_t))&mhfu_ai_overlay_loaded_helper,
        MHFU_HOOK_WRAP_POSTFIX,
        AI_OWNER_TAG);
    if (r != MHFU_HOOK_OK) {
        mhfu_log("[ai] overlay loader wrap failed (r=%d)", (int)r);
        return -1;
    }
    g_overlay_hook_installed = 1;
    mhfu_log("[ai] overlay loader wrap queued @ 0x%08X (postfix on jal -> 0x%08X)",
             MHFU_OVERLAY_LOAD_CALL, MHFU_OVERLAY_SEGMENT_LOAD);
    return 0;
}

/* --- EBOOT slot-loop wrapper ------------------------------------------
 * `J wrapper; nop` over the loop top. The wrapper asks the dispatcher for the slot, sets
 * $s1 and $s0 from it, replays the displaced pair and resumes. The AI tick reloads $ra from
 * its own frame, so the inner jal may clobber it. */
static int build_slot_wrapper(uint32_t *w, uint32_t dispatch_addr)
{
    int i = 0;
    w[i++] = mips_addiu(MIPS_REG_SP, MIPS_REG_SP, -0x10);
    w[i++] = mips_sw   (MIPS_REG_RA,  0x0c, MIPS_REG_SP);
    w[i++] = mips_move (MIPS_REG_A0,  MIPS_REG_S2);              /* entity */
    w[i++] = mips_jal  (dispatch_addr);
    w[i++] = mips_move (MIPS_REG_A1,  MIPS_REG_S1);              /* delay: slot */
    w[i++] = mips_lw   (MIPS_REG_RA,  0x0c, MIPS_REG_SP);
    w[i++] = mips_addiu(MIPS_REG_SP, MIPS_REG_SP,  0x10);
    w[i++] = mips_andi (MIPS_REG_V0,  MIPS_REG_V0, 0xFF);
    w[i++] = mips_move (MIPS_REG_S1,  MIPS_REG_V0);              /* $s1 = new slot */
    w[i++] = mips_sll  (MIPS_REG_V0,  MIPS_REG_S1, 1);
    w[i++] = mips_addu (MIPS_REG_S0,  MIPS_REG_S2, MIPS_REG_V0); /* $s0 = entity + 2*slot */
    w[i++] = SLOT_LOOP_WORD0;
    w[i++] = SLOT_LOOP_WORD1;
    w[i++] = mips_j(MHFU_SLOT_LOOP_RESUME);
    w[i++] = MIPS_NOP;                                            /* delay slot */
    while (i < 20) w[i++] = MIPS_NOP;
    return i;
}

/* --- overlay slot-loop wrapper -----------------------------------------
 * Same dispatcher as the EBOOT wrapper. Patched from the overlay-loaded helper, while the
 * overlay is JIT-cold, so a direct write and a cache flush take. */
static int build_overlay_slot_wrapper(uint32_t *w, uint32_t dispatch_addr)
{
    int i = 0;
    w[i++] = mips_addiu(MIPS_REG_SP, MIPS_REG_SP, -0x10);
    w[i++] = mips_sw   (MIPS_REG_RA, 0x0c, MIPS_REG_SP);
    w[i++] = mips_move (MIPS_REG_A0, MIPS_REG_S5);
    w[i++] = mips_jal  (dispatch_addr);
    w[i++] = mips_move (MIPS_REG_A1, MIPS_REG_S3);                /* delay */
    w[i++] = mips_move (MIPS_REG_S3, MIPS_REG_V0);
    w[i++] = mips_lw   (MIPS_REG_RA, 0x0c, MIPS_REG_SP);
    w[i++] = mips_addiu(MIPS_REG_SP, MIPS_REG_SP,  0x10);
    w[i++] = mips_ori  (MIPS_REG_T0, MIPS_REG_ZERO, 0xC8);
    w[i++] = mips_r3   (0, MIPS_REG_S3, MIPS_REG_T0, 0x18u);        /* mult s3, t0 */
    w[i++] = mips_r3   (MIPS_REG_S1, 0, 0, 0x12u);                  /* mflo s1 = slot*0xC8 */
    w[i++] = mips_sll  (MIPS_REG_T0, MIPS_REG_S3, 1);
    w[i++] = mips_addu (MIPS_REG_S0, MIPS_REG_S5, MIPS_REG_T0);     /* s0 = entity + slot*2 */
    w[i++] = mips_sll  (MIPS_REG_T0, MIPS_REG_S3, 6);
    w[i++] = mips_addu (MIPS_REG_S6, MIPS_REG_S5, MIPS_REG_T0);     /* s6 = entity + slot*0x40 */
    w[i++] = OVL_SLOT_LOOP_WORD0;
    w[i++] = mips_j(MHFU_BIGMON_SLOT_LOOP_RESUME);
    w[i++] = OVL_SLOT_LOOP_WORD1;                                   /* delay slot */
    while (i < 20) w[i++] = MIPS_NOP;
    return i;
}

static int install_overlay_slot_hook(void)
{
    if (g_overlay_slot_hook_installed) return 0;
    if (g_slot_n == 0) return 0;             /* no subscribers: spare the cave */

    uint32_t *w = mhfu_cave_alloc(20);
    if (!w) { mhfu_log("[ai] cave exhausted for ovl slot wrapper"); return -1; }
    build_overlay_slot_wrapper(w,
        (uint32_t)(uintptr_t)&mhfu_ai_slot_picked_dispatch_c);
    mhfu_hook_flush_caches();

    uint32_t cur0 = *(volatile uint32_t *)MHFU_BIGMON_SLOT_LOOP;
    uint32_t cur1 = *(volatile uint32_t *)(MHFU_BIGMON_SLOT_LOOP + 4);
    if (cur0 != OVL_SLOT_LOOP_WORD0 || cur1 != OVL_SLOT_LOOP_WORD1) {
        mhfu_log("[ai] ovl slot loop unexpected: [0x%08X]=0x%08lx [0x%08X]=0x%08lx",
                 MHFU_BIGMON_SLOT_LOOP, (unsigned long)cur0,
                 MHFU_BIGMON_SLOT_LOOP + 4, (unsigned long)cur1);
        return -1;
    }
    *(volatile uint32_t *)MHFU_BIGMON_SLOT_LOOP       = mips_j((uint32_t)(uintptr_t)w);
    *(volatile uint32_t *)(MHFU_BIGMON_SLOT_LOOP + 4) = MIPS_NOP;
    mhfu_hook_flush_caches();

    g_overlay_slot_hook_installed = 1;
    mhfu_log("[ai] ovl slot loop wrapped @ 0x%08X (wrapper 0x%08X, resumes 0x%08X)",
             MHFU_BIGMON_SLOT_LOOP, (unsigned)(uintptr_t)w, MHFU_BIGMON_SLOT_LOOP_RESUME);
    return 0;
}

extern "C" int mhfu_ai_install_overlay_slot_hook_fwd(void) { return install_overlay_slot_hook(); }

/* --- action executor entry detour ---------------------------------------
 * The wrapper passes (entity, a1) to the dispatcher, puts its answer in $a1, restores the
 * other arguments, replays the displaced prologue and resumes. */
extern "C" uint32_t mhfu_ai_bigmonster_action_dispatch_c(uint32_t entity, uint32_t a1)
{
    if (g_actionsel_n == 0) return a1;
    if (!mhfu_entity_is_bigmonster(entity)) return a1;   /* executor is shared */
    mhfu_bigmonster_action_ctx_t ctx;
    ctx.entity_ptr   = entity;
    ctx.monster_type = mhfu_entity_monster_type(entity);
    ctx.action_id    = (uint16_t)a1;
    uint32_t v = a1;
    for (int i = 0; i < g_actionsel_n; i++) v = g_actionsel_chain[i].cb(&ctx, v);
    return v;
}

static int build_overlay_action_wrapper(uint32_t *w, uint32_t dispatch_addr)
{
    int i = 0;
    w[i++] = mips_addiu(MIPS_REG_SP, MIPS_REG_SP, -0x20);
    w[i++] = mips_sw   (MIPS_REG_RA, 0x18, MIPS_REG_SP);
    w[i++] = mips_sw   (MIPS_REG_A0, 0x10, MIPS_REG_SP);
    w[i++] = mips_sw   (MIPS_REG_A2, 0x08, MIPS_REG_SP);
    w[i++] = mips_sw   (MIPS_REG_A3, 0x0C, MIPS_REG_SP);
    w[i++] = mips_jal  (dispatch_addr);             /* a0=entity, a1=action_id */
    w[i++] = MIPS_NOP;                              /* delay slot */
    w[i++] = mips_move (MIPS_REG_A1, MIPS_REG_V0);  /* a1 = chosen action id */
    w[i++] = mips_lw   (MIPS_REG_A0, 0x10, MIPS_REG_SP);
    w[i++] = mips_lw   (MIPS_REG_A2, 0x08, MIPS_REG_SP);
    w[i++] = mips_lw   (MIPS_REG_A3, 0x0C, MIPS_REG_SP);
    w[i++] = mips_lw   (MIPS_REG_RA, 0x18, MIPS_REG_SP);
    w[i++] = mips_addiu(MIPS_REG_SP, MIPS_REG_SP, 0x20);
    w[i++] = EXEC_WORD0;
    w[i++] = EXEC_WORD1;
    w[i++] = mips_j    (MHFU_ACTION_EXECUTOR_RESUME);
    w[i++] = MIPS_NOP;                              /* J delay slot */
    while (i < 20) w[i++] = MIPS_NOP;
    return i;
}

static int install_overlay_action_hook(void)
{
    if (g_actionsel_hook_installed) return 0;
    if (g_actionsel_n == 0) return 0;            /* no subscribers */

    /* One wrapper, reused by every re-patch. */
    uint32_t *w = g_actionsel_wrapper;
    if (!w) {
        w = mhfu_cave_alloc(20);
        if (!w) { mhfu_log("[ai] cave exhausted for action executor wrapper"); return -1; }
        build_overlay_action_wrapper(w,
            (uint32_t)(uintptr_t)&mhfu_ai_bigmonster_action_dispatch_c);
        mhfu_hook_flush_caches();
        g_actionsel_wrapper = w;
    }

    /* Patch only the original prologue: our J means done, a JIT marker means too late. */
    uint32_t cur0 = *(volatile uint32_t *)MHFU_ACTION_EXECUTOR;
    uint32_t cur1 = *(volatile uint32_t *)(MHFU_ACTION_EXECUTOR + 4);
    if (cur0 != EXEC_WORD0 || cur1 != EXEC_WORD1) {
        mhfu_log("[ai] exec entry not original (0x%08lx) — skip patch",
                 (unsigned long)cur0);
        return -1;
    }
    *(volatile uint32_t *)MHFU_ACTION_EXECUTOR       = mips_j((uint32_t)(uintptr_t)w);
    *(volatile uint32_t *)(MHFU_ACTION_EXECUTOR + 4) = MIPS_NOP;
    mhfu_hook_flush_caches();

    g_actionsel_hook_installed = 1;
    mhfu_log("[ai] action executor (re)patched @ 0x%08X (wrapper 0x%08X)",
             MHFU_ACTION_EXECUTOR, (unsigned)(uintptr_t)w);
    return 0;
}

extern "C" int mhfu_ai_install_overlay_action_hook_fwd(void) { return install_overlay_action_hook(); }

/* EBOOT code: queued for the JIT-cold title/menu window. */
static int install_slot_hook(void)
{
    if (g_slot_hook_installed) return 0;

    uint32_t *w = mhfu_cave_alloc(20);
    if (!w) { mhfu_log("[ai] cave exhausted for slot wrapper"); return -1; }
    build_slot_wrapper(w, (uint32_t)(uintptr_t)&mhfu_ai_slot_picked_dispatch_c);
    mhfu_hook_flush_caches();

    uint32_t new0  = mips_j((uint32_t)(uintptr_t)w);
    uint32_t new1  = MIPS_NOP;

    mhfu_hook_rc_t r0 = mhfu_hook_word_when_quiet(
        MHFU_SLOT_LOOP + 0x00, SLOT_LOOP_WORD0, new0, AI_OWNER_TAG);
    mhfu_hook_rc_t r1 = mhfu_hook_word_when_quiet(
        MHFU_SLOT_LOOP + 0x04, SLOT_LOOP_WORD1, new1, AI_OWNER_TAG);
    if (r0 != MHFU_HOOK_OK || r1 != MHFU_HOOK_OK) {
        mhfu_log("[ai] slot loop queue failed (r0=%d r1=%d)", (int)r0, (int)r1);
        return -1;
    }

    g_slot_hook_installed = 1;
    mhfu_log("[ai] slot_picked patch queued @ 0x%08X (wrapper 0x%08X)",
             MHFU_SLOT_LOOP, (unsigned)(uintptr_t)w);
    return 0;
}

/* --- ai_step prefix --------------------------------------------------- */

extern "C" void mhfu_ai_step_dispatch_c(uint32_t entity)
{
    if (g_step_n == 0) return;
    if (!mhfu_entity_is_bigmonster(entity)) return;
    mhfu_bigmonster_ai_step_ctx_t ctx;
    ctx.entity_ptr   = entity;
    ctx.monster_type = mhfu_entity_monster_type(entity);
    for (int i = 0; i < g_step_n; i++) g_step_chain[i].cb(&ctx);
}

/* Entry detour on the AI tick: `J wrapper; nop` over its prologue (addiu sp,sp,-0x20;
 * sw ra,0xC(sp)); the wrapper calls the dispatcher, replays both and resumes at +8.
 * Queued for the JIT-cold title/menu window. */
static int install_step_hook(void)
{
    if (g_step_hook_installed) return 0;

    uint32_t *w = mhfu_cave_alloc(16);
    if (!w) { mhfu_log("[ai] cave exhausted for ai_step wrapper"); return -1; }

    uint32_t helper = (uint32_t)(uintptr_t)&mhfu_ai_step_dispatch_c;
    int i = 0;
    w[i++] = mips_addiu(MIPS_REG_SP, MIPS_REG_SP, -0x20);     /* our frame */
    w[i++] = mips_sw   (MIPS_REG_RA, 0x18, MIPS_REG_SP);
    w[i++] = mips_sw   (MIPS_REG_A0, 0x10, MIPS_REG_SP);
    w[i++] = mips_jal  (helper);
    w[i++] = MIPS_NOP;                                         /* delay slot */
    w[i++] = mips_lw   (MIPS_REG_A0, 0x10, MIPS_REG_SP);
    w[i++] = mips_lw   (MIPS_REG_RA, 0x18, MIPS_REG_SP);
    w[i++] = mips_addiu(MIPS_REG_SP, MIPS_REG_SP, 0x20);       /* pop our frame */
    w[i++] = AI_TICK_WORD0;
    w[i++] = AI_TICK_WORD1;
    w[i++] = mips_j    (MHFU_AI_TICK + 0x08);
    w[i++] = MIPS_NOP;                                         /* J delay slot */
    while (i < 16) w[i++] = MIPS_NOP;
    mhfu_hook_flush_caches();

    uint32_t new0  = mips_j((uint32_t)(uintptr_t)w);
    uint32_t new1  = MIPS_NOP;

    mhfu_hook_rc_t r0 = mhfu_hook_word_when_quiet(
        MHFU_AI_TICK + 0x00, AI_TICK_WORD0, new0, AI_OWNER_TAG);
    mhfu_hook_rc_t r1 = mhfu_hook_word_when_quiet(
        MHFU_AI_TICK + 0x04, AI_TICK_WORD1, new1, AI_OWNER_TAG);
    if (r0 != MHFU_HOOK_OK || r1 != MHFU_HOOK_OK) {
        mhfu_log("[ai] ai_step queue failed (r0=%d r1=%d)", (int)r0, (int)r1);
        return -1;
    }

    g_step_hook_installed = 1;
    mhfu_log("[ai] ai_step entry detour queued @ 0x%08X (wrapper 0x%08X)",
             MHFU_AI_TICK, (unsigned)(uintptr_t)w);
    return 0;
}

/* --- public registration --------------------------------------------- */

extern "C" mhfu_hook_rc_t mhfu_on_ai_overlay_loaded(
    mhfu_ai_overlay_loaded_cb_t cb, int priority)
{
    if (!cb) return MHFU_HOOK_BADARG;
    if (install_overlay_hook() != 0) return MHFU_HOOK_CONFLICT;
    CHAIN_INSERT(g_overlay_chain, g_overlay_n, MAX_HANDLERS, cb, priority);
    return MHFU_HOOK_OK;
}

extern "C" mhfu_hook_rc_t mhfu_off_ai_overlay_loaded(
    mhfu_ai_overlay_loaded_cb_t cb)
{
    if (!cb) return MHFU_HOOK_BADARG;
    CHAIN_REMOVE(g_overlay_chain, g_overlay_n, cb);
}

extern "C" mhfu_hook_rc_t mhfu_on_bigmonster_slot_picked(
    mhfu_bigmonster_slot_picked_cb_t cb, int priority)
{
    if (!cb) return MHFU_HOOK_BADARG;
    if (install_slot_hook() != 0) return MHFU_HOOK_CONFLICT;
    CHAIN_INSERT(g_slot_chain, g_slot_n, MAX_HANDLERS, cb, priority);
    return MHFU_HOOK_OK;
}

extern "C" mhfu_hook_rc_t mhfu_on_bigmonster_action_input(
    mhfu_bigmonster_action_input_cb_t cb, int priority)
{
    if (!cb) return MHFU_HOOK_BADARG;
    if (install_action_hook() != 0) return MHFU_HOOK_CONFLICT;
    CHAIN_INSERT(g_input_chain, g_input_n, MAX_HANDLERS, cb, priority);
    return MHFU_HOOK_OK;
}

extern "C" mhfu_hook_rc_t mhfu_on_bigmonster_action_decided(
    mhfu_bigmonster_action_decided_cb_t cb, int priority)
{
    if (!cb) return MHFU_HOOK_BADARG;
    if (install_action_hook() != 0) return MHFU_HOOK_CONFLICT;
    CHAIN_INSERT(g_action_chain, g_action_n, MAX_HANDLERS, cb, priority);
    return MHFU_HOOK_OK;
}

/* Fallback for a section change that drops the executor patch: re-arm and re-apply, which
 * is a no-op unless the original prologue is back. */
static int g_actionsel_section_hook = 0;
extern "C" void mhfu_ai_section_repatch_cb(const void * /*ctx*/)
{
    g_actionsel_hook_installed = 0;
    install_overlay_action_hook();
}

extern "C" mhfu_hook_rc_t mhfu_on_bigmonster_action(
    mhfu_bigmonster_action_cb_t cb, int priority)
{
    if (!cb) return MHFU_HOOK_BADARG;
    /* The executor is overlay code: patched from the overlay-loaded helper. */
    if (install_overlay_hook() != 0) return MHFU_HOOK_CONFLICT;
    if (!g_actionsel_section_hook) {
        mhfu_event_register(MHFU_EVENT_MAP_SECTION_ENTERED,
                            (mhfu_event_cb_t)(void *)mhfu_ai_section_repatch_cb);
        g_actionsel_section_hook = 1;
    }
    CHAIN_INSERT(g_actionsel_chain, g_actionsel_n, MAX_HANDLERS, cb, priority);
    return MHFU_HOOK_OK;
}

extern "C" mhfu_hook_rc_t mhfu_off_bigmonster_action(
    mhfu_bigmonster_action_cb_t cb)
{
    if (!cb) return MHFU_HOOK_BADARG;
    CHAIN_REMOVE(g_actionsel_chain, g_actionsel_n, cb);
}

extern "C" mhfu_hook_rc_t mhfu_on_bigmonster_ai_step(
    mhfu_bigmonster_ai_step_cb_t cb, int priority)
{
    if (!cb) return MHFU_HOOK_BADARG;
    if (install_step_hook() != 0) return MHFU_HOOK_CONFLICT;
    CHAIN_INSERT(g_step_chain, g_step_n, MAX_HANDLERS, cb, priority);
    return MHFU_HOOK_OK;
}

extern "C" mhfu_hook_rc_t mhfu_off_bigmonster_slot_picked(
    mhfu_bigmonster_slot_picked_cb_t cb)
{
    if (!cb) return MHFU_HOOK_BADARG;
    CHAIN_REMOVE(g_slot_chain, g_slot_n, cb);
}

extern "C" mhfu_hook_rc_t mhfu_off_bigmonster_action_input(
    mhfu_bigmonster_action_input_cb_t cb)
{
    if (!cb) return MHFU_HOOK_BADARG;
    CHAIN_REMOVE(g_input_chain, g_input_n, cb);
}

extern "C" mhfu_hook_rc_t mhfu_off_bigmonster_action_decided(
    mhfu_bigmonster_action_decided_cb_t cb)
{
    if (!cb) return MHFU_HOOK_BADARG;
    CHAIN_REMOVE(g_action_chain, g_action_n, cb);
}

extern "C" mhfu_hook_rc_t mhfu_off_bigmonster_ai_step(
    mhfu_bigmonster_ai_step_cb_t cb)
{
    if (!cb) return MHFU_HOOK_BADARG;
    CHAIN_REMOVE(g_step_chain, g_step_n, cb);
}

/* --- spawn / death observe ------------------------------------------ */

extern "C" mhfu_hook_rc_t mhfu_on_bigmonster_spawn(
    mhfu_bigmonster_spawn_cb_t cb, int priority)
{
    if (!cb) return MHFU_HOOK_BADARG;
    CHAIN_INSERT(g_bmspawn_chain, g_bmspawn_n, MAX_HANDLERS, cb, priority);
    return MHFU_HOOK_OK;
}

extern "C" mhfu_hook_rc_t mhfu_on_bigmonster_death(
    mhfu_bigmonster_death_cb_t cb, int priority)
{
    if (!cb) return MHFU_HOOK_BADARG;
    CHAIN_INSERT(g_bmdeath_chain, g_bmdeath_n, MAX_HANDLERS, cb, priority);
    return MHFU_HOOK_OK;
}

extern "C" mhfu_hook_rc_t mhfu_off_bigmonster_spawn(mhfu_bigmonster_spawn_cb_t cb)
{
    if (!cb) return MHFU_HOOK_BADARG;
    CHAIN_REMOVE(g_bmspawn_chain, g_bmspawn_n, cb);
}

extern "C" mhfu_hook_rc_t mhfu_off_bigmonster_death(mhfu_bigmonster_death_cb_t cb)
{
    if (!cb) return MHFU_HOOK_BADARG;
    CHAIN_REMOVE(g_bmdeath_chain, g_bmdeath_n, cb);
}

extern "C" mhfu_hook_rc_t mhfu_on_bigmonster_damaged(
    mhfu_bigmonster_damaged_cb_t cb, int priority)
{
    if (!cb) return MHFU_HOOK_BADARG;
    CHAIN_INSERT(g_bmdamage_chain, g_bmdamage_n, MAX_HANDLERS, cb, priority);
    return MHFU_HOOK_OK;
}

extern "C" mhfu_hook_rc_t mhfu_off_bigmonster_damaged(mhfu_bigmonster_damaged_cb_t cb)
{
    if (!cb) return MHFU_HOOK_BADARG;
    CHAIN_REMOVE(g_bmdamage_chain, g_bmdamage_n, cb);
}

/* From the registry's monster-spawn edge: seeds the HP tracker, fans out for big monsters. */
extern "C" void mhfu_ai_on_monster_spawn(int slot, uint32_t entity, uint8_t type,
                                         uint16_t hp)
{
    if (slot <= 0 || slot >= MHFU_REGISTRY_SLOTS) return;
    int is_big = mhfu_entity_is_bigmonster(entity);
    g_was_big[slot] = (uint8_t)is_big;
    g_last_hp[slot] = hp;
    if (!is_big) return;
    if (g_bmspawn_n == 0) return;
    mhfu_bigmonster_spawn_ctx_t c;
    c.entity_ptr   = entity;
    c.slot         = slot;
    c.monster_type = type;
    c._pad[0] = c._pad[1] = c._pad[2] = 0;
    c.initial_hp   = hp;
    for (int i = 0; i < g_bmspawn_n; i++) g_bmspawn_chain[i].cb(&c);
}

/* From the spawn poll thread: an HP drop fires on_damaged, HP reaching 0 fires on_death. */
extern "C" void mhfu_ai_poll_death(void)
{
    if (g_bmdeath_n == 0 && g_bmdamage_n == 0) return;
    for (int slot = 1; slot < MHFU_REGISTRY_SLOTS; slot++) {
        if (!g_was_big[slot]) continue;
        uint32_t e = mhfu_entity_at(slot);
        if (!e) { g_was_big[slot] = 0; continue; }
        uint16_t hp = mhfu_entity_hp(e);
        uint16_t prev = g_last_hp[slot];
        g_last_hp[slot] = hp;

        /* Before the death edge, so the killing blow is also damage; a rise never fires. */
        if (g_bmdamage_n != 0 && hp < prev) {
            mhfu_bigmonster_damaged_ctx_t d;
            d.entity_ptr   = e;
            d.slot         = slot;
            d.monster_type = mhfu_entity_monster_type(e);
            d._pad[0] = d._pad[1] = d._pad[2] = 0;
            d.hp       = hp;
            d.prev_hp  = prev;
            d.amount   = (uint16_t)(prev - hp);
            d._pad2    = 0;
            for (int i = 0; i < g_bmdamage_n; i++) g_bmdamage_chain[i].cb(&d);
        }

        if (g_bmdeath_n != 0 && prev > 0 && hp == 0) {
            mhfu_bigmonster_death_ctx_t c;
            c.entity_ptr   = e;
            c.slot         = slot;
            c.monster_type = mhfu_entity_monster_type(e);
            c._pad[0] = c._pad[1] = c._pad[2] = 0;
            for (int i = 0; i < g_bmdeath_n; i++) g_bmdeath_chain[i].cb(&c);
            g_was_big[slot] = 0;     /* one-shot: no refire on respawn */
        }
    }
}
