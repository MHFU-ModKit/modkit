/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* The move player (mhfu/move.h), stepped by em_vhook's slot-29 stub on the game thread.
 *
 *   play()      NEXT and PENDING in the block, then the step goes in (mhfu_em_step)
 *   step, AI frame 0: enter the carrier through ENTER_ACTION; the host step then runs its
 *               phase 0, which dispatches the carrier's own clip
 *   frame 1:    the move's entry to the executor: our clip on every part
 *   frame 2..:  attacks at their frames, the end checks, SKIP's choice
 *   end:        windowed attacks still live are ended; the step comes out once nothing is
 *               pending
 *
 * An attack node is ended through its own end state (ATTACK_NODE_VTABLE.END): it stops being
 * tested at once, and the collision world deletes it on its next update. Only a node that is
 * still ours is touched: the vtable read at the spawn, our entity as owner, live, and not the
 * address a later spawn of this move got.
 *
 * The block lives in partition memory, like em_vhook's, because thread stacks reach into the
 * PRX image; nothing but its pointer is kept here. The step never logs: it runs inside the
 * engine's AI step. */
#include "mhfu/move.h"
#include "mhfu/em_vhook.h"
#include "mhfu/memory.h"
#include "mhfu/log.h"
#include "mhfu/events.h"
#include "addresses.gen.h"
#include "internal.h"
#include <stddef.h>

#ifndef MHFU_HOST
#include <pspsysmem.h>
#endif

#define OWNER "mhfu_move"
#define MAGIC 0x4D4F5650u   /* 'MOVP' */
#define NEVER 0xFFFFFFFFu
#define STOP  2u            /* PENDING: end the running move */

static_assert(sizeof(mhfu_move_attack_t) == MHFU_MOVE_ATTACK_SIZE, "MOVE_ATTACK layout");
static_assert(offsetof(mhfu_move_attack_t, id) == MHFU_MOVE_ATTACK_ID, "MOVE_ATTACK layout");
static_assert(offsetof(mhfu_move_attack_t, end) == MHFU_MOVE_ATTACK_END, "MOVE_ATTACK layout");
static_assert(sizeof(mhfu_move_t) == MHFU_MOVE_SIZE, "MOVE layout");
static_assert(offsetof(mhfu_move_t, carrier_main) == MHFU_MOVE_CARRIER_MAIN, "MOVE layout");
static_assert(offsetof(mhfu_move_t, back_mode) == MHFU_MOVE_BACK_MODE, "MOVE layout");
static_assert(offsetof(mhfu_move_t, attack_count) == MHFU_MOVE_ATTACK_COUNT, "MOVE layout");
static_assert(offsetof(mhfu_move_t, attacks) == MHFU_MOVE_ATTACKS, "MOVE layout");
static_assert(offsetof(mhfu_move_t, spawner) == MHFU_MOVE_SPAWNER, "MOVE layout");
static_assert(offsetof(mhfu_move_t, host_attacks) == MHFU_MOVE_HOST_ATTACKS, "MOVE layout");
static_assert(MHFU_MOVE_ATTACKS_COUNT == MHFU_MOVE_MAX_ATTACKS, "MOVE layout");
static_assert(sizeof(mhfu_move_state_t) == MHFU_MOVE_STATE_SIZE, "MOVE_STATE layout");
static_assert(offsetof(mhfu_move_state_t, state) == MHFU_MOVE_STATE_STATE, "MOVE_STATE layout");
static_assert(offsetof(mhfu_move_state_t, entity) == MHFU_MOVE_STATE_ENTITY, "MOVE_STATE layout");
static_assert(offsetof(mhfu_move_state_t, end_pair) == MHFU_MOVE_STATE_END_PAIR, "MOVE_STATE layout");
static_assert(offsetof(mhfu_move_state_t, end_frame) == MHFU_MOVE_STATE_END_FRAME, "MOVE_STATE layout");
static_assert(offsetof(mhfu_move_state_t, node) == MHFU_MOVE_STATE_NODE, "MOVE_STATE layout");
static_assert(offsetof(mhfu_move_state_t, peak) == MHFU_MOVE_STATE_PEAK, "MOVE_STATE layout");
static_assert(offsetof(mhfu_move_state_t, spawn_node) == MHFU_MOVE_STATE_SPAWN_NODE, "MOVE_STATE layout");
static_assert(offsetof(mhfu_move_state_t, move) == MHFU_MOVE_STATE_MOVE, "MOVE_STATE layout");
static_assert(offsetof(mhfu_move_state_t, next) == MHFU_MOVE_STATE_NEXT, "MOVE_STATE layout");
static_assert(offsetof(mhfu_move_state_t, next_entity) == MHFU_MOVE_STATE_NEXT_ENTITY, "MOVE_STATE layout");
static_assert(offsetof(mhfu_move_state_t, node_vtable) == MHFU_MOVE_STATE_NODE_VTABLE, "MOVE_STATE layout");
static_assert(offsetof(mhfu_move_state_t, ended_frame) == MHFU_MOVE_STATE_ENDED_FRAME, "MOVE_STATE layout");
static_assert(offsetof(mhfu_move_state_t, ended_state) == MHFU_MOVE_STATE_ENDED_STATE, "MOVE_STATE layout");
static_assert(offsetof(mhfu_move_state_t, steer) == MHFU_MOVE_STATE_STEER, "MOVE_STATE layout");
static_assert(sizeof(mhfu_steer_spec_t) == MHFU_STEER_SPEC_SIZE, "STEER_SPEC layout");
static_assert(offsetof(mhfu_steer_spec_t, key_count) == MHFU_STEER_SPEC_KEY_COUNT, "STEER_SPEC layout");
static_assert(offsetof(mhfu_steer_spec_t, stuck_main) == MHFU_STEER_SPEC_STUCK_MAIN, "STEER_SPEC layout");
static_assert(offsetof(mhfu_steer_spec_t, keys) == MHFU_STEER_SPEC_KEYS, "STEER_SPEC layout");
static_assert(sizeof(mhfu_steer_state_t) == MHFU_STEER_STATE_SIZE, "STEER_STATE layout");
static_assert(offsetof(mhfu_steer_state_t, now) == MHFU_STEER_STATE_NOW, "STEER_STATE layout");
static_assert(offsetof(mhfu_steer_state_t, base) == MHFU_STEER_STATE_BASE, "STEER_STATE layout");
static_assert(offsetof(mhfu_steer_state_t, yaw0) == MHFU_STEER_STATE_YAW0, "STEER_STATE layout");

/* --- the engine ------------------------------------------------------------------------- */

#ifndef MHFU_HOST
typedef void     (*enter_fn)(uint32_t, uint32_t, uint32_t, uint32_t);
typedef void     (*execute_fn)(uint32_t, uint32_t, uint32_t, uint32_t);
typedef uint32_t (*spawn_fn)(uint32_t, uint32_t, uint32_t);

static void enter(uint32_t ent, uint32_t main_state, uint32_t sub_state, uint32_t mode)
{
    ((enter_fn)MHFU_ENTER_ACTION)(ent, main_state, sub_state, mode);
}
/* a2 a3 as the hub handlers pass them */
static void execute(uint32_t ent, uint32_t entry)
{
    ((execute_fn)MHFU_ACTION_EXECUTOR)(ent, entry, 0, 0);
}
/* natives pass *COLLISION_WORLD_PTR as the unused first argument */
static uint32_t spawn(uint32_t spawner, uint32_t ent, uint32_t id)
{
    return ((spawn_fn)spawner)(mhfu_mem_read_u32(MHFU_COLLISION_WORLD_PTR), ent, id);
}
typedef void (*node_fn)(uint32_t);
static void end_node(uint32_t node, uint32_t vtable)
{
    ((node_fn)mhfu_mem_read_u32(vtable + MHFU_ATTACK_NODE_VTABLE_END))(node);
}
#else
extern "C" void     mhfu_host_enter(uint32_t ent, uint32_t m, uint32_t s, uint32_t mode);
extern "C" void     mhfu_host_execute(uint32_t ent, uint32_t entry);
extern "C" uint32_t mhfu_host_spawn(uint32_t spawner, uint32_t ent, uint32_t id);
extern "C" void     mhfu_host_end(uint32_t node, uint32_t vtable);
static void     enter(uint32_t e, uint32_t m, uint32_t s, uint32_t mode) { mhfu_host_enter(e, m, s, mode); }
static void     execute(uint32_t e, uint32_t entry) { mhfu_host_execute(e, entry); }
static uint32_t spawn(uint32_t sp, uint32_t e, uint32_t id) { return mhfu_host_spawn(sp, e, id); }
static void     end_node(uint32_t node, uint32_t vtable) { mhfu_host_end(node, vtable); }
extern "C" void *mhfu_host_at(uint32_t addr);
#endif

/* game memory a steer may read and write directly */
static void *direct(uint32_t addr)
{
#ifndef MHFU_HOST
    return (void *)addr;
#else
    return mhfu_host_at(addr);
#endif
}

static volatile mhfu_move_state_t *S;

static uint32_t pair_of(uint32_t ent)
{
    return ((uint32_t)mhfu_mem_read_u8(ent + MHFU_ENTITY_MAIN_STATE) << 8)
         | mhfu_mem_read_u8(ent + MHFU_ENTITY_SUB_STATE);
}
static uint32_t clip_block(uint32_t ent, int part)
{
    return ent + MHFU_ENTITY_CLIP_BLOCKS + (uint32_t)part * MHFU_CLIP_BLOCK_SIZE;
}
static uint32_t carrier(void)
{
    return ((uint32_t)S->move.carrier_main << 8) | S->move.carrier_sub;
}

/* YAW along the clip's turn and the spec's mode; the wall result (mhfu/steer.h). Part 0 holds
 * the root, whose motion the turn's keys go with. */
static int steer(uint32_t ent)
{
    uint32_t b = clip_block(ent, 0);
    void *e = direct(ent);
    if (!e) return MHFU_STEER_GO;
    const mhfu_vec3_t *hunter =
        (const mhfu_vec3_t *)direct(MHFU_PLAYER_ENTITY + MHFU_ENTITY_POSITION);
    return mhfu_steer_move(e, hunter, &S->steer, S->frames,
                           mhfu_mem_read_f32(b + MHFU_CLIP_BLOCK_PHASE),
                           mhfu_mem_read_f32(b + MHFU_CLIP_BLOCK_LOOP_START),
                           mhfu_mem_read_f32(b + MHFU_CLIP_BLOCK_END));
}

static uint32_t step(uint32_t ent);

static void step_out(void)
{
    mhfu_em_step(0);
    if (S->pending) mhfu_em_step(step);   /* a play() that raced in keeps its step */
}

/* attack i's node, if it is still ours and live, through its end state */
static void end_attack(int i, uint32_t ent)
{
    uint32_t node = S->spawn_node[i];
    if (!node || S->ended_frame[i] != NEVER) return;
    S->ended_frame[i] = S->frames;
    uint8_t state = 0xFF;
    int reused = 0;
    for (int j = 0; j < S->move.attack_count; j++)
        if (j != i && S->spawn_node[j] == node && S->spawn_frame[j] != NEVER
            && S->spawn_frame[j] > S->spawn_frame[i])
            reused = 1;
    if (!reused && mhfu_mem_read_u32(node) == S->node_vtable[i]
        && mhfu_mem_read_u32(node + MHFU_ATTACK_NODE_OWNER) == ent)
        state = mhfu_mem_read_u8(node + MHFU_ATTACK_NODE_STATE);
    S->ended_state[i] = state;
    if (state != 0 && state != 0xFF) end_node(node, S->node_vtable[i]);
}

static void end_attacks(uint32_t ent)
{
    for (int i = 0; i < S->move.attack_count; i++)
        if (S->move.attacks[i].end) end_attack(i, ent);
}

static void finish(int why, uint32_t pair)
{
    end_attacks(S->entity);
    mhfu_em_mute_events(0);
    S->end = (uint8_t)why;
    S->end_pair = (uint16_t)pair;
    S->end_frame = S->frames;
    S->skipping = 0;
    S->state = MHFU_MOVE_DONE;
}

static void start(uint32_t ent)
{
    volatile uint32_t *to = (volatile uint32_t *)&S->move;
    volatile uint32_t *from = (volatile uint32_t *)&S->next;
    for (unsigned k = 0; k < sizeof(mhfu_move_t) / 4; k++) to[k] = from[k];
    S->pending = 0;
    if (S->move.part >= MHFU_ENTITY_CLIP_BLOCKS_COUNT) S->move.part = 0;
    if (S->move.attack_count > MHFU_MOVE_MAX_ATTACKS) S->move.attack_count = MHFU_MOVE_MAX_ATTACKS;
    S->entity = ent;
    S->started++;
    S->frames = S->skipped = S->end_frame = 0;
    S->end = S->skipping = 0;
    S->end_pair = 0;
    for (int k = 0; k < MHFU_ENTITY_CLIP_BLOCKS_COUNT; k++) {
        S->node[k] = 0;
        S->clip_end[k] = S->peak[k] = -1.0f;
    }
    for (int i = 0; i < MHFU_MOVE_MAX_ATTACKS; i++) {
        S->spawn_frame[i] = S->ended_frame[i] = NEVER;
        S->spawn_cursor[i] = -1.0f;
        S->spawn_node[i] = S->node_vtable[i] = 0;
        S->ended_state[i] = 0;
    }
    enter(ent, S->move.carrier_main, S->move.carrier_sub, 0);
    uint32_t now = pair_of(ent);
    if (now != carrier()) {
        finish(MHFU_MOVE_END_REFUSED, now);
        return;
    }
    S->state = MHFU_MOVE_ENTERING;
    if (!S->move.host_attacks) mhfu_em_mute_events(ent);
}

/* the host step ran the carrier's phase 0 last frame; now the clip goes in */
static void dispatch(uint32_t ent)
{
    uint32_t now = pair_of(ent);
    if (now != carrier()) { finish(MHFU_MOVE_END_PAIR, now); return; }
    execute(ent, S->move.entry);
    for (int k = 0; k < MHFU_ENTITY_CLIP_BLOCKS_COUNT; k++) {
        S->node[k] = mhfu_mem_read_u32(clip_block(ent, k) + MHFU_CLIP_BLOCK_NODE);
        S->clip_end[k] = mhfu_mem_read_f32(clip_block(ent, k) + MHFU_CLIP_BLOCK_END);
    }
    S->state = MHFU_MOVE_PLAYING;
}

static int crosses(float prev, float cursor, uint16_t frame)
{
    float f = (float)frame;
    return prev < f && f <= cursor;
}

static void attacks(uint32_t ent, float prev, float cursor)
{
    for (int i = 0; i < S->move.attack_count; i++) {
        const mhfu_move_attack_t *at = (const mhfu_move_attack_t *)&S->move.attacks[i];
        if (S->spawn_frame[i] == NEVER && crosses(prev, cursor, at->frame)) {
            uint32_t spawner = S->move.spawner ? S->move.spawner : MHFU_TIGREX_ATTACK_SPAWN;
            uint32_t node = spawn(spawner, ent, at->id);
            S->spawn_node[i] = node;
            S->node_vtable[i] = node ? mhfu_mem_read_u32(node) : 0;
            S->spawn_frame[i] = S->frames;
            S->spawn_cursor[i] = cursor;
        }
        if (at->end && S->spawn_frame[i] != NEVER && crosses(prev, cursor, at->end))
            end_attack(i, ent);
    }
}

/* 1 when the host step skips this frame */
static uint32_t play(uint32_t ent)
{
    S->frames++;
    uint32_t now = pair_of(ent);
    if (now != carrier()) { finish(MHFU_MOVE_END_PAIR, now); return 0; }
    int p = S->move.part;
    float prev = S->peak[p];
    for (int k = 0; k < MHFU_ENTITY_CLIP_BLOCKS_COUNT; k++) {
        uint32_t b = clip_block(ent, k);
        float cur = mhfu_mem_read_f32(b + MHFU_CLIP_BLOCK_PHASE);
        if (mhfu_mem_read_u32(b + MHFU_CLIP_BLOCK_NODE) == S->node[k] && cur > S->peak[k])
            S->peak[k] = cur;
    }
    uint32_t b = clip_block(ent, p);
    if (mhfu_mem_read_u32(b + MHFU_CLIP_BLOCK_NODE) != S->node[p]) {
        finish(MHFU_MOVE_END_LOST, now);
        return 0;
    }
    attacks(ent, prev, mhfu_mem_read_f32(b + MHFU_CLIP_BLOCK_PHASE));
    int wall = steer(ent);
    if (wall == MHFU_STEER_STUCK) {
        /* as the Tigrex charge ends on such a wall; the AI script waits for the hub after it */
        const volatile mhfu_steer_spec_t *sp = &S->steer.now;
        enter(ent, sp->stuck_main, sp->stuck_sub, sp->stuck_mode);
        mhfu_mem_write_u8(ent + MHFU_ENTITY_SCRIPT_WAKE, 0);
        finish(MHFU_MOVE_END_STUCK, pair_of(ent));
        return 0;
    }
    int done = !(mhfu_mem_read_u16(b + MHFU_CLIP_BLOCK_FLAGS) & 1) || wall == MHFU_STEER_WALL;
    int timed = S->move.length && S->frames >= S->move.length;
    if (done || timed) {
        if (S->move.back_main != MHFU_MOVE_NO_PAIR || timed) {
            int back = S->move.back_main != MHFU_MOVE_NO_PAIR;
            enter(ent, back ? S->move.back_main : S->move.carrier_main,
                  back ? S->move.back_sub : S->move.carrier_sub,
                  back ? S->move.back_mode : 0);
            finish(wall ? MHFU_MOVE_END_WALL : MHFU_MOVE_END_BACK, pair_of(ent));
            return 0;
        }
        /* the host step runs the carrier's last phase now; AFTER reads what it picked */
        end_attacks(ent);
        mhfu_em_mute_events(0);
        S->end = wall ? MHFU_MOVE_END_WALL : MHFU_MOVE_END_CLIP;
        S->end_frame = S->frames;
        S->skipping = 0;
        S->state = MHFU_MOVE_AFTER;
        return 0;
    }
    if (!S->move.skip || mhfu_mem_read_u8(ent + MHFU_ENTITY_HIT_PENDING)) {
        S->skipping = 0;
        return 0;
    }
    S->skipping = 1;
    S->skipped++;
    return 1;
}

static uint32_t step(uint32_t ent)
{
    uint32_t skip = 0;
    if (S->pending == STOP) {
        S->pending = 0;
        if (S->state == MHFU_MOVE_ENTERING || S->state == MHFU_MOVE_PLAYING)
            finish(MHFU_MOVE_END_STOPPED, pair_of(S->entity));
    } else if (S->pending && ent == S->next_entity) {
        if (S->state == MHFU_MOVE_ENTERING || S->state == MHFU_MOVE_PLAYING)
            finish(MHFU_MOVE_END_REPLACED, pair_of(S->entity));
        start(ent);
        return 0;
    }
    if (ent == S->entity) {
        switch (S->state) {
        case MHFU_MOVE_ENTERING: dispatch(ent); break;
        case MHFU_MOVE_PLAYING:  skip = play(ent); break;
        case MHFU_MOVE_AFTER:
            S->end_pair = (uint16_t)pair_of(ent);
            S->state = MHFU_MOVE_DONE;
            break;
        default: break;
        }
    }
    if (S->state == MHFU_MOVE_DONE || S->state == MHFU_MOVE_IDLE) step_out();
    return skip;
}

/* --- public ----------------------------------------------------------------------------- */

extern "C" void mhfu_move_init_spec(mhfu_move_t *mv, uint16_t entry)
{
    uint8_t *b = (uint8_t *)mv;
    for (unsigned k = 0; k < sizeof(*mv); k++) b[k] = 0;
    mv->entry = entry;
    mv->carrier_main = 0;
    mv->carrier_sub = 2;
    mv->back_main = MHFU_MOVE_NO_PAIR;
}

extern "C" int mhfu_move_play(uint32_t entity, const mhfu_move_t *mv)
{
    if (!S || !mv || !entity || !mhfu_em_installed()) return 0;
    S->pending = 0;
    volatile uint32_t *to = (volatile uint32_t *)&S->next;
    const uint32_t *from = (const uint32_t *)mv;
    for (unsigned k = 0; k < sizeof(mhfu_move_t) / 4; k++) to[k] = from[k];
    S->next_entity = entity;
    S->pending = 1;
    mhfu_em_step(step);
    return 1;
}

extern "C" void mhfu_move_stop(void)
{
    if (!S || !mhfu_em_installed()) return;
    S->pending = STOP;
    mhfu_em_step(step);
}

extern "C" void mhfu_move_steer(const mhfu_steer_spec_t *s)
{
    if (!S || !s) return;
    volatile uint32_t *to = (volatile uint32_t *)&S->steer.next;
    const uint32_t *from = (const uint32_t *)s;
    for (unsigned k = 0; k < sizeof(mhfu_steer_spec_t) / 4; k++) to[k] = from[k];
}

extern "C" const volatile mhfu_move_state_t *mhfu_move_state(void) { return S; }

static void on_quest(const mhfu_event_ctx_t *ctx)
{
    (void)ctx;
    /* em_vhook drops the step with the wrapped vtable; nothing carries over */
    S->pending = 0;
    S->state = MHFU_MOVE_IDLE;
    S->entity = S->next_entity = 0;
}

#ifndef MHFU_HOST
extern "C" int mhfu_move_init(void)
{
    if (S) return 0;
    /* Low, as em_vhook's: High lands in the extra-RAM window */
    SceUID blk = sceKernelAllocPartitionMemory(2, OWNER, PSP_SMEM_Low,
                                               sizeof(mhfu_move_state_t) + 64, 0);
    if (blk < 0) {
        mhfu_log("[%s] partition alloc FAILED (%d)", OWNER, (int)blk);
        return -1;
    }
    uintptr_t at = ((uintptr_t)sceKernelGetBlockHeadAddr(blk) + 63) & ~(uintptr_t)63;
    S = (volatile mhfu_move_state_t *)at;
    volatile uint8_t *b = (volatile uint8_t *)S;
    for (unsigned k = 0; k < sizeof(mhfu_move_state_t); k++) b[k] = 0;
    S->magic = MAGIC;
    mhfu_steer_init_spec((mhfu_steer_spec_t *)&S->steer.next);
    if (mhfu_on_quest_beginning(on_quest, 0, OWNER) != MHFU_HOOK_OK)
        mhfu_log("[%s] quest event registration failed", OWNER);
    mhfu_log("[%s] block @0x%08X", OWNER, (unsigned)at);
    return 0;
}
#else
static mhfu_move_state_t g_host_block;
extern "C" int mhfu_move_init(void)
{
    S = &g_host_block;
    volatile uint8_t *b = (volatile uint8_t *)S;
    for (unsigned k = 0; k < sizeof(mhfu_move_state_t); k++) b[k] = 0;
    S->magic = MAGIC;
    mhfu_steer_init_spec((mhfu_steer_spec_t *)&S->steer.next);
    return 0;
}
/* host tests drive the step directly and reset between cases */
extern "C" uint32_t mhfu_move_host_step(uint32_t ent) { return step(ent); }
extern "C" void mhfu_move_host_quest(void) { on_quest(0); }
#endif
