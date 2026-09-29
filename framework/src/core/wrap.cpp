/* The wrapper builder (wrap.h). Wrappers call one shared save and one shared restore routine
 * in the cave, so each stays under 30 words. */
#include <stddef.h>
#include <string.h>

#include "wrap.h"
#include "mhfu/mips.h"

/* Encoders mips.h lacks; private names so a later mips.h addition cannot clash. */
static inline uint32_t enc_swc1(uint32_t ft, int16_t off, uint32_t base) {
    return (0x39u << 26) | ((base & 0x1Fu) << 21) | ((ft & 0x1Fu) << 16) | (uint16_t)off;
}
static inline uint32_t enc_mfhi(uint32_t rd) { return ((rd & 0x1Fu) << 11) | 0x10u; }
static inline uint32_t enc_mflo(uint32_t rd) { return ((rd & 0x1Fu) << 11) | 0x12u; }
static inline uint32_t enc_mthi(uint32_t rs) { return ((rs & 0x1Fu) << 21) | 0x11u; }
static inline uint32_t enc_mtlo(uint32_t rs) { return ((rs & 0x1Fu) << 21) | 0x13u; }
static inline uint32_t enc_cfc1(uint32_t rt, uint32_t fs) {
    return (0x11u << 26) | (0x02u << 21) | ((rt & 0x1Fu) << 16) | ((fs & 0x1Fu) << 11);
}
static inline uint32_t enc_ctc1(uint32_t rt, uint32_t fs) {
    return (0x11u << 26) | (0x06u << 21) | ((rt & 0x1Fu) << 16) | ((fs & 0x1Fu) << 11);
}

/* The frame: an argument home area (an o32 callee may spill a0-a3 there), the regs, then
 * the FPU's caller-saved registers f0-f19 and FCR31. */
#define N_FPRS 20
enum : int {
    REGS  = 0x10,
    FPRS  = REGS + (int)sizeof(mhfu_regs_t),
    FCSR  = FPRS + 4 * N_FPRS,
    FRAME = (FCSR + 4 + 15) & ~15,
};
static_assert(FRAME < 0x8000, "frame offsets are 16-bit immediates");
static_assert(FRAME % 16 == 0, "game code runs sv.q/lv.q on its stack");
#define OFF(f) ((int16_t)(REGS + offsetof(mhfu_regs_t, f)))

/* at and ra are saved by the wrapper itself: at before the save call, ra before it links. */
static const struct { uint8_t reg; int16_t off; } k_gprs[] = {
    {MIPS_REG_V0, OFF(v0)}, {MIPS_REG_V1, OFF(v1)},
    {MIPS_REG_A0, OFF(a0)}, {MIPS_REG_A1, OFF(a1)}, {MIPS_REG_A2, OFF(a2)}, {MIPS_REG_A3, OFF(a3)},
    {MIPS_REG_T0, OFF(t0)}, {MIPS_REG_T1, OFF(t1)}, {MIPS_REG_T2, OFF(t2)}, {MIPS_REG_T3, OFF(t3)},
    {MIPS_REG_T4, OFF(t4)}, {MIPS_REG_T5, OFF(t5)}, {MIPS_REG_T6, OFF(t6)}, {MIPS_REG_T7, OFF(t7)},
    {MIPS_REG_T8, OFF(t8)}, {MIPS_REG_T9, OFF(t9)},
    {MIPS_REG_S0, OFF(s0)}, {MIPS_REG_S1, OFF(s1)}, {MIPS_REG_S2, OFF(s2)}, {MIPS_REG_S3, OFF(s3)},
    {MIPS_REG_S4, OFF(s4)}, {MIPS_REG_S5, OFF(s5)}, {MIPS_REG_S6, OFF(s6)}, {MIPS_REG_S7, OFF(s7)},
};
#define N_GPRS ((int)(sizeof(k_gprs) / sizeof(k_gprs[0])))

/* save: the GPRs, HI/LO, the caller's sp, FCR31, f0-f19, return; restore: FCR31, f0-f19,
 * HI/LO, at and the GPRs, return */
static const int SAVE_WORDS    = N_GPRS + 4 + 2 + 2 + N_FPRS + 2;
static const int RESTORE_WORDS = 2 + N_FPRS + 4 + 1 + N_GPRS + 1;

typedef struct {
    uint32_t *out;
    int       cap, n;
    uint32_t  base;
    int       ok;
} emitter_t;

static void put(emitter_t *e, uint32_t word)
{
    if (e->n < e->cap) e->out[e->n] = word;
    else e->ok = 0;
    e->n++;
}

/* j and jal keep the top 4 bits of the delay slot's address */
static int reachable(const emitter_t *e, uint32_t target)
{
    uint32_t slot = e->base + 4u * (uint32_t)e->n + 4u;
    return !(target & 3u) && ((slot ^ target) & 0xF0000000u) == 0;
}
static void put_jal(emitter_t *e, uint32_t target)
{
    if (!reachable(e, target)) e->ok = 0;
    put(e, mips_jal(target));
}
static void put_j(emitter_t *e, uint32_t target)
{
    if (!reachable(e, target)) e->ok = 0;
    put(e, mips_j(target));
}

extern "C" int mhfu_wrap_emit_common(uint32_t *out, int cap, uint32_t base)
{
    if (!out) return 0;
    emitter_t e = {out, cap, 0, base, 1};

    /* save, entered by jal with the frame pushed and at and ra stored */
    for (int i = 0; i < N_GPRS; i++)
        put(&e, mips_sw(k_gprs[i].reg, k_gprs[i].off, MIPS_REG_SP));
    put(&e, enc_mfhi(MIPS_REG_T0));
    put(&e, mips_sw(MIPS_REG_T0, OFF(hi), MIPS_REG_SP));
    put(&e, enc_mflo(MIPS_REG_T0));
    put(&e, mips_sw(MIPS_REG_T0, OFF(lo), MIPS_REG_SP));
    put(&e, mips_addiu(MIPS_REG_T0, MIPS_REG_SP, FRAME));
    put(&e, mips_sw(MIPS_REG_T0, OFF(sp), MIPS_REG_SP));
    put(&e, enc_cfc1(MIPS_REG_T0, 31));
    put(&e, mips_sw(MIPS_REG_T0, FCSR, MIPS_REG_SP));
    for (int f = 0; f < N_FPRS; f++)
        put(&e, enc_swc1(f, (int16_t)(FPRS + 4 * f), MIPS_REG_SP));
    put(&e, mips_jr(MIPS_REG_RA));
    put(&e, MIPS_NOP);
    if (e.n != SAVE_WORDS) return 0;

    /* restore: everything but ra and sp, which the wrapper still needs */
    put(&e, mips_lw(MIPS_REG_T0, FCSR, MIPS_REG_SP));
    put(&e, enc_ctc1(MIPS_REG_T0, 31));
    for (int f = 0; f < N_FPRS; f++)
        put(&e, mips_lwc1(f, (int16_t)(FPRS + 4 * f), MIPS_REG_SP));
    put(&e, mips_lw(MIPS_REG_T0, OFF(hi), MIPS_REG_SP));
    put(&e, enc_mthi(MIPS_REG_T0));
    put(&e, mips_lw(MIPS_REG_T0, OFF(lo), MIPS_REG_SP));
    put(&e, enc_mtlo(MIPS_REG_T0));
    put(&e, mips_lw(MIPS_REG_AT, OFF(at), MIPS_REG_SP));
    for (int i = 0; i < N_GPRS; i++)
        if (k_gprs[i].reg != MIPS_REG_T0)
            put(&e, mips_lw(k_gprs[i].reg, k_gprs[i].off, MIPS_REG_SP));
    put(&e, mips_jr(MIPS_REG_RA));
    put(&e, mips_lw(MIPS_REG_T0, OFF(t0), MIPS_REG_SP));
    return e.ok && e.n == SAVE_WORDS + RESTORE_WORDS ? e.n : 0;
}

extern "C" int mhfu_wrap_emit(uint32_t *out, int cap, uint32_t base, uint32_t common,
                              const mhfu_wrap_t *w)
{
    if (!out || !w || !common || w->n_tail < 0 || w->n_tail > 2) return 0;
    uint32_t pre     = (uint32_t)(uintptr_t)w->pre;
    uint32_t post    = (uint32_t)(uintptr_t)w->post;
    uint32_t restore = common + 4u * SAVE_WORDS;
    emitter_t e = {out, cap, 0, base, 1};

    put(&e, mips_addiu(MIPS_REG_SP, MIPS_REG_SP, -FRAME));
    put(&e, mips_sw(MIPS_REG_RA, OFF(ra), MIPS_REG_SP));
    put_jal(&e, common);
    put(&e, mips_sw(MIPS_REG_AT, OFF(at), MIPS_REG_SP));
    put(&e, mips_lui(MIPS_REG_T0, (uint16_t)(w->pc >> 16)));
    put(&e, mips_ori(MIPS_REG_T0, MIPS_REG_T0, (uint16_t)w->pc));
    put(&e, mips_sw(MIPS_REG_T0, OFF(pc), MIPS_REG_SP));

    if (pre) {
        put_jal(&e, pre);
        put(&e, mips_addiu(MIPS_REG_A0, MIPS_REG_SP, REGS));
    }
    if (w->call) {
        /* the callee sees every register the caller left, with pre's edits, float
         * arguments included; its v0, v1 and f0 are the results */
        put_jal(&e, restore);
        put(&e, MIPS_NOP);
        put_jal(&e, w->call);
        put(&e, MIPS_NOP);
        put(&e, mips_sw(MIPS_REG_V0, OFF(v0), MIPS_REG_SP));
        put(&e, mips_sw(MIPS_REG_V1, OFF(v1), MIPS_REG_SP));
        put(&e, enc_swc1(0, FPRS, MIPS_REG_SP));
    }
    if (post) {
        put_jal(&e, post);
        put(&e, mips_addiu(MIPS_REG_A0, MIPS_REG_SP, REGS));
    }

    put_jal(&e, restore);
    put(&e, MIPS_NOP);
    put(&e, mips_lw(MIPS_REG_RA, OFF(ra), MIPS_REG_SP));
    put(&e, mips_addiu(MIPS_REG_SP, MIPS_REG_SP, FRAME));
    for (int i = 0; i < w->n_tail; i++) put(&e, w->tail[i]);
    if (w->resume) put_j(&e, w->resume);
    else           put(&e, mips_jr(MIPS_REG_RA));
    put(&e, MIPS_NOP);
    return e.ok ? e.n : 0;
}

#ifndef MHFU_HOST
#include "internal.h"

#define WRAP_MAX_WORDS 32

static uint32_t g_common;

extern "C" uint32_t mhfu_wrap_build(const mhfu_wrap_t *w)
{
    if (!g_common) {
        const int n = SAVE_WORDS + RESTORE_WORDS;
        uint32_t *c = mhfu_cave_alloc(n);
        if (!c || !mhfu_wrap_emit_common(c, n, (uint32_t)(uintptr_t)c)) return 0;
        g_common = (uint32_t)(uintptr_t)c;
    }
    /* the words do not depend on where they land, and the cave is one jump region */
    uint32_t tmp[WRAP_MAX_WORDS];
    int n = mhfu_wrap_emit(tmp, WRAP_MAX_WORDS, g_common, g_common, w);
    if (!n) return 0;
    uint32_t *p = mhfu_cave_alloc(n);
    if (!p) return 0;
    memcpy(p, tmp, 4u * (unsigned)n);
    mhfu_hook_flush_caches();
    return (uint32_t)(uintptr_t)p;
}
#endif
