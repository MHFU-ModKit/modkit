/*
 * em_vhook: takes over a big monster's AI by wrapping two slots of its species
 * vtable. A species' AI is an overlay reached through a vtable in the EBOOT; the
 * slots are writable and re-read on every dispatch, and a wrapper that tail-calls
 * the original is indistinguishable from the original. Only a word is written.
 *
 *   slot 29, MONSTER_VTABLE.AI_STEP       the per-frame AI step, ~30.8/s
 *   slot 32, MONSTER_VTABLE.ENTER_ACTION  enters a behaviour pair:
 *                                         (entity, main, id, mode)
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
 *   REQUEST       slot-29 pre: a pair Lua wants entered now, issued on the game
 *                 thread in the next AI frame through the engine's dispatcher
 *                 (MHFU_ENTER_ACTION), so it is provisioned too.
 *   RULES         slot-29 pre: a native 30 Hz brain: "in pair P for N frames,
 *                 player at [lo,hi), receding -> enter Q", cooldown, budget.
 *   BUDGET        slot-32 post + slot-29 one-shot: the ENTITY.ACTION_BUDGET
 *                 override; the stubs carry it, but nothing arms it.
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
 * thread stacks inside the PRX image, so a hook's C-call frame clobbers it. Every
 * decision is a MOVN/MOVZ select; the slot-29 stub's one call is a jalr whose
 * target is selected (the dispatcher or a `jr ra` in our block) with ra and the
 * step's arguments spilled to config words. The slot-32 stub keeps a 16-byte
 * hand-written frame (see build_act_stub).
 *
 * A slot's code is valid only while that species' overlay is resident, so we latch
 * onto the vtable of a big monster the engine spawned and restore on quest exit,
 * dropping every rule, substitution and request with it. One config block per
 * latched vtable, not per entity: two live monsters of that species would share
 * the dwell counter and the distance.
 */
#include "mhfu/mhfu.h"
#include "stubs.h"
#include <pspsysmem.h>
#include <stddef.h>

#define MOD_ID "em_vhook"

/* The config block, field for field the layout stubs.h indexes by offset. */
typedef struct {
    uint8_t  from_mask, from_sub, to_main, to_sub;
    uint32_t left;
} cfg_sub_t;
typedef struct {
    uint8_t  from_mask, from_sub, to_main, to_sub, mode, flags, _pad0, _pad1;
    uint32_t min_frames, d2_lo, d2_hi, left, fired, last_fire, cooldown, _pad2;
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
    cfg_rule_t rules[MHFU_EM_RULES];               /* +0xB8 */
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
static_assert(offsetof(cfg_rule_t, min_frames)      == RULE_MIN_FRAMES, "cfg layout");
static_assert(offsetof(cfg_rule_t, cooldown)        == RULE_COOLDOWN,   "cfg layout");
static_assert(sizeof(em_vhook_cfg_t)                == CFG_SIZE,        "cfg layout");

#define CFG_CANARY_VAL 0x5645484Bu   /* 'VEHK' */

/* The stubs and the config live outside the PRX image: the engine parks thread
 * stacks inside it, and stubs near its top were overwritten under heavy activity.
 * They come from the user partition, and only pointers live here. */
#define RET_INSNS   4     /* jr ra; nop, padded to 16 bytes */
#define BLOCK_BYTES ((STUB_AI_INSNS + STUB_ACT_INSNS + RET_INSNS) * 4 + CFG_SIZE + 128)

static uint32_t *g_stub_ai;
static uint32_t *g_stub_act;
static uint32_t *g_stub_ret;
static em_vhook_cfg_t *g_cfgp;
static SceUID g_block = -1;

static void cfg_reset_live(void)
{
    /* everything the stubs derive from the running monster; (0,0) packs to 0, so
     * a zeroed history would read as two ticks in (0,0) and fire the one-shot */
    g_cfgp->prev_pair = g_cfgp->prev2_pair = 0xFFFFFFFFu;
    g_cfgp->frames = 0;
    g_cfgp->d2 = g_cfgp->d2_prev = 0;
    g_cfgp->sub_pending = g_cfgp->sub_to_pending = 0;
}

static int alloc_block(void)
{
    if (g_block >= 0) return 0;
    /* Low, not High: High lands in PPSSPP's extra-RAM window, and a stub there
     * exits the emulator once its store retargets into normal RAM. */
    g_block = sceKernelAllocPartitionMemory(2, "em_vhook", PSP_SMEM_Low,
                                            BLOCK_BYTES, 0);
    if (g_block < 0) {
        mhfu_log("[%s] partition alloc FAILED (%d) - refusing to install",
                 MOD_ID, (int)g_block);
        return -1;
    }
    uint8_t *base = (uint8_t *)sceKernelGetBlockHeadAddr(g_block);
    if ((uintptr_t)base >= MHFU_USER_RAM_END) {
        mhfu_log("[%s] block at 0x%08X is in the extra-RAM window - refusing",
                 MOD_ID, (unsigned)(uintptr_t)base);
        sceKernelFreePartitionMemory(g_block);
        g_block = -1;
        return -1;
    }
    base = (uint8_t *)(((uintptr_t)base + 63) & ~(uintptr_t)63);
    g_stub_ai  = (uint32_t *)base;
    g_stub_act = (uint32_t *)(base + STUB_AI_INSNS * 4);
    g_stub_ret = (uint32_t *)(base + (STUB_AI_INSNS + STUB_ACT_INSNS) * 4);
    g_cfgp     = (em_vhook_cfg_t *)(base + (STUB_AI_INSNS + STUB_ACT_INSNS + RET_INSNS) * 4);
    for (unsigned k = 0; k < sizeof(*g_cfgp) / 4; k++)
        ((uint32_t *)g_cfgp)[k] = 0;
    cfg_reset_live();
    mhfu_log("[%s] block @0x%08X (outside the PRX image), cfg @0x%08X",
             MOD_ID, (unsigned)(uintptr_t)base, (unsigned)(uintptr_t)g_cfgp);
    return 0;
}

static uint32_t g_vtable;            /* the species vtable we latched onto */
static uint32_t g_orig_ai;
static uint32_t g_orig_act;
static int      g_installed;

/* --- slot 29: count, request, rules, one-shot budget, then tail-call. ------
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
 * The request and the rules run first, so a pair they enter gets its phase-0 tick
 * in the step that follows and the one-shot lands like an engine entry's.
 *
 * Frame-free because it runs at ~30 Hz inside the engine's stacks: its one jalr
 * spills ra and the step's arguments to config words. Not reentrant, and need not
 * be: the step is dispatched once per frame from one site.
 */
static void build_ai_stub(uint32_t original)
{
    uint32_t cfg = (uint32_t)(uintptr_t)g_cfgp;
    uint32_t ret = (uint32_t)(uintptr_t)g_stub_ret;
    int overflow = 0;
    int n = emv_build_ai_stub(g_stub_ai, STUB_AI_INSNS, cfg, original, ret, &overflow);
    if (overflow) {
        mhfu_log("[%s] ai stub is %d insns > STUB_AI_INSNS %d - installing a plain "
                 "trampoline instead", MOD_ID, n, STUB_AI_INSNS);
        g_stub_ai[0] = mips_j(original);
        g_stub_ai[1] = MIPS_NOP;
        return;
    }
    for (int k = n; k < STUB_AI_INSNS; k++) g_stub_ai[k] = MIPS_NOP;
    mhfu_log("[%s] ai stub: request + %d rules + one-shot budget, %d insns, frame-free",
             MOD_ID, MHFU_EM_RULES, n);
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
                 "trampoline instead", MOD_ID, n, STUB_ACT_INSNS);
        g_stub_act[0] = mips_j(original);
        g_stub_act[1] = MIPS_NOP;
        return;
    }
    for (int k = n; k < STUB_ACT_INSNS; k++) g_stub_act[k] = MIPS_NOP;
    mhfu_log("[%s] act stub: %d-entry substitution PRE, original, budget POST; "
             "%d insns, frame 0x%X", MOD_ID, MHFU_EM_SUBS, n, ACT_FRAME);
}

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
                 MOD_ID, slot, from_mask,
                 from_sub == MHFU_EM_SUB_ANY ? "any" : "exact", to_main, to_sub,
                 count == MHFU_EM_UNLIMITED ? "standing" : "n");
    else
        mhfu_log("[%s] substitute[%d]: cleared", MOD_ID, slot);
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
 * as raw f32 bits, which is what the stub compares. */
static uint32_t f32_bits(float f) { union { float f; uint32_t u; } v; v.f = f; return v.u; }

extern "C" void mhfu_em_rule(int slot, const mhfu_em_rule_t *r)
{
    if (!g_cfgp || slot < 0 || slot >= MHFU_EM_RULES) return;
    cfg_rule_t *c = &g_cfgp->rules[slot];
    c->left = 0;                                    /* off while we write */
    if (!r || r->count == 0 || r->from_mask == 0) {
        c->from_mask = 0;
        mhfu_log("[%s] rule[%d]: cleared", MOD_ID, slot);
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
    c->min_frames = r->min_frames;
    c->d2_lo      = f32_bits(lo * lo);
    c->d2_hi      = f32_bits(hi * hi);
    c->cooldown   = r->cooldown;
    c->fired      = 0;
    c->last_fire  = 0;
    c->left       = r->count;
    mhfu_log("[%s] rule[%d]: main mask 0x%02X sub %s, >=%u frames, d in [%d,%d)%s%s "
             "-> enter (%u,%u,m%u), cooldown %u, x%s",
             MOD_ID, slot, r->from_mask,
             r->from_sub == MHFU_EM_SUB_ANY ? "any" : "exact",
             (unsigned)r->min_frames, (int)lo, (int)hi,
             (r->flags & MHFU_EM_RULE_RECEDING) ? ", receding" : "",
             (r->flags & MHFU_EM_RULE_CLOSING)  ? ", closing"  : "",
             r->to_main, r->to_sub, r->mode, (unsigned)r->cooldown,
             r->count == MHFU_EM_UNLIMITED ? "standing" : "n");
}

extern "C" void mhfu_em_clear(void)
{
    if (!g_cfgp) return;
    g_cfgp->req_pending = 0;
    for (int i = 0; i < MHFU_EM_SUBS; i++)  { g_cfgp->subs[i].left = 0; g_cfgp->subs[i].from_mask = 0; }
    for (int i = 0; i < MHFU_EM_RULES; i++) { g_cfgp->rules[i].left = 0; g_cfgp->rules[i].from_mask = 0; }
}

static float bits_f32(uint32_t u) { union { float f; uint32_t u; } v; v.u = u; return v.f; }
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
    out->req_pending = g_cfgp->req_pending;
    out->req_done    = g_cfgp->req_done;
    out->req_result  = g_cfgp->req_result;
    out->ring_idx    = g_cfgp->ring_idx;
    for (int i = 0; i < MHFU_EM_RING; i++)  out->ring[i] = g_cfgp->ring[i];
    for (int i = 0; i < MHFU_EM_RULES; i++) { out->rule_fired[i] = g_cfgp->rules[i].fired;
                                               out->rule_left[i]  = g_cfgp->rules[i].left; }
    for (int i = 0; i < MHFU_EM_SUBS; i++)  out->sub_left[i] = g_cfgp->subs[i].left;
}

/* ------------------------------------------------------------ latch */

static void install_for(uint32_t entity)
{
    if (g_installed || !entity) return;
    if (alloc_block() < 0) return;
    uint32_t vt = mhfu_mem_read_u32(entity);
    /* every species entity vtable lies in this band: a corrupt pointer never
     * makes us write elsewhere */
    if (vt < MHFU_VTABLE_BAND || vt >= MHFU_VTABLE_BAND_END) {
        mhfu_log("[%s] entity 0x%08X vtable 0x%08X outside the species band "
                 "- not a big monster, skipping", MOD_ID,
                 (unsigned)entity, (unsigned)vt);
        return;
    }
    g_vtable  = vt;
    g_orig_ai  = mhfu_mem_read_u32(vt + MHFU_MONSTER_VTABLE_AI_STEP);
    g_orig_act = mhfu_mem_read_u32(vt + MHFU_MONSTER_VTABLE_ENTER_ACTION);

    cfg_reset_live();
    emv_build_ret_stub(g_stub_ret);
    g_stub_ret[2] = MIPS_NOP; g_stub_ret[3] = MIPS_NOP;
    build_ai_stub(g_orig_ai);
    build_act_stub(g_orig_act);
    mhfu_hook_flush_caches();

    /* data writes, not code patches: they take outside the JIT-cold window */
    mhfu_mem_write_u32(vt + MHFU_MONSTER_VTABLE_AI_STEP,      (uint32_t)(uintptr_t)g_stub_ai);
    mhfu_mem_write_u32(vt + MHFU_MONSTER_VTABLE_ENTER_ACTION, (uint32_t)(uintptr_t)g_stub_act);
    g_installed = 1;

    mhfu_log("[%s] vtable 0x%08X: slot29 0x%08X -> 0x%08X, slot32 0x%08X -> 0x%08X",
             MOD_ID, (unsigned)vt, (unsigned)g_orig_ai,
             (unsigned)(uintptr_t)g_stub_ai, (unsigned)g_orig_act,
             (unsigned)(uintptr_t)g_stub_act);
}

static void uninstall(void)
{
    if (!g_installed) return;
    mhfu_mem_write_u32(g_vtable + MHFU_MONSTER_VTABLE_AI_STEP,      g_orig_ai);
    mhfu_mem_write_u32(g_vtable + MHFU_MONSTER_VTABLE_ENTER_ACTION, g_orig_act);
    g_installed = 0;
    mhfu_em_clear();
    mhfu_log("[%s] restored vtable 0x%08X (ai_ticks=%u act_enters=%u sub %u/%u "
             "req %u brain %u)", MOD_ID, (unsigned)g_vtable,
             (unsigned)g_cfgp->ai_ticks, (unsigned)g_cfgp->act_enters,
             (unsigned)g_cfgp->sub_hits, (unsigned)g_cfgp->sub_landed,
             (unsigned)g_cfgp->req_done, (unsigned)g_cfgp->brain_fires);
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
    g_cfgp->req_done = 0;
}

static int em_vhook_init(void)
{
    if (alloc_block() < 0) return -1;
    g_cfgp->patch_off = MHFU_ENTITY_ACTION_BUDGET;
    g_cfgp->patch_val = 900;
    g_cfgp->want_main = 0xFF;      /* matches nothing: nothing arms the budget override */
    g_cfgp->want_sub  = 0xFF;
    g_cfgp->canary    = CFG_CANARY_VAL;
    mhfu_on_monster_spawned(on_spawn);
    mhfu_on_quest_beginning(on_quest);
    mhfu_log("[%s] v3.0 ready; cfg @0x%08X, stubs @0x%08X / 0x%08X", MOD_ID,
             (unsigned)(uintptr_t)g_cfgp,
             (unsigned)(uintptr_t)g_stub_ai, (unsigned)(uintptr_t)g_stub_act);
    return 0;
}

static void em_vhook_shutdown(void) { uninstall(); }

MHFU_MOD(.id = MOD_ID, .version = "3.0",
         .needs = 0, .conflicts = 0,
         .init = em_vhook_init, .shutdown = em_vhook_shutdown);
