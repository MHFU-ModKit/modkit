/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* Hook arbitration: every patch belongs to one owner (a mod id), a second owner gets CONFLICT,
 * and the framework restores an owner's patches when that mod shuts down. Events are in
 * events.h. */
#ifndef MHFU_HOOKS_H
#define MHFU_HOOKS_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    MHFU_HOOK_OK       =  0,
    MHFU_HOOK_CONFLICT = -1,   /* another owner holds or has queued the target */
    MHFU_HOOK_NOSPACE  = -2,   /* a table or the code cave is full */
    MHFU_HOOK_BADARG   = -3,
} mhfu_hook_rc_t;

/* The registers a wrapper saved; its helper may edit any of them, and the wrapper restores
 * them before the original code runs. A wrapper also keeps the FPU's caller-saved state. */
typedef struct {
    uint32_t at, v0, v1;
    uint32_t a0, a1, a2, a3;
    uint32_t t0, t1, t2, t3, t4, t5, t6, t7, t8, t9;
    uint32_t s0, s1, s2, s3, s4, s5, s6, s7;
    uint32_t hi, lo;
    uint32_t ra;
    uint32_t sp;   /* the caller's, above the wrapper's frame; read only */
    uint32_t pc;   /* the patched address; read only */
} mhfu_regs_t;

typedef void (*mhfu_wrap_fn)(mhfu_regs_t *regs);

/* A code patch takes only while the JIT has not translated the target, so the calls marked
 * Queued land at the next title or menu screen; a vtable swap is a data write and takes at
 * any time. */

mhfu_hook_rc_t mhfu_hook_vtable(uint32_t slot_addr, uint32_t fn, const char *owner);
/* Patches now: only for code the JIT has not translated yet. */
mhfu_hook_rc_t mhfu_hook_word(uint32_t addr, uint32_t word, const char *owner);

/* A word patch that lands only while addr still holds expect. Queued. */
mhfu_hook_rc_t mhfu_hook_word_when_quiet(uint32_t addr, uint32_t expect,
                                         uint32_t word, const char *owner);

/* Wrap the call site `jal orig_target`: pre runs before the call and may edit any register
 * it gets, post runs after it and may edit v0/v1; either may be NULL. orig_target may take
 * only register arguments (a0-a3, t0-t3, f12-f19): stack arguments would be read from the
 * wrapper's frame. Queued. */
mhfu_hook_rc_t mhfu_hook_call(uint32_t call_site, uint32_t orig_target,
                              mhfu_wrap_fn pre, mhfu_wrap_fn post, const char *owner);

/* Detour addr through pre: `J wrapper; NOP` over the two words expect0 and expect1, which the
 * wrapper runs after pre and then resumes at addr + 8. Neither may be a branch or a jump.
 * Queued. */
mhfu_hook_rc_t mhfu_hook_detour(uint32_t addr, uint32_t expect0, uint32_t expect1,
                                mhfu_wrap_fn pre, const char *owner);

/* Restore every patch owner holds and drop the ones it has queued; the framework calls it on
 * mod shutdown. */
void mhfu_hook_release(const char *owner);

/* Write back the data cache, then drop the instruction cache: call after writing a stub.
 * The patch calls above already flush what they patch. */
void mhfu_hook_flush_caches(void);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* MHFU_HOOKS_H */
