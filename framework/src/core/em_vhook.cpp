/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/*
 * em_vhook: takes over a big monster's AI by wrapping four slots of its species
 * vtable. A species' AI is an overlay reached through a vtable in the EBOOT; the
 * slots are writable and re-read on every dispatch, and a wrapper that tail-calls
 * the original is indistinguishable from the original. Only a word is written.
 *
 *   slot 29, MONSTER_VTABLE.AI_STEP       the per-frame AI step, ~30.8/s
 *   slot 32, MONSTER_VTABLE.ENTER_ACTION  enters a behaviour pair:
 *                                         (entity, main, id, mode)
 *   slot 30, MONSTER_VTABLE.ANIM_EVENTS   the playing entry's events at clip frames:
 *                                         attacks, effects, sounds
 *   OBJ_VTABLE.BRAIN                      the per-frame brain around the AI step, which
 *                                         poses the joints (POSE_UPDATE, ATTACH)
 *
 * The stubs read a config block on every dispatch, so all of this is retargetable
 * from Lua without a rebuild (public surface: mhfu/em_vhook.h):
 *
 *   SUBSTITUTION  slot-32 pre: the engine's own (main, id) is rewritten to a
 *                 declared pair before the species enter-action runs, which is
 *                 where a pair is provisioned (a charge gets its run budget
 *                 ENTITY.ACTION_BUDGET, then act_set writes the state cells), so
 *                 a substituted charge ends into the skid like a native one; a
 *                 pair written straight into the cells parks with its hitbox spent.
 *   REQUEST       the brain, first: a pair Lua wants entered now, issued on the game
 *                 thread in the next AI frame through the engine's dispatcher
 *                 (MHFU_ENTER_ACTION), so it is provisioned too.
 *   CUT           the brain: while the tail cut waits for its drop (cut_waits), the
 *                 request, the plays and the rules wait, and the move player's start
 *                 with them; the events seen meanwhile reach the rules after the drop.
 *   TIP           OBJ_VTABLE.BRAIN post (wrap.h): the port's tip joints posed from
 *                 their carriers, after ATTACH and before the draw, until the drop.
 *   BRAIN         slot-29 pre, in C (brain()): the pair's dwell, the hunter's
 *                 distance and the monster events (monster_events.cpp), then at most
 *                 one of: an own move asked for, the move of a rule on the flinch (or on
 *                 the break it made) whose reaction was replaced, the AFTER of one of
 *                 ours that ended, a rule
 *                 ("in pair P or own move M for N frames, or on event E, player at
 *                 [lo,hi), receding -> enter Q or play own move M'", cooldown, budget);
 *                 last, the rules on the flinch arm the reaction replacement for the
 *                 host step that follows. Own moves sit in a registry by slot
 *                 (mhfu_em_move) and play through the move player (move.cpp) that same
 *                 AI frame.
 *   BUDGET        slot-32 post + slot-29 one-shot: the ENTITY.ACTION_BUDGET
 *                 override; the stubs carry it, but nothing arms it.
 *   STEP          slot-29 pre: a C function per AI frame (mhfu_em_step), which may
 *                 take the frame from the host step; the move player's seam (move.cpp).
 *   MUTE          slot 30: one entity's animation events skipped (mhfu_em_mute_events),
 *                 so a clip of a port's own does not fire the host entry's attacks.
 *
 * The pre-hook rewrites the arguments, not the cells: act_set is the only writer
 * of the cells and runs from the (rewritten) arguments; neither the enter-action
 * (MHFU_EM75_ENTER_ACTION) nor a translator reads the cells first (the cells lead
 * only on the snapshot-restore path, MHFU_ACTION_RESTORE). The enter-action also
 * returns early without act_set (mode 3/4 while pinned), where pre-written cells
 * would leave the phase machine running an unprovisioned pair. So the stub
 * rewrites a1/a2 only and counts afterwards whether the cells took the pair
 * (`sub_landed`).
 *
 * Both stubs are branchless and the slot-29 one is frame-free: the engine parks
 * thread stacks inside the PRX image, so the stub keeps no frame of its own. Every
 * decision is a MOVN/MOVZ select; the slot-29 stub's calls are jalrs (the step's
 * target selected between the C step and a `jr ra` in our block) with ra and the
 * step's arguments spilled to config words. The C it calls (the brain, the step) has
 * frames, which the engine's AI thread takes fine; a C frame on the construction
 * thread is what broke. The slot-32 stub keeps a 16-byte hand-written frame (see
 * build_act_stub).
 *
 * A slot's code is valid only while that species' overlay is resident, so we latch
 * onto the vtable of a big monster the engine spawned and restore on quest exit,
 * dropping every rule, substitution and request with it. One config block per
 * latched vtable, not per entity: two live monsters of that species would share
 * the dwell counter and the distance.
 */
#include "mhfu/call.h"
#include "mhfu/em_vhook.h"
#include "mhfu/events.h"
#include "mhfu/hooks.h"
#include "mhfu/log.h"
#include "mhfu/memory.h"
#include "mhfu/move.h"
#include "mhfu/monster_events.h"
#include "internal.h"
#include "em_vhook_stubs.h"
#include <stddef.h>

#ifndef MHFU_HOST
#include <pspsysmem.h>
#endif

#define OWNER "mhfu_em"

/* The config block, field for field the layout em_vhook_stubs.h indexes by offset. */
typedef struct {
    uint8_t  from_mask, from_sub, to_main, to_sub;
    uint32_t left;
} cfg_sub_t;
typedef struct {
    uint8_t  from_mask, from_sub, to_main, to_sub, mode, flags;
    uint8_t  from_move, play_move;   /* own move slot + 1, 0 none (mhfu_em_rule_t) */
    uint32_t min_frames, d2_lo, d2_hi, left, fired, last_fire, cooldown;
    uint8_t  on, part, force, _pad;  /* a monster event, 0 none; its part or MHFU_EM_ANY_PART */
} cfg_rule_t;
typedef struct {
    uint8_t  want_main, want_sub, armed, arm29;    /* +0x00 */
    uint32_t patch_off, patch_val, sink;            /* +0x04 */
    uint32_t ai_ticks, act_enters, last_pair;       /* +0x10 */
    uint32_t canary;                                /* +0x1C */
    uint32_t prev_pair, prev2_pair;                 /* +0x20 */
    uint32_t frames;                                /* +0x28 */
    uint32_t ra_spill, a_spill[4];                  /* +0x2C */
    uint32_t d2, d2_prev, brain_fires, scratch;     /* +0x40 */
    uint32_t req_pending;                           /* +0x50 */
    uint8_t  req_main, req_sub, req_mode, _pad;     /* +0x54 */
    uint32_t req_done, req_result;                  /* +0x58 */
    cfg_sub_t subs[MHFU_EM_SUBS];                  /* +0x60 */
    uint32_t sub_hits, sub_landed, sub_last_in;     /* +0x80 */
    uint32_t sub_pending, sub_to_pending;           /* +0x8C */
    uint32_t ring_idx;                              /* +0x94 */
    uint32_t ring[MHFU_EM_RING];                   /* +0x98 */
    uint32_t step_fn, skip;                         /* +0xB8 */
    uint32_t mute_ent, muted;                       /* +0xC0 */
    uint32_t react_ent, react_mask;                 /* +0xC8 */
    uint8_t  react_main, react_to_main, react_to_sub, react_parts;  /* +0xD0 */
    uint32_t react_gate, react_hits, react_last;    /* +0xD4 */
    cfg_rule_t rules[MHFU_EM_RULES];               /* +0xE0 */
} em_vhook_cfg_t;

static_assert(offsetof(em_vhook_cfg_t, prev_pair)   == CFG_PREV,        "cfg layout");
static_assert(offsetof(em_vhook_cfg_t, frames)      == CFG_FRAMES,      "cfg layout");
static_assert(offsetof(em_vhook_cfg_t, ra_spill)    == CFG_RA_SPILL,    "cfg layout");
static_assert(offsetof(em_vhook_cfg_t, a_spill)     == CFG_A0_SPILL,    "cfg layout");
static_assert(offsetof(em_vhook_cfg_t, d2)          == CFG_D2,          "cfg layout");
static_assert(offsetof(em_vhook_cfg_t, scratch)     == CFG_SCRATCH,     "cfg layout");
static_assert(offsetof(em_vhook_cfg_t, req_pending) == CFG_REQ_PENDING, "cfg layout");
static_assert(offsetof(em_vhook_cfg_t, req_main)    == CFG_REQ_MAIN,    "cfg layout");
static_assert(offsetof(em_vhook_cfg_t, req_result)  == CFG_REQ_RESULT,  "cfg layout");
static_assert(offsetof(em_vhook_cfg_t, subs)        == CFG_SUB_BASE,    "cfg layout");
static_assert(sizeof(cfg_sub_t)                     == SUB_STRIDE,      "cfg layout");
static_assert(offsetof(em_vhook_cfg_t, sub_hits)    == CFG_SUB_HITS,    "cfg layout");
static_assert(offsetof(em_vhook_cfg_t, sub_to_pending) == CFG_SUB_TO_PEND, "cfg layout");
static_assert(offsetof(em_vhook_cfg_t, ring_idx)    == CFG_RING_IDX,    "cfg layout");
static_assert(offsetof(em_vhook_cfg_t, ring)        == CFG_RING,        "cfg layout");
static_assert(offsetof(em_vhook_cfg_t, rules)       == CFG_RULE_BASE,   "cfg layout");
static_assert(sizeof(cfg_rule_t)                    == RULE_STRIDE,     "cfg layout");
static_assert(offsetof(em_vhook_cfg_t, step_fn)     == CFG_STEP_FN,     "cfg layout");
static_assert(offsetof(em_vhook_cfg_t, skip)        == CFG_SKIP,        "cfg layout");
static_assert(offsetof(em_vhook_cfg_t, mute_ent)    == CFG_MUTE_ENT,    "cfg layout");
static_assert(offsetof(em_vhook_cfg_t, muted)       == CFG_MUTED,       "cfg layout");
static_assert(offsetof(em_vhook_cfg_t, react_ent)   == CFG_REACT_ENT,   "cfg layout");
static_assert(offsetof(em_vhook_cfg_t, react_mask)  == CFG_REACT_MASK,  "cfg layout");
static_assert(offsetof(em_vhook_cfg_t, react_main)  == CFG_REACT_MAIN,  "cfg layout");
static_assert(offsetof(em_vhook_cfg_t, react_to_sub) == CFG_REACT_TO_SUB, "cfg layout");
static_assert(offsetof(em_vhook_cfg_t, react_parts) == CFG_REACT_PARTS, "cfg layout");
static_assert(offsetof(em_vhook_cfg_t, react_gate)  == CFG_REACT_GATE,  "cfg layout");
static_assert(offsetof(em_vhook_cfg_t, react_last)  == CFG_REACT_LAST,  "cfg layout");
static_assert(sizeof(em_vhook_cfg_t)                == CFG_SIZE,        "cfg layout");
static_assert(offsetof(cfg_rule_t, min_frames) == MHFU_EM_RULE_MIN_FRAMES, "EM_RULE layout");
static_assert(offsetof(cfg_rule_t, d2_lo)      == MHFU_EM_RULE_D2_LO,      "EM_RULE layout");
static_assert(offsetof(cfg_rule_t, left)       == MHFU_EM_RULE_LEFT,       "EM_RULE layout");
static_assert(offsetof(cfg_rule_t, last_fire)  == MHFU_EM_RULE_LAST_FIRE,  "EM_RULE layout");
static_assert(offsetof(cfg_rule_t, cooldown)   == MHFU_EM_RULE_COOLDOWN,   "EM_RULE layout");
static_assert(offsetof(cfg_rule_t, on)         == MHFU_EM_RULE_ON,         "EM_RULE layout");
static_assert(offsetof(cfg_rule_t, part)       == MHFU_EM_RULE_PART,       "EM_RULE layout");
static_assert(offsetof(cfg_rule_t, force)      == MHFU_EM_RULE_FORCE,      "EM_RULE layout");

#define CFG_CANARY_VAL 0x5645484Bu   /* 'VEHK' */

#define CUT_PAIR     0x0404u   /* em75's tail cut, EM75_REACTIONS code 11 */
#define TAIL_DROPPED 0x4000u   /* ENTITY.FLAGS' doc: TAIL_DROP ran */
#define TAIL_MESH    1u        /* the mesh TAIL_SPAWN's object draws (em75) */

/* The tail: the tip's map and the cut's wait, after the config in the block. */
typedef struct {
    uint32_t vtable;                       /* the wrapped species': its entities are posed */
    uint32_t count;                        /* pairs in force; 0 while written */
    uint8_t  pairs[MHFU_EM_TIP_MAX][2];    /* {joint, carrier} */
    uint32_t copies, waits;
    uint32_t held_ent;                     /* whose events wait for the drop */
    uint16_t held_edges;
    uint8_t  held_parts, _pad;
} tail_t;

/* The stubs and the config live outside the PRX image: the engine parks thread
 * stacks inside it, and stubs near its top were overwritten under heavy activity.
 * They come from the user partition, and only pointers live here. */
#define RET_INSNS   4     /* jr ra; nop, padded to 16 bytes */
#define STUBS       (STUB_AI_INSNS + STUB_ACT_INSNS + STUB_EVT_INSNS + MHFU_WRAP_WORDS + RET_INSNS)
#define BLOCK_BYTES (STUBS * 4 + CFG_SIZE + sizeof(tail_t) + 128)

static uint32_t *g_stub_ai;
static uint32_t *g_stub_act;
static uint32_t *g_stub_evt;
static uint32_t *g_stub_brain;        /* the OBJ_VTABLE.BRAIN wrapper (wrap.h) */
static uint32_t *g_stub_ret;
static em_vhook_cfg_t *g_cfgp;
static volatile tail_t *T;
static volatile mhfu_em_moves_t *R;   /* the own-move registry */

#define MOVES_MAGIC 0x564F4D45u        /* 'EMOV' */

static_assert(sizeof(mhfu_em_own_t) == MHFU_EM_OWN_SIZE, "EM_OWN layout");
static_assert(offsetof(mhfu_em_own_t, steer) == MHFU_EM_OWN_STEER, "EM_OWN layout");
static_assert(offsetof(mhfu_em_own_t, key_at) == MHFU_EM_OWN_KEY_AT, "EM_OWN layout");
static_assert(offsetof(mhfu_em_own_t, after) == MHFU_EM_OWN_AFTER, "EM_OWN layout");
static_assert(offsetof(mhfu_em_own_t, valid) == MHFU_EM_OWN_VALID, "EM_OWN layout");
static_assert(sizeof(mhfu_em_moves_t) == MHFU_EM_MOVES_SIZE, "EM_MOVES layout");
static_assert(offsetof(mhfu_em_moves_t, req_ent) == MHFU_EM_MOVES_REQ_ENT, "EM_MOVES layout");
static_assert(offsetof(mhfu_em_moves_t, tag_slot) == MHFU_EM_MOVES_TAG_SLOT, "EM_MOVES layout");
static_assert(offsetof(mhfu_em_moves_t, key_top) == MHFU_EM_MOVES_KEY_TOP, "EM_MOVES layout");
static_assert(offsetof(mhfu_em_moves_t, moves) == MHFU_EM_MOVES_MOVES, "EM_MOVES layout");
static_assert(offsetof(mhfu_em_moves_t, keys) == MHFU_EM_MOVES_KEYS, "EM_MOVES layout");
static_assert(offsetof(mhfu_em_moves_t, scratch) == MHFU_EM_MOVES_SCRATCH, "EM_MOVES layout");
static_assert(offsetof(mhfu_em_moves_t, req_force) == MHFU_EM_MOVES_REQ_FORCE, "EM_MOVES layout");
static_assert(MHFU_EM_MOVES_MOVES_COUNT == MHFU_EM_MOVES, "EM_MOVES layout");
static_assert(MHFU_EM_MOVES_KEYS_COUNT == MHFU_EM_KEYS, "EM_MOVES layout");

static void cfg_reset_live(void)
{
    /* everything the stubs derive from the running monster; (0,0) packs to 0, so
     * a zeroed history would read as two ticks in (0,0) and fire the one-shot */
    g_cfgp->prev_pair = g_cfgp->prev2_pair = 0xFFFFFFFFu;
    g_cfgp->frames = 0;
    g_cfgp->d2 = g_cfgp->d2_prev = 0;
    g_cfgp->sub_pending = g_cfgp->sub_to_pending = 0;
    g_cfgp->skip = 0;
    g_cfgp->mute_ent = 0;
}

static void place_block(uint8_t *base)
{
    base = (uint8_t *)(((uintptr_t)base + 63) & ~(uintptr_t)63);
    g_stub_ai  = (uint32_t *)base;
    g_stub_act = (uint32_t *)(base + STUB_AI_INSNS * 4);
    g_stub_evt = (uint32_t *)(base + (STUB_AI_INSNS + STUB_ACT_INSNS) * 4);
    g_stub_brain = (uint32_t *)(base + (STUB_AI_INSNS + STUB_ACT_INSNS + STUB_EVT_INSNS) * 4);
    g_stub_ret = (uint32_t *)(base + (STUBS - RET_INSNS) * 4);
    g_cfgp     = (em_vhook_cfg_t *)(base + STUBS * 4);
    T          = (volatile tail_t *)(base + STUBS * 4 + CFG_SIZE);
    for (unsigned k = 0; k < (CFG_SIZE + sizeof(tail_t)) / 4; k++)
        ((uint32_t *)g_cfgp)[k] = 0;
    cfg_reset_live();
}

static void place_moves(void *at)
{
    R = (volatile mhfu_em_moves_t *)(((uintptr_t)at + 63) & ~(uintptr_t)63);
    volatile uint32_t *w = (volatile uint32_t *)R;
    for (unsigned k = 0; k < sizeof(mhfu_em_moves_t) / 4; k++) w[k] = 0;
    R->tag_slot = MHFU_EM_NO_MOVE;
    R->magic = MOVES_MAGIC;
}

#ifndef MHFU_HOST
static int g_block = -1;

/* Low, not High: High lands in PPSSPP's extra-RAM window, and a stub there exits the emulator
 * once its store retargets into normal RAM. NULL on failure, logged. */
static void *alloc_low(const char *name, unsigned bytes, int *uid)
{
    *uid = sceKernelAllocPartitionMemory(2, name, PSP_SMEM_Low, bytes, 0);
    if (*uid < 0) {
        mhfu_log("[%s] partition alloc of %s FAILED (%d) - refusing", OWNER, name, *uid);
        return 0;
    }
    void *base = sceKernelGetBlockHeadAddr(*uid);
    if ((uintptr_t)base >= MHFU_USER_RAM_END) {
        mhfu_log("[%s] %s at 0x%08X is in the extra-RAM window - refusing", OWNER, name,
                 (unsigned)(uintptr_t)base);
        sceKernelFreePartitionMemory(*uid);
        *uid = -1;
        return 0;
    }
    return base;
}

static int alloc_block(void)
{
    if (g_block >= 0) return 0;
    uint8_t *base = (uint8_t *)alloc_low("em_vhook", BLOCK_BYTES, &g_block);
    if (!base) return -1;
    place_block(base);
    mhfu_log("[%s] block @0x%08X (outside the PRX image), cfg @0x%08X",
             OWNER, (unsigned)(uintptr_t)g_stub_ai, (unsigned)(uintptr_t)g_cfgp);
    return 0;
}

static int alloc_moves(void)
{
    int uid;
    void *at = alloc_low("em_moves", sizeof(mhfu_em_moves_t) + 64, &uid);
    if (!at) return -1;
    place_moves(at);
    mhfu_log("[%s] own moves @0x%08X", OWNER, (unsigned)(uintptr_t)R);
    return 0;
}
#endif

#ifndef MHFU_HOST
static uint32_t g_vtable;            /* the species vtable we latched onto */
static uint32_t g_orig_ai;
static uint32_t g_orig_act;
static uint32_t g_orig_evt;
#endif
static int      g_installed;

/* --- the brain: slot 29 calls it every AI frame, before the C step. ----
 *
 * It enters the pair mhfu_em_request asked for, keeps the pair's dwell, the hunter's distance and
 * the monster events, then plays or enters at most one thing: an own move asked for (mhfu_em_play), else the move of the rule on the flinch
 * whose reaction the last host step replaced, else the AFTER of the registry's move that just
 * ended on its clip, its length or a wall, else the first rule that holds. Last, the rules on the
 * flinch arm the reaction replacement for the host step that follows. An own move goes to the
 * move player here, and its step, called right after, enters the carrier the same AI frame. While
 * the tail cut waits for its drop it enters and plays nothing (cut_waits). Runs inside the
 * engine's AI step: it never logs. */

#ifndef MHFU_HOST
static void enter(uint32_t ent, uint32_t m, uint32_t s, uint32_t mode)
{
    mhfu_call(MHFU_ENTER_ACTION, ent, m, s, mode);
}
#else
extern "C" void mhfu_host_enter(uint32_t ent, uint32_t m, uint32_t s, uint32_t mode);
static void enter(uint32_t e, uint32_t m, uint32_t s, uint32_t mode) { mhfu_host_enter(e, m, s, mode); }
#endif

static void copy_words(volatile void *to, const volatile void *from, unsigned bytes)
{
    volatile uint32_t *t = (volatile uint32_t *)to;
    const volatile uint32_t *f = (const volatile uint32_t *)from;
    for (unsigned k = 0; k < bytes / 4; k++) t[k] = f[k];
}

static uint32_t pair_of(uint32_t ent)
{
    return ((uint32_t)mhfu_mem_read_u8(ent + MHFU_ENTITY_MAIN_STATE) << 8)
         | mhfu_mem_read_u8(ent + MHFU_ENTITY_SUB_STATE);
}

static uint32_t f32_bits(float f) { union { float f; uint32_t u; } v; v.f = f; return v.u; }
static float bits_f32(uint32_t u) { union { float f; uint32_t u; } v; v.u = u; return v.f; }

/* the move player runs a move, or one waits for its step */
static int busy(const volatile mhfu_move_state_t *m)
{
    return m->pending || m->state == MHFU_MOVE_ENTERING || m->state == MHFU_MOVE_PLAYING;
}

/* the slot the move player's current or last move came from, or -1 */
static int ours(const volatile mhfu_move_state_t *m)
{
    if (!R || R->tag_slot == MHFU_EM_NO_MOVE || m->started != R->tag_started) return -1;
    return (int)R->tag_slot;
}

/* slot's steering in the registry's scratch, keys and all */
static const volatile mhfu_steer_spec_t *steer_of(const volatile mhfu_em_own_t *o)
{
    volatile mhfu_steer_spec_t *sp = &R->scratch;
    copy_words(&sp->steer, &o->steer, sizeof(mhfu_steer_t));
    sp->key_count = o->key_count;
    sp->stuck_main = o->stuck_main;
    sp->stuck_sub = o->stuck_sub;
    sp->stuck_mode = o->stuck_mode;
    for (unsigned k = 0; k < o->key_count && k < MHFU_STEER_KEYS; k++)
        sp->keys[k] = R->keys[o->key_at + k];
    return sp;
}

/* the move player's next move is slot's: what ours() reads */
static void tag(uint32_t slot, const volatile mhfu_move_state_t *m)
{
    R->tag_slot = slot;
    R->tag_started = m->started + 1;   /* the step starts it right after us */
    R->plays++;
}

static int play_slot(uint32_t ent, uint32_t slot, const volatile mhfu_move_state_t *m, int force)
{
    if (slot >= MHFU_EM_MOVES || !R->moves[slot].valid) return 0;
    const volatile mhfu_em_own_t *o = &R->moves[slot];
    mhfu_move_t mv;
    copy_words(&mv, &o->move, sizeof(mv));
    mv.force = (uint8_t)(force != 0);
    mhfu_move_steer((const mhfu_steer_spec_t *)steer_of(o));
    if (!mhfu_move_play(ent, &mv)) return 0;
    tag(slot, m);
    return 1;
}

/* rule r's pair or own move, dwell, distance and cooldown hold on ent this frame */
static int gates(const cfg_rule_t *r, uint32_t ent, const volatile mhfu_move_state_t *m)
{
    uint32_t dwell = g_cfgp->frames;
    if (r->from_move) {
        if (ours(m) != r->from_move - 1 || m->state != MHFU_MOVE_PLAYING || m->entity != ent)
            return 0;
        dwell = m->frames;
    } else if (r->from_mask) {
        uint32_t pair = pair_of(ent);
        if (busy(m) || !((r->from_mask >> (pair >> 8)) & 1)) return 0;
        if (r->from_sub != MHFU_EM_SUB_ANY && r->from_sub != (pair & 0xFF)) return 0;
    }
    /* distances squared as f32 bits: non-negative floats order like unsigned ints */
    uint32_t d2 = g_cfgp->d2, prev = g_cfgp->d2_prev;
    if (dwell < r->min_frames || d2 < r->d2_lo || d2 >= r->d2_hi) return 0;
    if ((r->flags & MHFU_EM_RULE_RECEDING) && !(prev < d2)) return 0;
    if ((r->flags & MHFU_EM_RULE_CLOSING) && !(d2 < prev)) return 0;
    return g_cfgp->ai_ticks - r->last_fire >= r->cooldown;
}

static int part_of(const cfg_rule_t *r, uint8_t parts)
{
    return r->part == MHFU_EM_ANY_PART || (r->part < 8 && ((parts >> r->part) & 1));
}

/* rule r fires on ent this frame; edges: what the monster events raised, parts: what flinched */
static int holds(const cfg_rule_t *r, uint32_t ent, const volatile mhfu_move_state_t *m,
                 uint16_t edges, uint8_t parts)
{
    if (r->on == MHFU_MONSTER_FLINCH) return 0;   /* through the reaction replacement */
    if (r->on && (!((edges >> r->on) & 1) || !part_of(r, parts))) return 0;
    return gates(r, ent, m);
}

static void count_fire(cfg_rule_t *r)
{
    if (r->left != MHFU_EM_UNLIMITED) r->left--;
    r->fired++;
    r->last_fire = g_cfgp->ai_ticks;
    g_cfgp->brain_fires++;
    g_cfgp->frames = 0;
}

static int fire(cfg_rule_t *r, uint32_t ent, const volatile mhfu_move_state_t *m)
{
    if (r->play_move) {
        if (!play_slot(ent, r->play_move - 1u, m, r->force)) return 0;
    } else {
        enter(ent, r->to_main, r->to_sub, r->mode);
    }
    count_fire(r);
    return 1;
}

/* a live rule's own move, if it plays one */
static const volatile mhfu_em_own_t *rule_move(const cfg_rule_t *r)
{
    if (!r->left || !r->play_move || r->play_move > MHFU_EM_MOVES
        || !R->moves[r->play_move - 1].valid)
        return 0;
    return &R->moves[r->play_move - 1];
}

/* a rule on the flinch with a move to play: what the replacement may enter */
static const volatile mhfu_em_own_t *flinch_move(const cfg_rule_t *r)
{
    return r->on == MHFU_MONSTER_FLINCH ? rule_move(r) : 0;
}

static int g_reacts;        /* the brain owns the reaction replacement */
static uint32_t g_armed;    /* bit i: rule i's gates held when it was armed */

/* The replacement entered the carrier in place of a flinch in the last host step: a rule on the
 * break that flinch made, whose gates hold and whose own move rides that carrier, else the first
 * rule armed for it whose part flinched, hands the move player its move, which the step starts. */
static int react_take(uint32_t ent, const volatile mhfu_move_state_t *m, uint16_t edges)
{
    if (!g_reacts || !mhfu_move_react_pending(ent)) return 0;
    uint8_t flinched = mhfu_mem_read_u8(ent + MHFU_ENTITY_FLINCH_MASK);
    cfg_rule_t *first = 0;
    for (int i = 0; i < MHFU_EM_RULES && ((edges >> MHFU_MONSTER_PART_BROKEN) & 1); i++) {
        cfg_rule_t *r = &g_cfgp->rules[i];
        const volatile mhfu_em_own_t *o = rule_move(r);
        if (r->on == MHFU_MONSTER_PART_BROKEN && o && part_of(r, flinched) && gates(r, ent, m)
            && o->move.carrier_main == g_cfgp->react_to_main
            && o->move.carrier_sub == g_cfgp->react_to_sub) {
            first = r;
            break;
        }
    }
    for (int i = 0; i < MHFU_EM_RULES && !first; i++) {
        cfg_rule_t *r = &g_cfgp->rules[i];
        if (((g_armed >> i) & 1) && flinch_move(r) && part_of(r, flinched)) first = r;
    }
    if (!first) return 1;   /* the rules went meanwhile: the carrier runs as it is */
    const volatile mhfu_em_own_t *o = &R->moves[first->play_move - 1];
    mhfu_move_react_take((const mhfu_move_t *)&o->move, steer_of(o));
    tag(first->play_move - 1u, m);
    count_fire(first);
    return 1;
}

/* The parts the rules on the flinch take in the host step that follows, with their gates as they
 * stand now, on the first such rule's carrier. Off once no rule on the flinch is installed. */
static void react_arm(uint32_t ent, const volatile mhfu_move_state_t *m)
{
    int any = 0, have = 0;
    uint8_t parts = 0, cm = 0, cs = 0;
    g_armed = 0;
    for (int i = 0; i < MHFU_EM_RULES; i++) {
        const cfg_rule_t *r = &g_cfgp->rules[i];
        if (r->on != MHFU_MONSTER_FLINCH || !r->left) continue;
        any = 1;
        const volatile mhfu_em_own_t *o = flinch_move(r);
        if (!o || !gates(r, ent, m)) continue;
        if (!have) {
            cm = o->move.carrier_main;
            cs = o->move.carrier_sub;
            have = 1;
        } else if (o->move.carrier_main != cm || o->move.carrier_sub != cs) {
            continue;
        }
        parts |= r->part == MHFU_EM_ANY_PART ? 0xFF : (uint8_t)(1u << (r->part & 7));
        g_armed |= 1u << i;
    }
    if (!any) {
        if (g_reacts) mhfu_move_react_arm(0, 0, 0, 0);
        g_reacts = 0;
        return;
    }
    em_vhook_cfg_t *c = g_cfgp;
    if (!g_reacts || c->react_ent != ent || c->react_parts != parts
        || (have && (c->react_to_main != cm || c->react_to_sub != cs)))
        mhfu_move_react_arm(ent, parts, have ? cm : c->react_to_main,
                            have ? cs : c->react_to_sub);
    g_reacts = 1;
}

/* at most one thing played or entered; 1 when a replaced reaction waits for the step */
static int act(uint32_t ent, const volatile mhfu_move_state_t *m, uint16_t edges, uint8_t parts)
{
    if (R->req_ent == ent) {
        R->req_ent = 0;
        play_slot(ent, R->req_slot, m, (int)R->req_force);
        return 0;
    }
    if (react_take(ent, m, edges)) return 1;
    int k = ours(m);
    if (k >= 0 && m->state == MHFU_MOVE_DONE && m->entity == ent
        && (m->end == MHFU_MOVE_END_BACK || m->end == MHFU_MOVE_END_WALL)
        && R->moves[k].after != MHFU_EM_NO_MOVE) {
        if (play_slot(ent, R->moves[k].after, m, m->move.force)) R->chained++;
        else R->tag_slot = MHFU_EM_NO_MOVE;   /* an empty AFTER: not again */
        return 0;
    }
    for (int i = 0; i < MHFU_EM_RULES; i++) {
        cfg_rule_t *r = &g_cfgp->rules[i];
        if (r->left && holds(r, ent, m, edges, parts) && fire(r, ent, m)) break;
    }
    return 0;
}

/* The tail cut runs to its drop: ent is in the cut with SEVERED, TAIL_DROP has yet to run, and
 * its model has the mesh the dropped tail draws (a one-mesh port crashes it, so it keeps leaving
 * the cut as before). TAIL_DROP enters (4,15), which ends the wait. */
static int cut_waits(uint32_t ent)
{
    if (pair_of(ent) != CUT_PAIR || !(mhfu_mem_read_u8(ent + MHFU_ENTITY_SEVERED) & 1)
        || (mhfu_mem_read_u32(ent + MHFU_ENTITY_FLAGS) & TAIL_DROPPED))
        return 0;
    uint32_t pmo = mhfu_mem_read_u32(ent + MHFU_ENTITY_PMO);
    return pmo && mhfu_mem_read_u16(pmo + MHFU_PMO_MESH_COUNT) > TAIL_MESH;
}

extern "C" int mhfu_em_cut_waits(uint32_t entity) { return entity && cut_waits(entity); }

/* the pair mhfu_em_request asked for, through the engine's dispatcher */
static void request(uint32_t ent)
{
    em_vhook_cfg_t *c = g_cfgp;
    if (!c->req_pending) return;
    uint8_t m = c->req_main, s = c->req_sub, mode = c->req_mode;
    c->req_pending = 0;
    c->req_done++;
    enter(ent, m, s, mode);
    c->req_result = pair_of(ent);
    c->frames = 0;   /* the pair just changed: its dwell restarts */
}

/* The events of the AI frames the cut waited reach the rules in the first one after it. */
static void hold(uint32_t ent, uint16_t edges, uint8_t parts)
{
    if (T->held_ent != ent) {
        T->held_edges = 0;
        T->held_parts = 0;
    }
    T->held_ent = ent;
    T->held_edges |= edges;
    T->held_parts |= parts;
}

static uint16_t held(uint32_t ent, uint8_t *parts)
{
    if (T->held_ent != ent) return 0;
    uint16_t edges = T->held_edges;
    *parts |= T->held_parts;
    T->held_ent = 0;
    T->held_edges = 0;
    T->held_parts = 0;
    return edges;
}

static uint32_t brain(uint32_t ent)
{
    em_vhook_cfg_t *c = g_cfgp;
    const int waits = cut_waits(ent);
    if (waits) T->waits++;
    else request(ent);
    uint32_t pair = pair_of(ent);
    c->frames = pair == c->prev_pair ? c->frames + 1 : 0;
    float dx = mhfu_mem_read_f32(ent + MHFU_ENTITY_POSITION)
             - mhfu_mem_read_f32(MHFU_PLAYER_ENTITY + MHFU_ENTITY_TRANSLATION);
    float dz = mhfu_mem_read_f32(ent + MHFU_ENTITY_POSITION + 8)
             - mhfu_mem_read_f32(MHFU_PLAYER_ENTITY + MHFU_ENTITY_TRANSLATION + 8);
    c->d2_prev = c->d2;
    c->d2 = f32_bits(dx * dx + dz * dz);
    const volatile mhfu_move_state_t *m = mhfu_move_state();
    if (!m || !R) return 0;
    uint8_t parts;
    uint16_t edges = mhfu_monster_events_frame(ent, m->entity == ent && m->skipping, &parts);
    if (waits) {
        hold(ent, edges, parts);
        return 0;
    }
    edges |= held(ent, &parts);
    if (!act(ent, m, edges, parts)) react_arm(ent, m);
    return 0;
}

/* --- the tail tip: OBJ_VTABLE.BRAIN's post, after POSE_UPDATE and ATTACH, before the draw. ---
 * Each pair's JOINT.POSE from its carrier, on every entity of the wrapped species, until the
 * drop. On the game thread at ~30 Hz an entity: no allocation, no log. */
static void tip(uint32_t ent)
{
    const uint32_t n = T->count;
    if (!n || mhfu_mem_read_u32(ent + MHFU_ENTITY_VTABLE) != T->vtable
        || (mhfu_mem_read_u32(ent + MHFU_ENTITY_FLAGS) & TAIL_DROPPED))
        return;
    const uint32_t joints = mhfu_mem_read_u32(ent + MHFU_ENTITY_JOINTS);
    const uint32_t count = mhfu_mem_read_u16(ent + MHFU_ENTITY_JOINT_COUNT);
    if (!joints) return;
    for (uint32_t i = 0; i < n && i < MHFU_EM_TIP_MAX; i++) {
        const uint32_t j = T->pairs[i][0], k = T->pairs[i][1];
        if (j >= count || k >= count) continue;
        const uint32_t to = joints + j * MHFU_JOINT_SIZE + MHFU_JOINT_POSE;
        const uint32_t from = joints + k * MHFU_JOINT_SIZE + MHFU_JOINT_POSE;
        for (uint32_t w = 0; w < 4 * MHFU_JOINT_POSE_COUNT; w += 4)
            mhfu_mem_write_u32(to + w, mhfu_mem_read_u32(from + w));
    }
    T->copies++;
}

#ifndef MHFU_HOST
static void tip_post(mhfu_regs_t *r) { tip(r->a0); }
#endif

#ifndef MHFU_HOST
/* --- slot 29: count, request, the brain, the C step, one-shot budget, tail-call. ---
 *
 * A slot-32 post-hook owns ENTITY.ACTION_BUDGET for 15 of the 27 timer-gated
 * actions; the other 12 re-seed it in their handler's phase-0 block, on the first
 * slot-29 tick, one frame after any slot-32 hook can write.
 *
 * The write is a one-shot, on the action's second tick: re-asserting the budget
 * every frame would freeze it (the handler decrements, we restore, the action
 * never ends). Tick 1 is phase 0, where the handler seeds its own value after
 * this pre-hook; tick 2 only decrements, so ours lands and counts down.
 *
 *      write  iff  matched  &&  prev == cur  &&  prev2 != cur
 *
 * The request and the brain run first, so a pair they enter gets its phase-0 tick
 * in the step that follows and the one-shot lands like an engine entry's.
 *
 * Frame-free because it runs at ~30 Hz inside the engine's stacks: its jalrs
 * spill ra and the step's arguments to config words. Not reentrant, and need not
 * be: the step is dispatched once per frame from one site.
 */
static void build_ai_stub(uint32_t original)
{
    uint32_t cfg = (uint32_t)(uintptr_t)g_cfgp;
    uint32_t ret = (uint32_t)(uintptr_t)g_stub_ret;
    int overflow = 0;
    uint32_t fn = (uint32_t)(uintptr_t)brain;
    int n = emv_build_ai_stub(g_stub_ai, STUB_AI_INSNS, cfg, original, ret, fn, &overflow);
    if (overflow) {
        mhfu_log("[%s] ai stub is %d insns > STUB_AI_INSNS %d - installing a plain "
                 "trampoline instead", OWNER, n, STUB_AI_INSNS);
        g_stub_ai[0] = mips_j(original);
        g_stub_ai[1] = MIPS_NOP;
        return;
    }
    for (int k = n; k < STUB_AI_INSNS; k++) g_stub_ai[k] = MIPS_NOP;
    mhfu_log("[%s] ai stub: request + brain + C step + one-shot budget, %d insns, "
             "frame-free", OWNER, n);
}

/* --- slot 32: substitution pre part, the original, the budget post part. ---
 *
 * The budget store runs after the original: stored before a tail-call, the
 * species code overwrote it (1 of 85 samples read it back). After it, the action
 * clock is ours: a forced 30 ends the action in 0.55 s.
 *
 * This stub has a 16-byte stack frame, which the frame-free rule otherwise
 * forbids: it is 16 bytes of hand-written asm, not a C frame; the wrapped
 * function allocates 0x20 itself and calls deeper; and the stubs do not live in
 * the image. Slot 32 can be re-entered from a handler (the (2,9) handler calls
 * vt+0x88), so its ra goes on the stack, not in a config word.
 *
 * The budget match keys on the pair actually entered (after substitution), off
 * the frame: act_set runs inside the original, so before the call the state
 * cells still hold the previous action.
 *
 * Branchless: both candidates are computed and MOVN picks. On a miss the
 * substitution leaves a1/a2 alone and the budget store lands in cfg.sink, so
 * armed and unarmed runs execute the same instruction stream.
 */
static void build_act_stub(uint32_t original)
{
    uint32_t cfg = (uint32_t)(uintptr_t)g_cfgp;
    int overflow = 0;
    int n = emv_build_act_stub(g_stub_act, STUB_ACT_INSNS, cfg, original, &overflow);
    if (overflow) {
        mhfu_log("[%s] act stub is %d insns > STUB_ACT_INSNS %d - installing a plain "
                 "trampoline instead", OWNER, n, STUB_ACT_INSNS);
        g_stub_act[0] = mips_j(original);
        g_stub_act[1] = MIPS_NOP;
        return;
    }
    for (int k = n; k < STUB_ACT_INSNS; k++) g_stub_act[k] = MIPS_NOP;
    mhfu_log("[%s] act stub: %d-entry substitution PRE, original, budget POST; "
             "%d insns, frame 0x%X", OWNER, MHFU_EM_SUBS, n, ACT_FRAME);
}

/* --- slot 30: skipped for the muted entity. --- */
static void build_events_stub(uint32_t original)
{
    int overflow = 0;
    int n = emv_build_events_stub(g_stub_evt, STUB_EVT_INSNS, (uint32_t)(uintptr_t)g_cfgp,
                                  original, (uint32_t)(uintptr_t)g_stub_ret, &overflow);
    if (overflow) {
        g_stub_evt[0] = mips_j(original);
        g_stub_evt[1] = MIPS_NOP;
        return;
    }
    for (int k = n; k < STUB_EVT_INSNS; k++) g_stub_evt[k] = MIPS_NOP;
}
#endif

/* ------------------------------------------------------------ public API */

extern "C" int mhfu_em_installed(void) { return g_installed; }

/* `count` 0 clears; MHFU_EM_UNLIMITED is standing. */
extern "C" void mhfu_em_substitute(int slot, uint8_t from_mask, uint8_t from_sub,
                                    uint8_t to_main, uint8_t to_sub, uint32_t count)
{
    if (!g_cfgp || slot < 0 || slot >= MHFU_EM_SUBS) return;
    cfg_sub_t *s = &g_cfgp->subs[slot];
    /* order: disable first (left = 0 is the stub's off switch), then the
     * bytes, then enable — a dispatch racing in between sees off or the
     * complete new entry, never a half-written one */
    s->left = 0;
    s->from_mask = count ? from_mask : 0;
    s->from_sub  = from_sub;
    s->to_main   = to_main;
    s->to_sub    = to_sub;
    s->left      = count;
    if (count)
        mhfu_log("[%s] substitute[%d]: main mask 0x%02X sub %s -> (%u,%u) x%s",
                 OWNER, slot, from_mask,
                 from_sub == MHFU_EM_SUB_ANY ? "any" : "exact", to_main, to_sub,
                 count == MHFU_EM_UNLIMITED ? "standing" : "n");
    else
        mhfu_log("[%s] substitute[%d]: cleared", OWNER, slot);
}

#ifdef MHFU_HOST
static mhfu_em_step_fn g_host_step;   /* a host pointer does not fit the config word */
#endif

extern "C" void mhfu_em_step(mhfu_em_step_fn fn)
{
    if (g_cfgp) g_cfgp->step_fn = (uint32_t)(uintptr_t)fn;
#ifdef MHFU_HOST
    g_host_step = fn;
#endif
}

extern "C" void mhfu_em_mute_events(uint32_t entity)
{
    if (g_cfgp) g_cfgp->mute_ent = entity;
}

/* A pair to enter on the next AI frame, through the engine's dispatcher. */
extern "C" int mhfu_em_request(uint8_t main_state, uint8_t sub_state, uint8_t mode)
{
    if (!g_cfgp || !g_installed) return 0;
    g_cfgp->req_pending = 0;
    g_cfgp->req_main = main_state;
    g_cfgp->req_sub  = sub_state;
    g_cfgp->req_mode = mode;
    g_cfgp->req_pending = 1;
    return 1;
}

/* A native brain rule; NULL clears the slot. Distances go into the block squared,
 * as raw f32 bits, which is what the brain compares. */
extern "C" void mhfu_em_rule(int slot, const mhfu_em_rule_t *r)
{
    if (!g_cfgp || slot < 0 || slot >= MHFU_EM_RULES) return;
    cfg_rule_t *c = &g_cfgp->rules[slot];
    c->left = 0;                                    /* off while we write */
    int bad_on = r && (r->on > MHFU_MONSTER_TAIL_CUT
                       || (r->on == MHFU_MONSTER_FLINCH && !r->play_move));
    if (!r || r->count == 0 || bad_on || (!r->from_mask && !r->from_move && !r->on)) {
        c->from_mask = 0;
        c->from_move = 0;
        c->on = 0;
        mhfu_log("[%s] rule[%d]: cleared%s", OWNER, slot,
                 bad_on ? " (an event it cannot take; the flinch plays an own move)" : "");
        return;
    }
    float lo = r->dist_lo < 0 ? 0 : r->dist_lo;
    float hi = r->dist_hi < 0 ? 0 : r->dist_hi;
    c->from_mask  = r->from_mask;
    c->from_sub   = r->from_sub;
    c->to_main    = r->to_main;
    c->to_sub     = r->to_sub;
    c->mode       = r->mode;
    c->flags      = r->flags;
    c->from_move  = r->from_move;
    c->play_move  = r->play_move;
    c->on         = r->on;
    c->part       = r->part;
    c->force      = r->force;
    c->min_frames = r->min_frames;
    c->d2_lo      = f32_bits(lo * lo);
    c->d2_hi      = f32_bits(hi * hi);
    c->cooldown   = r->cooldown;
    c->fired      = 0;
    c->last_fire  = 0;
    c->left       = r->count;
    mhfu_log("[%s] rule[%d]: on %s part %d, from main mask 0x%02X sub %s / own move %d, "
             ">=%u frames, d in [%d,%d)%s%s -> enter (%u,%u,m%u) / own move %d, cooldown %u, x%s",
             OWNER, slot, r->on ? mhfu_monster_event_names[r->on - 1] : "-",
             r->part == MHFU_EM_ANY_PART ? -1 : (int)r->part, r->from_mask,
             r->from_sub == MHFU_EM_SUB_ANY ? "any" : "exact", (int)r->from_move - 1,
             (unsigned)r->min_frames, (int)lo, (int)hi,
             (r->flags & MHFU_EM_RULE_RECEDING) ? ", receding" : "",
             (r->flags & MHFU_EM_RULE_CLOSING)  ? ", closing"  : "",
             r->to_main, r->to_sub, r->mode, (int)r->play_move - 1, (unsigned)r->cooldown,
             r->count == MHFU_EM_UNLIMITED ? "standing" : "n");
}

extern "C" void mhfu_em_clear(void)
{
    if (!g_cfgp) return;
    g_cfgp->req_pending = 0;
    if (R) R->req_ent = 0;
    for (int i = 0; i < MHFU_EM_SUBS; i++)  { g_cfgp->subs[i].left = 0; g_cfgp->subs[i].from_mask = 0; }
    for (int i = 0; i < MHFU_EM_RULES; i++) { g_cfgp->rules[i].left = 0; g_cfgp->rules[i].from_mask = 0;
                                               g_cfgp->rules[i].from_move = 0; g_cfgp->rules[i].on = 0; }
    if (g_reacts) mhfu_move_react_arm(0, 0, 0, 0);
    g_reacts = 0;
    T->held_ent = 0;
}

/* --- the tail tip --- */

static int g_tip_wrapped;   /* OBJ_VTABLE.BRAIN carries tip() */

/* count off while the pairs are written, as a rule's LEFT */
extern "C" int mhfu_em_tip(const uint8_t (*pairs)[2], int n)
{
    if (!T || !g_installed || !g_tip_wrapped || n < 0 || n > MHFU_EM_TIP_MAX || (n && !pairs))
        return 0;
    T->count = 0;
    for (int i = 0; i < n; i++) {
        T->pairs[i][0] = pairs[i][0];
        T->pairs[i][1] = pairs[i][1];
    }
    T->count = (uint32_t)n;
    mhfu_log("[%s] tail tip: %d pair(s)", OWNER, n);
    return 1;
}

/* --- own moves --- */

extern "C" void mhfu_em_moves_clear(void)
{
    if (!R) return;
    R->req_ent = 0;
    for (int i = 0; i < MHFU_EM_MOVES; i++) R->moves[i].valid = 0;
    R->key_top = 0;
}

/* A slot is rewritten off (VALID 0) and its keys appended: a brain that reads it meanwhile sees
 * it empty, and keys a rewrite leaves behind wait for mhfu_em_moves_clear. */
extern "C" int mhfu_em_move(int slot, const mhfu_move_t *mv, const mhfu_steer_spec_t *s,
                            uint8_t after)
{
    if (!R || !mv || !s || slot < 0 || slot >= MHFU_EM_MOVES) return 0;
    uint32_t n = s->key_count > MHFU_STEER_KEYS ? MHFU_STEER_KEYS : s->key_count;
    if (R->key_top + n > MHFU_EM_KEYS) {
        mhfu_log("[%s] own move %d: %u turn keys, %u left", OWNER, slot, (unsigned)n,
                 (unsigned)(MHFU_EM_KEYS - R->key_top));
        return 0;
    }
    volatile mhfu_em_own_t *o = &R->moves[slot];
    o->valid = 0;
    copy_words(&o->move, mv, sizeof(*mv));
    /* the carrier again at the end, so AFTER follows before the host brain picks */
    if (after != MHFU_EM_NO_MOVE && mv->back_main == MHFU_MOVE_NO_PAIR) {
        o->move.back_main = mv->carrier_main;
        o->move.back_sub = mv->carrier_sub;
        o->move.back_mode = 0;
    }
    copy_words(&o->steer, &s->steer, sizeof(s->steer));
    o->key_at = (uint16_t)R->key_top;
    o->key_count = (uint16_t)n;
    for (uint32_t k = 0; k < n; k++) R->keys[R->key_top + k] = s->keys[k];
    R->key_top += n;
    o->stuck_main = s->stuck_main;
    o->stuck_sub = s->stuck_sub;
    o->stuck_mode = s->stuck_mode;
    o->after = after;
    o->valid = 1;
    return 1;
}

extern "C" int mhfu_em_play(uint32_t entity, int slot, int force)
{
    if (!R || !g_installed || !entity || slot < 0 || slot >= MHFU_EM_MOVES
        || !R->moves[slot].valid)
        return 0;
    R->req_ent = 0;
    R->req_slot = (uint32_t)slot;
    R->req_force = (uint32_t)(force != 0);
    R->req_ent = entity;
    return 1;
}

extern "C" int mhfu_em_playing(void)
{
    const volatile mhfu_move_state_t *m = mhfu_move_state();
    if (!m || !(m->state == MHFU_MOVE_ENTERING || m->state == MHFU_MOVE_PLAYING)) return -1;
    return ours(m);
}

extern "C" const volatile mhfu_em_moves_t *mhfu_em_moves(void) { return R; }

static float sqrt_approx(float x)
{
    /* the status is for logs; `sqrt.s` is one Allegrex instruction */
    if (!(x > 0)) return 0;
    return __builtin_sqrtf(x);
}

extern "C" void mhfu_em_status(mhfu_em_status_t *out)
{
    if (!out) return;
    for (unsigned k = 0; k < sizeof(*out) / 4; k++) ((uint32_t *)out)[k] = 0;
    if (!g_cfgp) return;
    out->installed   = (uint32_t)g_installed;
    out->ai_ticks    = g_cfgp->ai_ticks;
    out->act_enters  = g_cfgp->act_enters;
    out->last_pair   = g_cfgp->last_pair;
    out->frames      = g_cfgp->frames;
    out->dist        = sqrt_approx(bits_f32(g_cfgp->d2));
    out->sub_hits    = g_cfgp->sub_hits;
    out->sub_landed  = g_cfgp->sub_landed;
    out->sub_last_in = g_cfgp->sub_last_in;
    out->brain_fires = g_cfgp->brain_fires;
    out->events_muted = g_cfgp->muted;
    out->tip_pairs   = T->count;
    out->tip_copies  = T->copies;
    out->cut_waits   = T->waits;
    out->req_pending = g_cfgp->req_pending;
    out->req_done    = g_cfgp->req_done;
    out->req_result  = g_cfgp->req_result;
    out->ring_idx    = g_cfgp->ring_idx;
    for (int i = 0; i < MHFU_EM_RING; i++)  out->ring[i] = g_cfgp->ring[i];
    for (int i = 0; i < MHFU_EM_RULES; i++) { out->rule_fired[i] = g_cfgp->rules[i].fired;
                                               out->rule_left[i]  = g_cfgp->rules[i].left; }
    for (int i = 0; i < MHFU_EM_SUBS; i++)  out->sub_left[i] = g_cfgp->subs[i].left;
}

/* --- the reaction replacement: one entry, owned by the move player (move.cpp) ---------- */

extern "C" void mhfu_em_react(uint32_t entity, uint8_t main_state, uint32_t sub_mask,
                              uint16_t gate, uint8_t parts, uint8_t to_main, uint8_t to_sub)
{
    if (!g_cfgp) return;
    g_cfgp->react_ent = 0;                          /* off while we write */
    g_cfgp->react_main = main_state;
    g_cfgp->react_mask = sub_mask;
    g_cfgp->react_gate = gate;
    g_cfgp->react_parts = parts;
    g_cfgp->react_to_main = to_main;
    g_cfgp->react_to_sub = to_sub;
    g_cfgp->react_ent = entity;
}

extern "C" uint32_t mhfu_em_react_hits(uint32_t *last)
{
    if (!g_cfgp) return 0;
    if (last) *last = g_cfgp->react_last;
    return g_cfgp->react_hits;
}

/* ------------------------------------------------------------ latch */
#ifndef MHFU_HOST

/* The tip's seam, rebuilt in place for this species' brain; without it em_vhook runs on and
 * mhfu_em_tip refuses. */
static void wrap_brain(uint32_t vt)
{
    const uint32_t slot = vt + MHFU_OBJ_VTABLE_BRAIN;
    const uint32_t orig = mhfu_mem_read_u32(slot);
    mhfu_wrap_t w = {};
    w.call = orig;
    w.post = tip_post;
    w.pc = orig;
    T->count = 0;
    T->vtable = vt;
    if (!mhfu_wrap_build_at(g_stub_brain, &w)) {
        mhfu_log("[%s] brain 0x%08X: no wrapper, no tail tip", OWNER, (unsigned)orig);
        return;
    }
    mhfu_hook_rc_t rc = mhfu_hook_vtable(slot, (uint32_t)(uintptr_t)g_stub_brain, OWNER);
    g_tip_wrapped = rc == MHFU_HOOK_OK;
    mhfu_log("[%s] brain 0x%08X -> 0x%08X%s", OWNER, (unsigned)orig,
             (unsigned)(uintptr_t)g_stub_brain, g_tip_wrapped ? "" : ": claim failed, no tail tip");
}

static void install_for(uint32_t entity)
{
    if (g_installed || !entity) return;
    if (alloc_block() < 0) return;
    uint32_t vt = mhfu_mem_read_u32(entity);
    /* every species entity vtable lies in this band: a corrupt pointer never
     * makes us write elsewhere */
    if (vt < MHFU_VTABLE_BAND || vt >= MHFU_VTABLE_BAND_END) {
        mhfu_log("[%s] entity 0x%08X vtable 0x%08X outside the species band "
                 "- not a big monster, skipping", OWNER,
                 (unsigned)entity, (unsigned)vt);
        return;
    }
    g_vtable  = vt;
    g_orig_ai  = mhfu_mem_read_u32(vt + MHFU_MONSTER_VTABLE_AI_STEP);
    g_orig_act = mhfu_mem_read_u32(vt + MHFU_MONSTER_VTABLE_ENTER_ACTION);
    g_orig_evt = mhfu_mem_read_u32(vt + MHFU_MONSTER_VTABLE_ANIM_EVENTS);

    cfg_reset_live();
    emv_build_ret_stub(g_stub_ret);
    g_stub_ret[2] = MIPS_NOP; g_stub_ret[3] = MIPS_NOP;
    build_ai_stub(g_orig_ai);
    build_act_stub(g_orig_act);
    build_events_stub(g_orig_evt);
    mhfu_hook_flush_caches();

    /* data writes, not code patches: they take outside the JIT-cold window */
    mhfu_hook_rc_t rc = mhfu_hook_vtable(vt + MHFU_MONSTER_VTABLE_AI_STEP,
                                         (uint32_t)(uintptr_t)g_stub_ai, OWNER);
    if (rc == MHFU_HOOK_OK)
        rc = mhfu_hook_vtable(vt + MHFU_MONSTER_VTABLE_ENTER_ACTION,
                              (uint32_t)(uintptr_t)g_stub_act, OWNER);
    if (rc == MHFU_HOOK_OK)
        rc = mhfu_hook_vtable(vt + MHFU_MONSTER_VTABLE_ANIM_EVENTS,
                              (uint32_t)(uintptr_t)g_stub_evt, OWNER);
    if (rc != MHFU_HOOK_OK) {
        mhfu_hook_release(OWNER);
        mhfu_log("[%s] vtable 0x%08X: slot claim failed (rc=%d), not installed",
                 OWNER, (unsigned)vt, (int)rc);
        return;
    }
    g_installed = 1;

    mhfu_log("[%s] vtable 0x%08X: slot29 0x%08X -> 0x%08X, slot32 0x%08X -> 0x%08X, "
             "slot30 0x%08X -> 0x%08X", OWNER, (unsigned)vt, (unsigned)g_orig_ai,
             (unsigned)(uintptr_t)g_stub_ai, (unsigned)g_orig_act,
             (unsigned)(uintptr_t)g_stub_act, (unsigned)g_orig_evt,
             (unsigned)(uintptr_t)g_stub_evt);
    wrap_brain(vt);
}

static void uninstall(void)
{
    if (!g_installed) return;
    mhfu_hook_release(OWNER);
    g_installed = 0;
    g_tip_wrapped = 0;
    T->count = 0;
    g_cfgp->step_fn = 0;
    mhfu_em_clear();
    mhfu_log("[%s] restored vtable 0x%08X (ai_ticks=%u act_enters=%u sub %u/%u "
             "req %u brain %u tip %u cut %u)", OWNER, (unsigned)g_vtable,
             (unsigned)g_cfgp->ai_ticks, (unsigned)g_cfgp->act_enters,
             (unsigned)g_cfgp->sub_hits, (unsigned)g_cfgp->sub_landed,
             (unsigned)g_cfgp->req_done, (unsigned)g_cfgp->brain_fires,
             (unsigned)T->copies, (unsigned)T->waits);
}

static void on_spawn(const mhfu_monster_spawn_ctx_t *ctx)
{
    if (!ctx) return;
    install_for(ctx->entity_ptr);
}

static void on_quest(const mhfu_event_ctx_t *ctx)
{
    (void)ctx;
    /* a new quest may load another species' overlay, so the saved slots no
     * longer describe resident code: drop the hook and everything declared for
     * the old monster, and re-latch on the next big-monster spawn */
    uninstall();
    g_cfgp->ai_ticks = g_cfgp->act_enters = 0;
    g_cfgp->sub_hits = g_cfgp->sub_landed = g_cfgp->brain_fires = 0;
    g_cfgp->muted = 0;
    g_cfgp->req_done = 0;
    R->tag_slot = MHFU_EM_NO_MOVE;
}

extern "C" int mhfu_em_init(void)
{
    if (alloc_block() < 0 || alloc_moves() < 0) return -1;
    g_cfgp->patch_off = MHFU_ENTITY_ACTION_BUDGET;
    g_cfgp->patch_val = 900;
    g_cfgp->want_main = 0xFF;      /* matches nothing: nothing arms the budget override */
    g_cfgp->want_sub  = 0xFF;
    g_cfgp->canary    = CFG_CANARY_VAL;
    if (mhfu_on_monster_spawned(on_spawn, 0, OWNER) != MHFU_HOOK_OK ||
        mhfu_on_quest_beginning(on_quest, 0, OWNER) != MHFU_HOOK_OK) {
        mhfu_event_release(OWNER);
        mhfu_log("[%s] event registration failed", OWNER);
        return -1;
    }
    mhfu_log("[%s] ready; cfg @0x%08X, stubs @0x%08X / 0x%08X", OWNER,
             (unsigned)(uintptr_t)g_cfgp,
             (unsigned)(uintptr_t)g_stub_ai, (unsigned)(uintptr_t)g_stub_act);
    mhfu_move_init();
    return 0;
}
#else
/* host tests: the blocks in static memory, a monster wrapped, and the slot-29 stub's order in
 * C: the brain (the request first), the C step, then the pair history. */
static uint32_t g_host_block[(BLOCK_BYTES + 64) / 4];
static uint32_t g_host_moves[(sizeof(mhfu_em_moves_t) + 64) / 4];

extern "C" int mhfu_em_init(void)
{
    place_block((uint8_t *)g_host_block);
    place_moves(g_host_moves);
    g_installed = 1;
    g_tip_wrapped = 1;
    g_host_step = 0;
    mhfu_move_init();
    return 0;
}

/* the species vtable latched, as install_for's */
extern "C" void mhfu_em_host_latch(uint32_t vtable) { T->vtable = vtable; }

/* the brain slot as its wrapper runs it: the species brain (em_host.cpp), then the tip */
extern "C" void mhfu_host_brain(uint32_t ent);
extern "C" void mhfu_em_host_brain(uint32_t ent)
{
    mhfu_host_brain(ent);
    tip(ent);
}

extern "C" uint32_t mhfu_em_host_frame(uint32_t ent)
{
    g_cfgp->ai_ticks++;
    brain(ent);
    uint32_t skip = g_host_step ? g_host_step(ent) : 0;
    g_cfgp->prev2_pair = g_cfgp->prev_pair;
    g_cfgp->prev_pair = pair_of(ent);
    return skip;
}

extern "C" void mhfu_em_host_quest(void)
{
    mhfu_em_clear();
    R->tag_slot = MHFU_EM_NO_MOVE;
}

/* the engine's reaction entering (main, sub) mode 2 through the slot-32 stub's replacement,
 * modelled (test_em_vhook_stubs.py runs the stub itself) */
extern "C" void mhfu_em_host_reaction(uint32_t ent, uint8_t main_state, uint8_t sub)
{
    em_vhook_cfg_t *c = g_cfgp;
    uint8_t gate = mhfu_mem_read_u8(ent + (c->react_gate & 0x7FF));
    if (c->react_ent == ent && main_state == c->react_main && sub < 32
        && ((c->react_mask >> sub) & 1) && (gate & c->react_parts)) {
        c->react_hits++;
        c->react_last = (2u << 16) | ((uint32_t)main_state << 8) | sub;
        main_state = c->react_to_main;
        sub = c->react_to_sub;
    }
    enter(ent, main_state, sub, 2);
}
#endif
