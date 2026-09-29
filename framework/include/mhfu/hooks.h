/* Hook arbitration. An event hook fans out to every mod; an exclusive patch (function entry,
 * vtable slot, code word) belongs to one mod, and the framework restores it on shutdown. */
#ifndef MHFU_HOOKS_H
#define MHFU_HOOKS_H

#include <stdint.h>
#include "events.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    MHFU_HOOK_OK       =  0,
    MHFU_HOOK_CONFLICT = -1,   /* another mod owns the target */
    MHFU_HOOK_NOSPACE  = -2,   /* owner table full */
    MHFU_HOOK_BADARG   = -3,
} mhfu_hook_rc_t;

/* An event callback with a priority: higher runs first, ties in registration order. */
mhfu_hook_rc_t mhfu_hook_event(mhfu_event_id_t id, mhfu_event_cb_t cb, int priority);

/* Exclusive patches; owner is the mod id. A code patch takes only while the JIT has not
 * translated the target yet, so claim it at title or menu (mhfu_hook_word_when_quiet); a
 * vtable swap is a data write and takes at any time. */

/* Redirect a function entry to stub with `J stub; NOP`. The stub must be branchless: the
 * JIT's block markers send a short branch block to PC 0. */
mhfu_hook_rc_t mhfu_hook_function(uint32_t addr, uint32_t stub, const char *owner);
mhfu_hook_rc_t mhfu_hook_vtable(uint32_t slot_addr, uint32_t fn, const char *owner);
mhfu_hook_rc_t mhfu_hook_word(uint32_t addr, uint32_t word, const char *owner);

/* Queue a word patch; it lands at title or menu, and only while addr still holds expect. */
mhfu_hook_rc_t mhfu_hook_word_when_quiet(uint32_t addr, uint32_t expect,
                                         uint32_t word, const char *owner);

#define MHFU_HOOK_WRAP_PREFIX  0   /* helper runs before the original */
#define MHFU_HOOK_WRAP_POSTFIX 1   /* helper runs after the original */

/* Wrap the call site `jal orig_target`: a cave stub calls helper(the call's $a0) around
 * orig_target, and the site is queued with mhfu_hook_word_when_quiet. */
mhfu_hook_rc_t mhfu_hook_call(uint32_t call_site, uint32_t orig_target,
                              void (*helper)(uint32_t), int mode, const char *owner);

/* Restore every patch owner holds; the framework calls it on mod shutdown. */
void mhfu_hook_release(const char *owner);

/* Write back the data cache, then drop the instruction cache: call after writing a stub.
 * The patch calls above already flush the word they patch. */
void mhfu_hook_flush_caches(void);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* MHFU_HOOKS_H */
