/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/*
 * MWo3 overlay relocator, mhfu.reloc in C (tests/test_reloc.py holds the two to the same bytes).
 * An overlay has no relocation table, so its references are found as mhfu.mips finds them:
 * lui/lo pairs by spimdisasm's per-function RegistersTracker walk, ported from rabbitizer.
 */
#include "mhfu/ovl_reloc.h"
#include <stddef.h>
#include <stdlib.h>
#include <string.h>

#define TEXT    ((uint32_t)sizeof(mhfu_ovl_header_t))   /* text follows the header */
#define NONE    0xFFFFFFFFu
static_assert(sizeof(mhfu_ovl_header_t) == 0x40, "MWo3 header");

static inline uint32_t rd32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static inline void wr32(uint8_t *p, uint32_t v) {
    p[0] = v & 0xFF; p[1] = (v >> 8) & 0xFF; p[2] = (v >> 16) & 0xFF; p[3] = (v >> 24) & 0xFF;
}
#define RS(w) (((w) >> 21) & 0x1F)
#define RT(w) (((w) >> 16) & 0x1F)
#define RD(w) (((w) >> 11) & 0x1F)

/* --- what the walk reads of an instruction, as rabbitizer's Allegrex table has it for a valid
 * encoding (reserved fields go unchecked: overlay code has none) --- */
enum {
    I_BRANCH = 1 << 0,   /* isBranch: b, bal and the bc/bv branches included, j not */
    I_LIKELY = 1 << 1,
    I_UNCOND = 1 << 2,   /* b and j */
    I_JUMP   = 1 << 3,   /* j jal jr jalr */
    I_JADDR  = 1 << 4,   /* j jal */
    I_LINK   = 1 << 5,
    I_RETURN = 1 << 6,   /* jr ra */
    I_HI     = 1 << 7,   /* lui */
    I_LO     = 1 << 8,   /* takes a %lo */
    I_CONST  = 1 << 9,   /* ori: the %lo of a constant, not an address */
    I_WRT    = 1 << 10,  /* writes GPR rt */
    I_WRD    = 1 << 11,  /* writes GPR rd */
    I_MOVE   = 1 << 12,  /* addu, or: may copy a register's state */
    I_DEREF  = 1 << 13,  /* load or store */
    I_RRS    = 1 << 14,  /* a branch comparing rs */
    I_RRT    = 1 << 15,  /* a branch comparing rt */
    I_TOFPU  = 1 << 16,  /* mtc1 dmtc1 ctc1: rabbitizer drops the GPR's state */
};

static uint32_t special(uint32_t w)
{
    switch (w & 0x3F) {
        case 0x08: return I_JUMP | (RS(w) == 31 ? I_RETURN : 0);
        case 0x09: return I_JUMP | I_LINK | I_WRD;
        case 0x21: case 0x25: return I_WRD | I_MOVE;
        case 0x00: case 0x02: case 0x03: case 0x04: case 0x06: case 0x07: case 0x0A: case 0x0B:
        case 0x10: case 0x12: case 0x14: case 0x16: case 0x17: case 0x20: case 0x22: case 0x23:
        case 0x24: case 0x26: case 0x27: case 0x2A: case 0x2B: case 0x2C: case 0x2D: case 0x38:
        case 0x3A: case 0x3B: case 0x3C: case 0x3E: case 0x3F:
            return I_WRD;
    }
    return 0;
}

static uint32_t regimm(uint32_t w)
{
    uint32_t rt = RT(w), c = I_BRANCH | I_RRS;
    if (rt & 0x0C) return 0;                           /* traps */
    if (rt & 0x10) {
        if (rt == 0x11 && RS(w) == 0) return I_BRANCH | I_LINK;   /* bal */
        c |= I_LINK;
    }
    return c | (rt & 2 ? I_LIKELY : 0);
}

/* COP0/1/2: moves to and from GPRs and the condition branches. */
static uint32_t cop(uint32_t w, uint32_t op)
{
    uint32_t rs = RS(w);
    if (rs == 8 && (op == 0x12 || RT(w) < 4))
        return I_BRANCH | (RT(w) & 2 ? I_LIKELY : 0);
    if (rs == 0 || rs == 2 || (rs == 1 && op != 0x12)) return I_WRT;   /* mfc cfc dmfc */
    if (op == 0x12) return rs == 3 ? I_WRT : 0;        /* mfv mfvc */
    return (op == 0x11 && rs >= 4 && rs <= 6) ? I_TOFPU : 0;
}

static uint32_t cls(uint32_t w)
{
    uint32_t op = w >> 26;
    switch (op) {
        case 0x00: return w ? special(w) : 0;                              /* nop */
        case 0x01: return regimm(w);
        case 0x02: return I_UNCOND | I_JUMP | I_JADDR;
        case 0x03: return I_JUMP | I_JADDR | I_LINK;
        case 0x04:
            if (RT(w) == 0) return RS(w) == 0 ? I_BRANCH | I_UNCOND : I_BRANCH | I_RRS;
            return I_BRANCH | I_RRS | I_RRT;
        case 0x05: return I_BRANCH | I_RRS | (RT(w) ? I_RRT : 0);
        case 0x06: case 0x07: return I_BRANCH | I_RRS;
        case 0x14: case 0x15: return I_BRANCH | I_LIKELY | I_RRS | I_RRT;
        case 0x16: case 0x17: return I_BRANCH | I_LIKELY | I_RRS;
        case 0x08: case 0x09: return I_LO | I_WRT;
        case 0x0A: case 0x0B: case 0x0C: case 0x0E: return I_WRT;
        case 0x0D: return I_LO | I_CONST | I_WRT;
        case 0x0F: return RS(w) ? 0 : I_HI | I_WRT;
        case 0x10: case 0x11: case 0x12: return cop(w, op);
        case 0x1C: return (w & 0x3F) == 0x24 ? I_WRT : 0;                 /* mfie */
        case 0x1F: {
            uint32_t f = w & 0x3F;
            if (f == 0x00 || f == 0x04) return I_WRT;                      /* ext ins */
            return f == 0x20 ? I_WRD : 0;                                  /* seb seh wsbh ... */
        }
        case 0x1A: case 0x20: case 0x21: case 0x22: case 0x23: case 0x24: case 0x25: case 0x26:
        case 0x27: case 0x30:
            return I_LO | I_WRT | I_DEREF;                                 /* GPR loads */
        case 0x28: case 0x29: case 0x2A: case 0x2B: case 0x2C: case 0x2D: case 0x2E: case 0x31:
        case 0x38: case 0x39:
            return I_LO | I_DEREF;                                    /* stores, lwc1 swc1 sc */
    }
    return 0;
}

static inline int ends_flow(uint32_t c)
{
    return (c & I_UNCOND) || ((c & I_JUMP) && !(c & I_LINK));
}

/* getBranchVramGeneric: a branch's or a j/jal's target. */
static inline uint32_t target(uint32_t w, uint32_t va, uint32_t c)
{
    if (c & I_JADDR) return (va & 0xF0000000u) | ((w & 0x03FFFFFFu) << 2);
    return va + 4 + ((uint32_t)(int32_t)(int16_t)(w & 0xFFFF) << 2);
}

/* --- RegistersTracker, the part that decides which lo pairs with which lui --- */
enum { R_LUI = 1, R_BL = 2, R_LO = 4, R_DEREF = 8, R_BR = 16 };

typedef struct {
    uint32_t value;
    uint32_t lui_at, lo_at, deref_at;   /* where each was set */
    uint32_t f;                         /* R_*; R_BL: the lui sat in a likely branch's slot,
                                         * R_BR: a branch compared it, so it pairs no lo */
} reg_t;
typedef struct { reg_t r[32]; } regs_t;

static inline void clear_hi(reg_t *r) { r->f &= ~(R_LUI | R_BL); r->lui_at = 0; }
static inline void clear_lo(reg_t *r)
{
    r->f &= ~(R_LO | R_DEREF | R_BR); r->lo_at = r->deref_at = 0;
}
static inline void set_lo(reg_t *r, uint32_t v, uint32_t at)
{
    r->value = v; r->f = (r->f & ~(R_DEREF | R_BR)) | R_LO; r->lo_at = at; r->deref_at = 0;
}
/* a move copies all but the branch mark */
static inline void copy(reg_t *d, const reg_t *s) { *d = *s; d->f &= ~R_BR; }
static inline int any(const reg_t *r) { return (r->f & (R_LUI | R_LO)) != 0; }

/* per instruction */
enum { M_BT = 1, M_START = 2, M_KNOWN = 4, M_TAKEN = 8 /* x2: likely */, M_IN = 32, M_OUT = 64 };

typedef struct {
    regs_t   t;
    uint32_t at;
    uint8_t  likely, resumed;
} frame_t;

typedef struct {
    const uint8_t *text;
    uint32_t va0, end;          /* text [va0, end) */
    uint32_t foot_lo, foot_hi;
    uint8_t *mark;
    frame_t *stack;
    uint32_t sp, cap;
    int      oom;
    uint32_t fn_lo, fn_hi;
} ctx_t;

static inline uint32_t word(const ctx_t *x, uint32_t va) { return rd32(x->text + (va - x->va0)); }
static inline uint8_t *mk(const ctx_t *x, uint32_t va) { return &x->mark[(va - x->va0) >> 2]; }
static inline int in_text(const ctx_t *x, uint32_t va) { return va >= x->va0 && va < x->end; }

static void unset_after_call(regs_t *t, uint32_t prev_c)
{
    if (!(prev_c & I_LINK)) return;
    for (int i = 1; i < 32; i++)
        if (i <= 15 || i == 24 || i == 25 || i == 31) memset(&t->r[i], 0, sizeof(reg_t));
}

/* moveRegisters: 1 when rd took a source's state */
static int move(regs_t *t, uint32_t w)
{
    uint32_t rd = RD(w), rs = RS(w), rt = RT(w), src;
    reg_t *r = t->r;
    if (!rs && !rt) return 0;
    if (rs && rt) {
        int a = any(&r[rs]), b = any(&r[rt]);
        if (a && !b)       src = rs;
        else if (b && !a)  src = rt;
        else if (rd == rs) src = (r[rs].f & R_LUI) ? rs : rt;
        else if (rd == rt) src = (r[rt].f & R_LUI) ? rt : rs;
        else return 0;
        copy(&r[rd], &r[src]);
        return 1;
    }
    src = rs ? rs : rt;
    if (any(&r[src])) { copy(&r[rd], &r[src]); return 1; }
    memset(&r[rd], 0, sizeof(reg_t));
    return 0;
}

static void overwrite(regs_t *t, uint32_t va, uint32_t w, uint32_t c)
{
    if ((c & I_MOVE) && move(t, w)) return;
    int reg = -1;
    if (c & I_TOFPU) reg = (int)RT(w);
    if ((c & I_WRT) && !(c & I_HI)) reg = (int)RT(w);
    if (c & I_WRD) reg = (int)RD(w);
    if (reg < 0) return;
    reg_t *r = &t->r[reg];
    if (r->f & R_LUI) clear_hi(r);
    if (r->lo_at != va && r->deref_at != va) clear_lo(r);
}

static inline void pair(ctx_t *x, uint32_t hi, uint32_t value)
{
    *mk(x, hi) |= (value >= x->foot_lo && value < x->foot_hi) ? M_IN : M_OUT;
}

/* _Walk.process; prev_c NONE when the walk passes no previous instruction */
static void process(ctx_t *x, regs_t *t, uint32_t va, uint32_t w, uint32_t c, uint32_t prev_c)
{
    reg_t *r = t->r;
    if (c & (I_BRANCH | I_UNCOND)) {
        if (c & I_RRS) r[RS(w)].f |= R_BR;
        if (c & I_RRT) r[RT(w)].f |= R_BR;
    } else if (c & I_HI) {
        reg_t *d = &r[RT(w)];
        memset(d, 0, sizeof(*d));
        d->f = R_LUI; d->lui_at = va; d->value = (w & 0xFFFF) << 16;
        if (prev_c != NONE && (prev_c & (I_LIKELY | I_UNCOND))) d->f |= R_BL;
    } else if ((c & I_LO) && (c & I_CONST)) {
        const reg_t *s = &r[RS(w)];
        if (s->f & R_LUI) {
            uint32_t v = (word(x, s->lui_at) << 16) | (w & 0xFFFF);
            pair(x, s->lui_at, v);
            set_lo(&r[RT(w)], v, va);
        }
    } else if ((c & I_LO) && !(r[RS(w)].f & R_BR)) {
        const reg_t *s = &r[RS(w)];
        if ((s->f & (R_LUI | R_BL)) == R_LUI) {
            uint32_t v = s->value + (uint32_t)(int32_t)(int16_t)(w & 0xFFFF);
            pair(x, s->lui_at, v);
            if (c & I_WRT) {
                reg_t *d = &r[RT(w)];
                set_lo(d, v, va);
                if (c & I_DEREF) { d->f |= R_DEREF; d->deref_at = va; }
            }
        } else if (RS(w) != 28 && (c & I_WRT) && (c & I_DEREF) &&     /* not gp-relative */
                   (s->f & R_LO) && !(s->f & R_DEREF)) {
            reg_t *d = &r[RT(w)];
            copy(d, s);
            d->f |= R_DEREF; d->deref_at = va;
        }
    }
    overwrite(t, va, w, c);
}

/* Frames live in one growable stack; outer is a frame index, or -1 for the walk's own. */
static void enter(ctx_t *x, regs_t *own, int outer, uint32_t va, uint32_t w, uint32_t c,
                  uint32_t pva, uint32_t pw, uint32_t pc, int likely)
{
    if (!(pc & (I_BRANCH | I_UNCOND))) return;
    uint32_t to = target(pw, pva, pc);
    if (to < x->fn_lo) return;
    if (x->sp == x->cap) {
        uint32_t cap = x->cap ? x->cap * 2 : 16;
        frame_t *s = (frame_t *)realloc(x->stack, cap * sizeof(frame_t));
        if (!s) { x->oom = 1; return; }
        x->stack = s; x->cap = cap;
    }
    frame_t *f = &x->stack[x->sp];
    f->t = outer < 0 ? *own : x->stack[outer].t;
    process(x, &f->t, va, w, c, NONE);
    uint8_t bit = (uint8_t)(M_TAKEN << (likely ? 1 : 0));
    if (*mk(x, va) & bit) return;
    *mk(x, va) |= bit;
    f->at = to; f->likely = (uint8_t)likely; f->resumed = 0;
    x->sp++;
}

/* look_ahead: depth first into every branch target, registers as they were at the branch */
static void look_ahead(ctx_t *x, regs_t *own, uint32_t va, uint32_t w, uint32_t c,
                       uint32_t pw, uint32_t pc)
{
    enter(x, own, -1, va, w, c, va - 4, pw, pc, (pc & I_LIKELY) != 0);
    while (x->sp && !x->oom) {
        frame_t *f = &x->stack[x->sp - 1];
        if (f->resumed) {
            uint32_t came = cls(word(x, f->at - 4));
            if (ends_flow(came)) { x->sp--; continue; }
            unset_after_call(&f->t, came);
            f->at += 4;
            f->resumed = 0;
        }
        if (f->at >= x->fn_hi) { x->sp--; continue; }
        uint32_t at = f->at, tw = word(x, at), tc = cls(tw);
        if (at < x->va0 + 4) { process(x, &f->t, at, tw, tc, NONE); f->at += 4; continue; }
        uint32_t bw = word(x, at - 4), bc = cls(bw);
        process(x, &f->t, at, tw, tc, bc);
        f->resumed = 1;
        enter(x, own, (int)x->sp - 1, at, tw, tc, at - 4, bw, bc, f->likely || (bc & I_LIKELY));
    }
}

/* _Walk.run over [fn_lo, fn_hi) */
static void walk(ctx_t *x)
{
    regs_t t;
    memset(&t, 0, sizeof(t));
    uint32_t pw = 0, pc = NONE;
    for (uint32_t va = x->fn_lo; va < x->fn_hi && !x->oom; va += 4) {
        uint32_t w = word(x, va), c = cls(w);
        if (pc == NONE || !(pc & (I_LIKELY | I_UNCOND))) process(x, &t, va, w, c, pc);
        if (pc != NONE) {
            look_ahead(x, &t, va, w, c, pw, pc);
            if ((pc & I_JADDR) && !(pc & I_LINK)) {
                uint32_t to = target(pw, va - 4, pc);
                if (to < x->fn_lo || to >= x->fn_hi) memset(&t, 0, sizeof(t));
            }
            unset_after_call(&t, pc);
            if (ends_flow(pc) || (pc & I_RETURN)) memset(&t, 0, sizeof(t));
        }
        pw = w; pc = c;
    }
}

/* Code._run_start: where the straight run holding va starts, or NONE if a branch enters it */
static uint32_t run_start(const ctx_t *x, uint32_t va)
{
    uint32_t a = va;
    while (a >= x->va0 + 4 && !(a >= x->va0 + 8 && ends_flow(cls(word(x, a - 8))))) {
        if (*mk(x, a) & M_BT) return NONE;
        a -= 4;
    }
    while (a < va && word(x, a) == 0) a += 4;
    return (*mk(x, a) & M_BT) ? NONE : a;
}

/* Code.entries: jal targets, prologue runs, and tail-call targets */
static void entries(ctx_t *x)
{
    uint32_t va;
    for (va = x->va0; va < x->end; va += 4) {
        uint32_t w = word(x, va), c = cls(w);
        if (c & I_BRANCH) {
            uint32_t to = target(w, va, c);
            if (in_text(x, to)) *mk(x, to) |= M_BT;
        }
    }
    *mk(x, x->va0) |= M_START;
    for (va = x->va0; va < x->end; va += 4) {
        uint32_t w = word(x, va), to = target(w, va, I_JADDR);
        if ((w >> 26) == 3 && in_text(x, to)) *mk(x, to) |= M_START;
    }
    for (va = x->va0; va < x->end; va += 4) {
        uint32_t w = word(x, va);                    /* addiu sp, sp, -N */
        if ((w & 0xFFFF8000u) == 0x27BD8000u) {
            uint32_t s = run_start(x, va);
            if (s != NONE) *mk(x, s) |= M_START;
        }
    }
    for (va = x->va0; va < x->end; va += 4)
        if (*mk(x, va) & M_START) *mk(x, va) |= M_KNOWN;
    for (va = x->va0; va < x->end; va += 4) {
        uint32_t w = word(x, va), to = target(w, va, I_JADDR);
        if ((w >> 26) != 2 || !in_text(x, to) || (*mk(x, to) & M_START) || run_start(x, to) != to)
            continue;
        uint32_t lo = va, hi = va + 4;              /* the known entries around the j */
        while (!(*mk(x, lo) & M_KNOWN)) lo -= 4;
        while (hi < x->end && !(*mk(x, hi) & M_KNOWN)) hi += 4;
        int take = to < lo || to >= hi;
        if (!take) {
            uint32_t a = va + 8 > x->va0 ? va + 8 : x->va0, stop = to < x->end ? to : x->end;
            while (a < stop && word(x, a) == 0) a += 4;
            take = a >= stop;
        }
        if (take) *mk(x, to) |= M_START;
    }
}

extern "C" mhfu_ovl_reloc_stats_t
mhfu_ovl_relocate(void *image, uint32_t image_size, int32_t delta)
{
    mhfu_ovl_reloc_stats_t st;
    memset(&st, 0, sizeof(st));
    uint8_t *d = (uint8_t *)image;
    if (image_size < TEXT || memcmp(d, "MWo3", 4) != 0 || (delta & 0xFFFF)) return st;

    mhfu_ovl_header_t h;
    memcpy(&h, d, sizeof(h));
    uint32_t load = h.load_address, text = h.text_size, data = h.data_size;
    uint64_t stop = (uint64_t)load + TEXT + text + data + h.bss_size;
    if (((text | data) & 3) || (uint64_t)TEXT + text + data > image_size || stop > 0xFFFFFFFFu)
        return st;

    ctx_t x;
    memset(&x, 0, sizeof(x));
    x.text = d + TEXT; x.va0 = load + TEXT; x.end = x.va0 + text;
    x.foot_lo = load; x.foot_hi = (uint32_t)stop;
    x.mark = (uint8_t *)calloc(text / 4 + 1, 1);
    if (!x.mark) return st;

    entries(&x);
    for (x.fn_lo = x.va0; x.fn_lo < x.end && !x.oom; x.fn_lo = x.fn_hi) {
        x.fn_hi = x.fn_lo + 4;
        while (x.fn_hi < x.end && !(*mk(&x, x.fn_hi) & M_START)) x.fn_hi += 4;
        walk(&x);
    }
    free(x.stack);

    /* a lui that forms addresses in and out of the footprint cannot move; a jump must reach */
    int ok = !x.oom;
    uint32_t va;
    for (va = x.va0; ok && va < x.end; va += 4) {
        uint32_t w = word(&x, va), c = cls(w), to = target(w, va, c);
        if ((*mk(&x, va) & (M_IN | M_OUT)) == (M_IN | M_OUT)) ok = 0;
        if ((c & I_JADDR) && to >= x.foot_lo && to < x.foot_hi &&
            ((to + (uint32_t)delta) & 0xF0000000u) != (va & 0xF0000000u)) ok = 0;
    }
    if (!ok) { free(x.mark); return st; }

    for (va = x.va0; va < x.end; va += 4) {
        uint8_t *p = d + (va - load);
        uint32_t w = rd32(p), c = cls(w), to = target(w, va, c);
        if ((c & I_JADDR) && to >= x.foot_lo && to < x.foot_hi) {
            wr32(p, (w & 0xFC000000u) | (((to + (uint32_t)delta) >> 2) & 0x03FFFFFFu));
            st.n_jump++;
        } else if (*mk(&x, va) & M_IN) {
            wr32(p, (w & 0xFFFF0000u) | ((w + ((uint32_t)delta >> 16)) & 0xFFFF));
            st.n_hi++;
        }
    }
    free(x.mark);
    for (uint32_t o = TEXT + text; o < TEXT + text + data; o += 4) {
        uint32_t v = rd32(d + o);
        if (v >= x.foot_lo && v < x.foot_hi) { wr32(d + o, v + (uint32_t)delta); st.n_data++; }
    }
    static const uint8_t header[] = {
        offsetof(mhfu_ovl_header_t, load_address),
        offsetof(mhfu_ovl_header_t, static_init_start),
        offsetof(mhfu_ovl_header_t, static_init_end),
    };
    for (unsigned i = 0; i < sizeof(header); i++) {       /* an end may sit at the very end */
        uint32_t v = rd32(d + header[i]);
        if (v >= x.foot_lo && v <= x.foot_hi) wr32(d + header[i], v + (uint32_t)delta);
    }
    st.new_base = load + (uint32_t)delta;
    st.ok = 1;
    return st;
}
