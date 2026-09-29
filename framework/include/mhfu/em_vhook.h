/* The em_vhook mod's surface: it wraps two slots of the spawned big monster's species vtable,
 * MONSTER_VTABLE.ENTER_ACTION (provisions a behaviour pair) and .AI_STEP (every frame).
 *
 *   substitution  the engine's (main, id) is rewritten before enter-action runs, so our
 *                 pair is provisioned like a native one;
 *   request       a pair entered on the game thread in the next AI frame, through
 *                 MHFU_ENTER_ACTION;
 *   rules         a 30 Hz brain: "in pair P for >= N frames, player distance in [lo, hi),
 *                 receding or closing -> enter (main, sub, mode)", with cooldown and budget.
 *
 * Every call only writes the config block the stubs read, so it is safe from any thread,
 * and a no-op returning 0 while no vtable is wrapped. */
#ifndef MHFU_EM_VHOOK_H
#define MHFU_EM_VHOOK_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define MHFU_EM_SUBS   4    /* substitution entries */
#define MHFU_EM_RULES  4    /* brain rules */
#define MHFU_EM_RING   8    /* last enter-action dispatches kept */

#define MHFU_EM_SUB_ANY   0xFEu        /* any sub state; 0xFF is the never-matching idle value */
#define MHFU_EM_UNLIMITED 0xFFFFFFFFu  /* a fire budget that never runs out */

/* mhfu_em_rule_t.flags */
#define MHFU_EM_RULE_RECEDING 0x01   /* only while the distance grows */
#define MHFU_EM_RULE_CLOSING  0x02   /* only while the distance shrinks */

typedef struct {
    uint8_t  from_mask;    /* bit k set: may fire from main state k */
    uint8_t  from_sub;     /* exact sub, or MHFU_EM_SUB_ANY */
    uint8_t  to_main, to_sub, mode;
    uint8_t  flags;        /* MHFU_EM_RULE_* */
    uint32_t min_frames;   /* the pair must have stood this long */
    float    dist_lo, dist_hi;   /* player XZ distance window [lo, hi) */
    uint32_t cooldown;     /* frames between two fires */
    uint32_t count;        /* fires allowed; MHFU_EM_UNLIMITED for a standing rule */
} mhfu_em_rule_t;

typedef struct {
    uint32_t installed;
    uint32_t ai_ticks, act_enters, last_pair, frames;
    float    dist;               /* player XZ distance at the last measurement */
    uint32_t sub_hits, sub_landed, sub_last_in;
    uint32_t brain_fires;
    uint32_t req_pending, req_done, req_result;
    uint32_t ring_idx;
    uint32_t ring[MHFU_EM_RING];  /* (subst<<24)|(mode<<16)|(main<<8)|sub, oldest at ring_idx+1 */
    uint32_t rule_fired[MHFU_EM_RULES];
    uint32_t rule_left[MHFU_EM_RULES];
    uint32_t sub_left[MHFU_EM_SUBS];
} mhfu_em_status_t;

/* 1 while a big monster's vtable is wrapped (from its spawn to the next quest_beginning). */
int  mhfu_em_installed(void);

/* Entry slot (0..MHFU_EM_SUBS-1): an enter-action whose main is in from_mask and whose id is
 * from_sub (or any) is entered as (to_main, to_sub) instead, count times; 0 clears it. */
void mhfu_em_substitute(int slot, uint8_t from_mask, uint8_t from_sub,
                        uint8_t to_main, uint8_t to_sub, uint32_t count);

/* Enter (main, sub, mode) on the next AI frame; 0 while nothing is wrapped. */
int  mhfu_em_request(uint8_t main_state, uint8_t sub_state, uint8_t mode);

/* Rule slot (0..MHFU_EM_RULES-1); NULL clears it. */
void mhfu_em_rule(int slot, const mhfu_em_rule_t *r);

/* Drop every substitution, rule and pending request. */
void mhfu_em_clear(void);

void mhfu_em_status(mhfu_em_status_t *out);

#ifdef __cplusplus
}
#endif
#endif /* MHFU_EM_VHOOK_H */
