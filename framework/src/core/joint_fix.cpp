/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/*
 * The joint fix: when the engine hands the joint builder a skeleton without the skeleton
 * magic, as it does on a quest with a relocated port, the builder gets the relocated PAC's
 * skeleton (mhfu_joint_fix_skeleton) instead.
 *
 * The builder takes (entity a0, bone array a1, skeleton blob a2) and reads the bone count
 * from a2; a bad a2 gives a garbage count and the joint-array allocation crashes. a1 is a2
 * plus a fixed offset, so it is off too. The patch replaces the builder's `move s2, a2` after
 * the prologue (the entry's delay slot would run `sw ra` on an unadjusted sp); there s4 = a1
 * and a2 is live, and the next word runs as our J's delay slot, as it would unpatched.
 */
#include "mhfu/mips.h"
#include "addresses.gen.h"
#include "internal.h"

#define OWNER       "mhfu_joint"
#define STUB_INSNS  16

/* The stub, frame-free and branchless: it runs on the big-monster construction thread, whose
 * stack reaches into the PRX, and a branch trips the JIT's block markers.
 *   t5 = [a2] ^ SKELETON_MAGIC   0 for a valid skeleton: native monsters pass through
 *   t2 = [skel_word]             the relocated skeleton, 0 for none: everything passes through
 *   t4 = t2 + (s4 - a2)          the bone array rebased on it
 *   s2 = a2 (the displaced word); if t5 && t2: s2 = t2, s4 = t4
 * Clobbers t0-t5, which the builder does not read before writing at the resume point. */
extern "C" int mhfu_joint_fix_emit(uint32_t *out, int cap, uint32_t skel_word)
{
    const uint32_t w[] = {
        mips_lw(MIPS_REG_T0, 0, MIPS_REG_A2),
        mips_lui(MIPS_REG_T1, (uint16_t)(MHFU_SKELETON_MAGIC >> 16)),
        mips_ori(MIPS_REG_T1, MIPS_REG_T1, (uint16_t)MHFU_SKELETON_MAGIC),
        mips_xor(MIPS_REG_T5, MIPS_REG_T0, MIPS_REG_T1),
        mips_lui(MIPS_REG_T2, (uint16_t)((skel_word + 0x8000u) >> 16)),
        mips_lw(MIPS_REG_T2, (int16_t)(skel_word & 0xFFFFu), MIPS_REG_T2),
        mips_subu(MIPS_REG_T3, MIPS_REG_S4, MIPS_REG_A2),
        mips_addu(MIPS_REG_T4, MIPS_REG_T2, MIPS_REG_T3),
        mips_move(MIPS_REG_S2, MIPS_REG_A2),
        mips_movz(MIPS_REG_T5, MIPS_REG_ZERO, MIPS_REG_T2),
        mips_movn(MIPS_REG_S2, MIPS_REG_T2, MIPS_REG_T5),
        mips_movn(MIPS_REG_S4, MIPS_REG_T4, MIPS_REG_T5),
        mips_j(MHFU_JOINT_BUILDER_RESUME),
        MIPS_NOP,
    };
    const int n = (int)(sizeof(w) / sizeof(w[0]));
    if (n > cap) return 0;
    for (int i = 0; i < n; i++) out[i] = w[i];
    return n;
}

#ifndef MHFU_HOST
#include "mhfu/hooks.h"
#include "mhfu/log.h"
#include "mhfu/memory.h"

static uint32_t g_stub[STUB_INSNS] __attribute__((aligned(64)));
static volatile uint32_t g_skel;   /* the stub loads it on every call */

extern "C" void mhfu_joint_fix_skeleton(uint32_t skel)
{
    if (skel && mhfu_mem_read_u32(skel) != MHFU_SKELETON_MAGIC) {
        mhfu_log("[joint] 0x%08X is not a skeleton; the fix passes through",
                 (unsigned)skel);
        skel = 0;
    }
    g_skel = skel;
}

extern "C" void mhfu_joint_fix_init(void)
{
    int n = mhfu_joint_fix_emit(g_stub, STUB_INSNS, (uint32_t)(uintptr_t)&g_skel);
    for (int i = n; i < STUB_INSNS; i++) g_stub[i] = MIPS_NOP;
    mhfu_hook_flush_caches();

    /* EBOOT code: queued for the JIT-cold title/menu window */
    mhfu_hook_rc_t rc = mhfu_hook_word_when_quiet(
        MHFU_JOINT_BUILDER_HOOK, mips_move(MIPS_REG_S2, MIPS_REG_A2),
        mips_j((uint32_t)(uintptr_t)g_stub), OWNER);
    mhfu_log("[joint] fix %s @0x%08X -> stub 0x%08X (rc=%d)",
             rc == MHFU_HOOK_OK ? "queued" : "FAILED", MHFU_JOINT_BUILDER_HOOK,
             (unsigned)(uintptr_t)g_stub, (int)rc);
}
#endif
