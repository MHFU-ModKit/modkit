/* The wrapper builder, every hook's one way to call C from patched code (wrap.cpp). Free of
 * PSP headers, so the host tests compile it. */
#ifndef MHFU_CORE_WRAP_H
#define MHFU_CORE_WRAP_H

#include <stdint.h>
#include "mhfu/hooks.h"

#ifdef __cplusplus
extern "C" {
#endif

/* A wrapper saves every caller-saved register into an mhfu_regs_t on its stack, runs pre,
 * restores them (with pre's edits) and calls call, runs post, restores them again, runs the
 * displaced words and resumes. No conditional branches, for the JIT. Not for code on the
 * big-monster construction thread, whose stack reaches into the PRX: those stubs stay
 * frame-free (em_vhook.cpp, joint_fix.cpp). */
typedef struct {
    mhfu_wrap_fn pre;       /* may be 0 */
    uint32_t     call;      /* 0, or a function called between pre and post */
    mhfu_wrap_fn post;      /* may be 0; sees the call's v0/v1 and may edit them */
    uint32_t     pc;        /* recorded in regs->pc */
    uint32_t     tail[2];   /* the words a detour displaced, run after the restore */
    int          n_tail;
    uint32_t     resume;    /* 0: return through ra; else jump here */
} mhfu_wrap_t;
/* Writes the save and restore routines every wrapper calls, for address base, into out (cap
 * words); returns the word count, or 0 when it does not fit. */
int mhfu_wrap_emit_common(uint32_t *out, int cap, uint32_t base);
/* Writes w's code for address base into out (cap words), calling the routines at common;
 * returns the word count, or 0 when it does not fit or a jump leaves base's 256 MB region.
 * Pure, so a host test can run the words at base. */
int mhfu_wrap_emit(uint32_t *out, int cap, uint32_t base, uint32_t common,
                   const mhfu_wrap_t *w);
/* Emits w into the code cave and flushes the caches; the wrapper's address, 0 when full. */
uint32_t mhfu_wrap_build(const mhfu_wrap_t *w);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* MHFU_CORE_WRAP_H */
