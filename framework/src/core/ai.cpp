/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* Big-monster AI events (mhfu/ai.h): dispatchers over the action picker, the slot loops, the
 * action executor and the AI tick, the hook the registry installs for each event's first
 * subscriber, the spawn, damage and death edges of the registry poll, and the poll's raising of
 * the monster events (mhfu/monster_events.h). */
#include "mhfu/ai.h"
#include "mhfu/monster_events.h"
#include "mhfu/mips.h"
#include "mhfu/log.h"
#include "mhfu/entity.h"
#include "addresses.gen.h"
#include "internal.h"

#define OWNER "mhfu_ai"

/* The picker's vtable slot per species; all four share MHFU_ACTION_PICKER. Listed so an
 * unmapped species is never hooked by accident. */
static const struct { const char *name; uint32_t slot; } k_picker_slots[] = {
    { "popo",     MHFU_POPO_VTABLE     + MHFU_MONSTER_VTABLE_PICK_ACTION },
    { "anteka",   MHFU_ANTEKA_VTABLE   + MHFU_MONSTER_VTABLE_PICK_ACTION },
    { "tigrex",   MHFU_TIGREX_VTABLE   + MHFU_MONSTER_VTABLE_PICK_ACTION },
    { "giadrome", MHFU_GIADROME_VTABLE + MHFU_MONSTER_VTABLE_PICK_ACTION },
};

/* EBOOT slot loop in the AI tick: $s1 = slot, $s2 = entity, $s0 = entity + 2*slot. */
#define SLOT_LOOP_WORD0     mips_sll (MIPS_REG_V0, MIPS_REG_S1, 6)
#define SLOT_LOOP_WORD1     mips_addu(MIPS_REG_V0, MIPS_REG_S2, MIPS_REG_V0)

/* The AI tick's prologue. */
#define AI_TICK_WORD0       mips_addiu(MIPS_REG_SP, MIPS_REG_SP, -0x20)
#define AI_TICK_WORD1       mips_sw   (MIPS_REG_RA, 0x0C, MIPS_REG_SP)

#define OVERLAY_AI_SIZE     0x00100000u   /* the AI overlay at MHFU_AI_OVERLAY */

/* Overlay slot loop, the one big monsters run (entity+0x288 bit 17 skips the EBOOT loop):
 * $s3 = slot, $s5 = entity, $s0 = entity + 2*slot, $s1 = slot*0xC8, $s6 = entity + slot*0x40. */
#define OVL_SLOT_LOOP_WORD0 mips_lw   (MIPS_REG_V1, MHFU_ENTITY_ACTION_LIST, MIPS_REG_S5)
#define OVL_SLOT_LOOP_WORD1 mips_addiu(MIPS_REG_A0, MIPS_REG_ZERO, 2)

/* Action executor f(entity, a1 = action id, a2, a3): picks the clip for every body slot at
 * once, so rewriting a1 is coherent where a per-slot write desyncs the body. It moves the
 * animation only; the attack is the move act_set picks. Its prologue: */
#define EXEC_WORD0          mips_addiu(MIPS_REG_SP, MIPS_REG_SP, -0x40)
#define EXEC_WORD1          mips_sw   (MIPS_REG_RA, 0x2C, MIPS_REG_SP)

/* --- big-monster predicate ------------------------------------------------------------ */

extern "C" int mhfu_entity_is_bigmonster(uint32_t entity_ptr)
{
    /* A species allowlist; the quest's target list would be exact. */
    if (!entity_ptr) return 0;
    uint8_t t = mhfu_entity_monster_type(entity_ptr);
    if (t == MHFU_MONSTER_TIGREX || t == MHFU_MONSTER_GIADROME) return 1;
    return 0;
}

/* --- per-species (vt8_input -> ptr) cache -----------------------------------------------
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

/* --- dispatchers: each copies its event's handlers and calls them through its type ------ */

/* The slot the loop is processing, for ctx.slot of the picker events; 0 until a
 * slot_picked subscriber hooks the loop. */
static volatile uint8_t g_current_slot = 0;

static uint8_t slot_dispatch(uint32_t entity, uint32_t slot)
{
    uint8_t s = (uint8_t)slot;
    g_current_slot = s;
    mhfu_handler_t h[MHFU_EVENT_MAX_HANDLERS];
    int n = mhfu_event_handlers(MHFU_EVENT_BIGMONSTER_SLOT_PICKED, h);
    if (n == 0 || !mhfu_entity_is_bigmonster(entity)) return s;

    mhfu_bigmonster_slot_picked_ctx_t ctx;
    ctx.entity_ptr    = entity;
    ctx.monster_type  = mhfu_entity_monster_type(entity);
    ctx.original_slot = s;
    ctx.action_count  = *(volatile uint16_t *)(entity + MHFU_ENTITY_SLOT_COUNT);

    uint8_t v = s;
    for (int i = 0; i < n; i++) v = ((mhfu_bigmonster_slot_picked_cb_t)h[i].fn)(&ctx, v);
    if (ctx.action_count == 0) return s;
    if (v >= ctx.action_count) v = (uint8_t)(ctx.action_count - 1);   /* the loop bound */
    g_current_slot = v;
    return v;
}

/* EBOOT loop: the new slot and $s0 derived from it; the displaced words recompute $v0. */
static void slot_pre(mhfu_regs_t *r)
{
    uint32_t s = slot_dispatch(r->s2, r->s1);
    if (s == r->s1) return;
    r->s1 = s;
    r->s0 = r->s2 + 2 * s;
}

static void ovl_slot_pre(mhfu_regs_t *r)
{
    uint32_t s = slot_dispatch(r->s5, r->s3);
    if (s == r->s3) return;
    r->s3 = s;
    r->s1 = s * 0xC8;
    r->s0 = r->s5 + 2 * s;
    r->s6 = r->s5 + s * 0x40;
}

/* Before vt[8]: the input it reads (low 16 bits). */
static void picker_pre(mhfu_regs_t *r)
{
    mhfu_handler_t h[MHFU_EVENT_MAX_HANDLERS];
    int n = mhfu_event_handlers(MHFU_EVENT_BIGMONSTER_ACTION_INPUT, h);
    if (n == 0 || !mhfu_entity_is_bigmonster(r->a0)) return;

    mhfu_bigmonster_action_input_ctx_t ctx;
    ctx.entity_ptr   = r->a0;
    ctx.monster_type = mhfu_entity_monster_type(r->a0);
    ctx.slot         = g_current_slot;
    ctx.vt8_input    = (uint16_t)r->a1;

    uint16_t v = ctx.vt8_input;
    for (int i = 0; i < n; i++) {
        v = ((mhfu_bigmonster_action_input_cb_t)h[i].fn)(&ctx, v);
        ctx.vt8_input = v;
    }
    if (v != (uint16_t)r->a1) r->a1 = v;
}

/* After vt[8]: caches the pair, then threads the picker's result through the chain. */
static void picker_post(mhfu_regs_t *r)
{
    if (!mhfu_entity_is_bigmonster(r->a0)) return;
    uint8_t  type  = mhfu_entity_monster_type(r->a0);
    uint16_t input = (uint16_t)r->a1;
    ai_cache_remember(type, input, r->v0);   /* with or without subscribers */

    mhfu_handler_t h[MHFU_EVENT_MAX_HANDLERS];
    int n = mhfu_event_handlers(MHFU_EVENT_BIGMONSTER_ACTION_DECIDED, h);
    if (n == 0) return;

    mhfu_bigmonster_action_decided_ctx_t ctx;
    ctx.entity_ptr   = r->a0;
    ctx.monster_type = type;
    ctx.slot         = g_current_slot;
    ctx.vt8_input    = input;

    uint32_t v = r->v0;
    for (int i = 0; i < n; i++) v = ((mhfu_bigmonster_action_decided_cb_t)h[i].fn)(&ctx, v);
    r->v0 = v;
}

/* The executor's entry: the action id in $a1. */
static void action_pre(mhfu_regs_t *r)
{
    mhfu_handler_t h[MHFU_EVENT_MAX_HANDLERS];
    int n = mhfu_event_handlers(MHFU_EVENT_BIGMONSTER_ACTION, h);
    if (n == 0 || !mhfu_entity_is_bigmonster(r->a0)) return;   /* the executor is shared */

    mhfu_bigmonster_action_ctx_t ctx;
    ctx.entity_ptr   = r->a0;
    ctx.monster_type = mhfu_entity_monster_type(r->a0);
    ctx.action_id    = (uint16_t)r->a1;

    uint32_t v = r->a1;
    for (int i = 0; i < n; i++) v = ((mhfu_bigmonster_action_cb_t)h[i].fn)(&ctx, v);
    r->a1 = v;
}

/* The AI tick's entry, every frame. */
static void step_pre(mhfu_regs_t *r)
{
    mhfu_handler_t h[MHFU_EVENT_MAX_HANDLERS];
    int n = mhfu_event_handlers(MHFU_EVENT_BIGMONSTER_AI_STEP, h);
    if (n == 0 || !mhfu_entity_is_bigmonster(r->a0)) return;

    mhfu_bigmonster_ai_step_ctx_t ctx;
    ctx.entity_ptr   = r->a0;
    ctx.monster_type = mhfu_entity_monster_type(r->a0);
    ctx._pad[0] = ctx._pad[1] = ctx._pad[2] = 0;
    for (int i = 0; i < n; i++) ((mhfu_bigmonster_ai_step_cb_t)h[i].fn)(&ctx);
}

/* --- overlay hooks ----------------------------------------------------------------------
 * A section roam reloads the overlay, which wipes our patches; its bytes are JIT-cold right
 * after the copy, so they are (re)patched from the loader's postfix. */

/* BADARG (neither the original words nor our J) is logged once by the hook layer. */
static void patch_overlay(uint32_t addr, uint32_t w0, uint32_t w1, mhfu_wrap_fn pre)
{
    mhfu_hook_rc_t rc = mhfu_hook_detour_now(addr, w0, w1, pre, OWNER);
    if (rc != MHFU_HOOK_OK && rc != MHFU_HOOK_BADARG)
        mhfu_log("[ai] overlay patch @0x%08lx: rc=%d", (unsigned long)addr, (int)rc);
}

/* Postfix on the loader's call into the per-segment loader, a few dozen calls per map load:
 * acts only while the executor holds its original prologue, i.e. on a fresh overlay. */
static void overlay_post(mhfu_regs_t *r)
{
    (void)r;
    if (*(volatile uint32_t *)MHFU_ACTION_EXECUTOR != EXEC_WORD0) return;

    if (mhfu_event_count(MHFU_EVENT_BIGMONSTER_SLOT_PICKED))
        patch_overlay(MHFU_BIGMON_SLOT_LOOP, OVL_SLOT_LOOP_WORD0, OVL_SLOT_LOOP_WORD1,
                      ovl_slot_pre);
    if (mhfu_event_count(MHFU_EVENT_BIGMONSTER_ACTION))
        patch_overlay(MHFU_ACTION_EXECUTOR, EXEC_WORD0, EXEC_WORD1, action_pre);

    mhfu_handler_t h[MHFU_EVENT_MAX_HANDLERS];
    int n = mhfu_event_handlers(MHFU_EVENT_AI_OVERLAY_LOADED, h);
    mhfu_ai_overlay_loaded_ctx_t ctx;
    ctx.dest = MHFU_AI_OVERLAY;
    ctx.src  = 0;
    ctx.size = OVERLAY_AI_SIZE;
    for (int i = 0; i < n; i++) ((mhfu_ai_overlay_loaded_cb_t)h[i].fn)(&ctx);
}

/* Fallback for an overlay reload the postfix missed; OK and silent while our J is in place. */
static void section_repatch(const mhfu_map_section_ctx_t *ctx)
{
    (void)ctx;
    if (mhfu_event_count(MHFU_EVENT_BIGMONSTER_ACTION) == 0) return;
    mhfu_hook_detour_now(MHFU_ACTION_EXECUTOR, EXEC_WORD0, EXEC_WORD1, action_pre, OWNER);
}

/* --- installers (events.cpp calls them for an event's first subscriber) ----------------- */

static int failed(const char *what, int rc)
{
    mhfu_log("[ai] %s: hook failed (rc=%d)", what, rc);
    return -1;
}

extern "C" int mhfu_ai_install_overlay_loaded(void)
{
    static int s_done = 0;
    if (s_done) return 0;
    mhfu_hook_rc_t rc = mhfu_hook_call(MHFU_OVERLAY_LOAD_CALL, MHFU_OVERLAY_SEGMENT_LOAD,
                                       0, overlay_post, OWNER);
    if (rc != MHFU_HOOK_OK) return failed("overlay loader", rc);
    s_done = 1;
    return 0;
}

/* The EBOOT loop is queued; the overlay loop is patched from the loader's postfix. */
extern "C" int mhfu_ai_install_slot_picked(void)
{
    mhfu_hook_rc_t rc = mhfu_hook_detour(MHFU_SLOT_LOOP, SLOT_LOOP_WORD0, SLOT_LOOP_WORD1,
                                         slot_pre, OWNER);
    if (rc != MHFU_HOOK_OK) return failed("slot loop", rc);
    return mhfu_ai_install_overlay_loaded();
}

/* A vtable swap is a data write, so the JIT cannot miss it. */
extern "C" int mhfu_ai_install_picker(void)
{
    static uint32_t s_wrapper = 0;
    if (!s_wrapper) {
        mhfu_wrap_t w = {};
        w.pre  = picker_pre;
        w.call = MHFU_ACTION_PICKER;
        w.post = picker_post;
        w.pc   = MHFU_ACTION_PICKER;
        s_wrapper = mhfu_wrap_build(&w);
        if (!s_wrapper) return failed("picker wrapper", MHFU_HOOK_NOSPACE);
    }
    int hooked = 0;
    for (const auto &p : k_picker_slots) {
        mhfu_hook_rc_t rc = mhfu_hook_vtable(p.slot, s_wrapper, OWNER);
        if (rc == MHFU_HOOK_OK) hooked++;
        else mhfu_log("[ai] vt[8] of %s: rc=%d", p.name, (int)rc);
    }
    return hooked ? 0 : -1;
}

extern "C" int mhfu_ai_install_ai_step(void)
{
    mhfu_hook_rc_t rc = mhfu_hook_detour(MHFU_AI_TICK, AI_TICK_WORD0, AI_TICK_WORD1,
                                         step_pre, OWNER);
    return rc == MHFU_HOOK_OK ? 0 : failed("ai tick", rc);
}

/* The executor is overlay code: patched from the loader's postfix. */
extern "C" int mhfu_ai_install_action(void)
{
    if (mhfu_ai_install_overlay_loaded() != 0) return -1;
    mhfu_hook_rc_t rc = mhfu_on_map_section_entered(section_repatch, 0, OWNER);
    return rc == MHFU_HOOK_OK ? 0 : failed("section re-patch", rc);
}

/* --- spawn, damage and death, from the registry poll ------------------------------------ */

/* Per registry slot: last HP and whether it holds a big monster, for the death edge. */
static uint16_t g_last_hp[MHFU_ENTITY_REGISTRY_COUNT];
static uint8_t  g_was_big[MHFU_ENTITY_REGISTRY_COUNT];

/* Every spawn seeds the HP tracker; big monsters fan out. */
extern "C" void mhfu_ai_on_monster_spawn(int slot, uint32_t entity, uint8_t type,
                                         uint16_t hp)
{
    if (slot <= 0 || slot >= MHFU_ENTITY_REGISTRY_COUNT) return;
    int is_big = mhfu_entity_is_bigmonster(entity);
    g_was_big[slot] = (uint8_t)is_big;
    g_last_hp[slot] = hp;
    if (!is_big) return;

    mhfu_handler_t h[MHFU_EVENT_MAX_HANDLERS];
    int n = mhfu_event_handlers(MHFU_EVENT_BIGMONSTER_SPAWN, h);
    mhfu_bigmonster_spawn_ctx_t c;
    c.entity_ptr   = entity;
    c.slot         = slot;
    c.monster_type = type;
    c._pad[0] = c._pad[1] = c._pad[2] = 0;
    c.initial_hp   = hp;
    for (int i = 0; i < n; i++) ((mhfu_bigmonster_spawn_cb_t)h[i].fn)(&c);
}

/* An HP drop fires on_damaged, HP reaching 0 fires on_death; the monster events the move
 * player's step found are raised first. */
extern "C" void mhfu_ai_poll_death(void)
{
    mhfu_monster_events_drain();
    int n_damaged = mhfu_event_count(MHFU_EVENT_BIGMONSTER_DAMAGED);
    int n_death   = mhfu_event_count(MHFU_EVENT_BIGMONSTER_DEATH);
    if (n_death == 0 && n_damaged == 0) return;
    for (int slot = 1; slot < MHFU_ENTITY_REGISTRY_COUNT; slot++) {
        if (!g_was_big[slot]) continue;
        uint32_t e = mhfu_entity_at(slot);
        if (!e) { g_was_big[slot] = 0; continue; }
        uint16_t hp = mhfu_entity_hp(e);
        uint16_t prev = g_last_hp[slot];
        g_last_hp[slot] = hp;

        /* Before the death edge, so the killing blow is also damage; a rise never fires. */
        if (n_damaged != 0 && hp < prev) {
            mhfu_bigmonster_damaged_ctx_t d;
            d.entity_ptr   = e;
            d.slot         = slot;
            d.monster_type = mhfu_entity_monster_type(e);
            d._pad[0] = d._pad[1] = d._pad[2] = 0;
            d.hp       = hp;
            d.prev_hp  = prev;
            d.amount   = (uint16_t)(prev - hp);
            d._pad2    = 0;
            mhfu_handler_t h[MHFU_EVENT_MAX_HANDLERS];
            int n = mhfu_event_handlers(MHFU_EVENT_BIGMONSTER_DAMAGED, h);
            for (int i = 0; i < n; i++) ((mhfu_bigmonster_damaged_cb_t)h[i].fn)(&d);
        }

        if (n_death != 0 && prev > 0 && hp == 0) {
            mhfu_bigmonster_death_ctx_t c;
            c.entity_ptr   = e;
            c.slot         = slot;
            c.monster_type = mhfu_entity_monster_type(e);
            c._pad[0] = c._pad[1] = c._pad[2] = 0;
            mhfu_handler_t h[MHFU_EVENT_MAX_HANDLERS];
            int n = mhfu_event_handlers(MHFU_EVENT_BIGMONSTER_DEATH, h);
            for (int i = 0; i < n; i++) ((mhfu_bigmonster_death_cb_t)h[i].fn)(&c);
            g_was_big[slot] = 0;     /* one-shot: no refire on respawn */
        }
    }
}
