/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* Hook arbitration (hooks.h): every patched word or vtable slot has one owner and keeps its
 * original value for the release. Code patches that wait for a JIT-cold screen queue here and
 * the deferred thread lands them; detour and call wrappers are built once per address. */
#include <string.h>

#include "mhfu/hooks.h"
#include "mhfu/mips.h"
#include "mhfu/log.h"
#include "internal.h"

#ifndef MHFU_HOST
#include <pspsdk.h>
#include <psputils.h>
#include <pspthreadman.h>
#define WORD(a) (*(volatile uint32_t *)(uintptr_t)(a))
#else
#define WORD(a) (*mhfu_host_word(a))
#endif

#define MAX_HOOKS    64
#define MAX_QUEUED   32
#define MAX_WRAPPERS 48
#define OWNER_LEN    32

enum hook_kind { HK_FREE = 0, HK_CODE, HK_VTABLE };

typedef struct {
    uint8_t  kind;
    uint8_t  n;                /* words: 1, or 2 for a detour */
    uint32_t addr;
    uint32_t orig[2];
    char     owner[OWNER_LEN];
} hook_record_t;

typedef struct {
    uint8_t  pending;
    uint8_t  n;
    uint8_t  warned;           /* expect mismatch logged */
    uint32_t addr;
    uint32_t expect[2];
    uint32_t word[2];
    char     owner[OWNER_LEN];
} queued_t;

typedef struct {
    uint32_t    addr;          /* 0: free */
    mhfu_wrap_t w;
    uint32_t    wrapper;
} wrapper_rec_t;

static hook_record_t g_hooks[MAX_HOOKS];
static queued_t      g_queue[MAX_QUEUED];
static wrapper_rec_t g_wrappers[MAX_WRAPPERS];
static uint32_t      g_mismatch_logged[MAX_WRAPPERS];   /* detour_now: addrs logged once */
static const char   *g_holder;                          /* who a CONFLICT met, under the lock */

/* --- platform: the lock and the cache-coherent code write ------------------------------ */

#ifndef MHFU_HOST
static SceUID g_lock = -1;
static void lock(void)   { if (g_lock >= 0) sceKernelWaitSema(g_lock, 1, 0); }
static void unlock(void) { if (g_lock >= 0) sceKernelSignalSema(g_lock, 1); }

/* Ranged invalidate per write: the one path that makes PPSSPP drop a stale JIT block, and the
 * order real hardware needs (dcache writeback, then icache). */
extern "C" void mhfu_smc_patch_word(uint32_t addr, uint32_t word)
{
    WORD(addr) = word;
    sceKernelDcacheWritebackInvalidateRange((const void *)(uintptr_t)addr, 4);
    sceKernelIcacheInvalidateRange((const void *)(uintptr_t)addr, 4);
}

extern "C" void mhfu_hook_flush_caches(void)
{
    sceKernelDcacheWritebackInvalidateAll();
    sceKernelIcacheInvalidateAll();
}

static void data_write(uint32_t addr, uint32_t v)
{
    WORD(addr) = v;   /* a data write: the JIT cannot miss it */
    sceKernelDcacheWritebackInvalidateRange((const void *)(uintptr_t)addr, 4);
}
#else
static void lock(void)   {}
static void unlock(void) {}
extern "C" void mhfu_smc_patch_word(uint32_t addr, uint32_t word) { WORD(addr) = word; }
extern "C" void mhfu_hook_flush_caches(void) {}
static void data_write(uint32_t addr, uint32_t v) { WORD(addr) = v; }
#endif

extern "C" void mhfu_hook_init(void)
{
    memset(g_hooks, 0, sizeof(g_hooks));
    memset(g_queue, 0, sizeof(g_queue));
    memset(g_wrappers, 0, sizeof(g_wrappers));
    memset(g_mismatch_logged, 0, sizeof(g_mismatch_logged));
#ifndef MHFU_HOST
    if (g_lock < 0) g_lock = sceKernelCreateSema("mhfu_hooks", 0, 1, 1, 0);
#endif
}

/* --- the table ------------------------------------------------------------------------ */

static int overlaps(uint32_t a, int n, uint32_t b, int m)
{
    return a < b + 4u * (uint32_t)m && b < a + 4u * (uint32_t)n;
}

static void set_owner(char *dst, const char *owner)
{
    strncpy(dst, owner, OWNER_LEN - 1);
    dst[OWNER_LEN - 1] = 0;
}

/* Another owner that holds or has queued a word of [addr, addr + 4n), or 0. */
static const char *other_owner(uint32_t addr, int n, const char *owner)
{
    for (int i = 0; i < MAX_HOOKS; i++) {
        const hook_record_t *r = &g_hooks[i];
        if (r->kind != HK_FREE && overlaps(addr, n, r->addr, r->n)
            && strncmp(r->owner, owner, OWNER_LEN - 1) != 0)
            return r->owner;
    }
    for (int i = 0; i < MAX_QUEUED; i++) {
        const queued_t *q = &g_queue[i];
        if (q->pending && overlaps(addr, n, q->addr, q->n)
            && strncmp(q->owner, owner, OWNER_LEN - 1) != 0)
            return q->owner;
    }
    return 0;
}

/* rc, logging a CONFLICT with the holder the check met */
/* 1 when owner's record covers exactly [addr, n) */
static int holds(uint32_t addr, int n, const char *owner)
{
    for (int i = 0; i < MAX_HOOKS; i++) {
        const hook_record_t *r = &g_hooks[i];
        if (r->kind != HK_FREE && r->addr == addr && r->n == n
            && strncmp(r->owner, owner, OWNER_LEN - 1) == 0)
            return 1;
    }
    return 0;
}

static mhfu_hook_rc_t logged(mhfu_hook_rc_t rc, uint32_t addr, const char *owner)
{
    if (rc == MHFU_HOOK_CONFLICT)
        mhfu_log("[hook] CONFLICT @0x%08lx: '%s' wants it, held by '%s'",
                 (unsigned long)addr, owner, g_holder);
    return rc;
}

static mhfu_hook_rc_t check_free(uint32_t addr, int n, const char *owner)
{
    g_holder = other_owner(addr, n, owner);
    return g_holder ? MHFU_HOOK_CONFLICT : MHFU_HOOK_OK;
}

/* The record for exactly [addr, n) of owner, made on first claim with the words there now. */
static mhfu_hook_rc_t claim(uint8_t kind, uint32_t addr, int n, const char *owner,
                            hook_record_t **out)
{
    if (check_free(addr, n, owner) != MHFU_HOOK_OK) return MHFU_HOOK_CONFLICT;
    hook_record_t *free_rec = 0;
    for (int i = 0; i < MAX_HOOKS; i++) {
        hook_record_t *r = &g_hooks[i];
        if (r->kind == HK_FREE) { if (!free_rec) free_rec = r; continue; }
        if (!overlaps(addr, n, r->addr, r->n)) continue;
        if (r->kind == kind && r->addr == addr && r->n == n) { *out = r; return MHFU_HOOK_OK; }
        g_holder = r->owner;                      /* the same owner, another shape */
        return MHFU_HOOK_CONFLICT;
    }
    if (!free_rec) {
        mhfu_log("[hook] table full, '%s' refused @0x%08lx", owner, (unsigned long)addr);
        return MHFU_HOOK_NOSPACE;
    }
    free_rec->kind = kind;
    free_rec->n    = (uint8_t)n;
    free_rec->addr = addr;
    for (int i = 0; i < n; i++) free_rec->orig[i] = WORD(addr + 4u * i);
    set_owner(free_rec->owner, owner);
    *out = free_rec;
    return MHFU_HOOK_OK;
}

/* Highest address first, so a detour's delay-slot NOP is in place before its J. */
static void write_code(uint32_t addr, const uint32_t *words, int n)
{
    for (int i = n - 1; i >= 0; i--) mhfu_smc_patch_word(addr + 4u * i, words[i]);
}

/* Lowest address first, so the J goes before the NOP it needs. */
static void restore(hook_record_t *r)
{
    if (r->kind == HK_VTABLE) data_write(r->addr, r->orig[0]);
    else for (int i = 0; i < r->n; i++) mhfu_smc_patch_word(r->addr + 4u * i, r->orig[i]);
    mhfu_log("[hook] restored '%s' @0x%08lx", r->owner, (unsigned long)r->addr);
    r->kind = HK_FREE;
}

static void unqueue(uint32_t addr, const char *owner)
{
    for (int i = 0; i < MAX_QUEUED; i++) {
        queued_t *q = &g_queue[i];
        if (q->pending && q->addr == addr && strncmp(q->owner, owner, OWNER_LEN - 1) == 0)
            q->pending = 0;
    }
}

/* Claims [addr, n) for owner and writes words now; the owner's own queued copy is dropped. */
static mhfu_hook_rc_t patch_now(uint32_t addr, const uint32_t *words, int n, const char *owner)
{
    hook_record_t *r;
    mhfu_hook_rc_t rc = claim(HK_CODE, addr, n, owner, &r);
    if (rc != MHFU_HOOK_OK) return rc;
    unqueue(addr, owner);
    write_code(addr, words, n);
    return MHFU_HOOK_OK;
}

/* --- wrappers ------------------------------------------------------------------------- */

static int same_wrap(const mhfu_wrap_t *a, const mhfu_wrap_t *b)
{
    if (a->pre != b->pre || a->call != b->call || a->post != b->post || a->pc != b->pc
        || a->n_tail != b->n_tail || a->resume != b->resume)
        return 0;
    for (int i = 0; i < a->n_tail; i++)
        if (a->tail[i] != b->tail[i]) return 0;
    return 1;
}

/* The wrapper already built for addr from w, or 0. */
static uint32_t wrapper_find(uint32_t addr, const mhfu_wrap_t *w)
{
    for (int i = 0; i < MAX_WRAPPERS; i++) {
        const wrapper_rec_t *r = &g_wrappers[i];
        if (r->addr == addr && same_wrap(&r->w, w)) return r->wrapper;
    }
    return 0;
}

/* The wrapper for addr built from w, reusing an identical one; 0 when full. */
static uint32_t wrapper_for(uint32_t addr, const mhfu_wrap_t *w)
{
    uint32_t wr = wrapper_find(addr, w);
    if (wr) return wr;
    wrapper_rec_t *free_rec = 0;
    for (int i = 0; i < MAX_WRAPPERS && !free_rec; i++)
        if (!g_wrappers[i].addr) free_rec = &g_wrappers[i];
    if (!free_rec) { mhfu_log("[hook] wrapper table full @0x%08lx", (unsigned long)addr); return 0; }
    wr = mhfu_wrap_build(w);
    if (!wr) { mhfu_log("[hook] code cave full @0x%08lx", (unsigned long)addr); return 0; }
    free_rec->addr    = addr;
    free_rec->w       = *w;
    free_rec->wrapper = wr;
    return wr;
}

/* A word the wrapper cannot run displaced: any branch or jump, whose target or delay slot
 * would move. */
static int is_control(uint32_t word)
{
    uint32_t op = word >> 26, rs = (word >> 21) & 0x1F;
    if (op == 0x00) return (word & 0x3E) == 0x08;             /* jr, jalr */
    if (op == 0x01) return 1;                                 /* REGIMM branches */
    if (op >= 0x02 && op <= 0x07) return 1;                   /* j, jal, beq..bgtz */
    if (op >= 0x14 && op <= 0x17) return 1;                   /* branch-likely */
    if ((op == 0x11 || op == 0x12) && rs == 0x08) return 1;   /* bc1x, VFPU bvf/bvt */
    return 0;
}

static void detour_wrap(mhfu_wrap_t *w, uint32_t addr, uint32_t e0, uint32_t e1,
                        mhfu_wrap_fn pre)
{
    memset(w, 0, sizeof(*w));
    w->pre     = pre;
    w->pc      = addr;
    w->tail[0] = e0;
    w->tail[1] = e1;
    w->n_tail  = 2;
    w->resume  = addr + 8;
}

/* --- the queue ------------------------------------------------------------------------ */

static mhfu_hook_rc_t queue(uint32_t addr, int n, const uint32_t *expect,
                            const uint32_t *word, const char *owner)
{
    queued_t *q = 0;
    for (int i = 0; i < MAX_QUEUED && !q; i++)
        if (g_queue[i].pending && g_queue[i].addr == addr
            && strncmp(g_queue[i].owner, owner, OWNER_LEN - 1) == 0)
            q = &g_queue[i];                                  /* the owner's update */
    for (int i = 0; i < MAX_QUEUED && !q; i++)
        if (!g_queue[i].pending) q = &g_queue[i];
    if (!q) {
        mhfu_log("[hook] queue full, '%s' refused @0x%08lx", owner, (unsigned long)addr);
        return MHFU_HOOK_NOSPACE;
    }
    q->addr   = addr;
    q->n      = (uint8_t)n;
    q->warned = 0;
    for (int i = 0; i < n; i++) { q->expect[i] = expect[i]; q->word[i] = word[i]; }
    set_owner(q->owner, owner);
    q->pending = 1;
    return MHFU_HOOK_OK;
}

extern "C" void mhfu_hook_land_queued(void)
{
    lock();
    for (int i = 0; i < MAX_QUEUED; i++) {
        queued_t *q = &g_queue[i];
        if (!q->pending) continue;
        int match = 1, placed = 1;
        for (int k = 0; k < q->n; k++) {
            match  &= WORD(q->addr + 4u * k) == q->expect[k];
            placed &= WORD(q->addr + 4u * k) == q->word[k];
        }
        if (placed && !match && holds(q->addr, q->n, q->owner)) {   /* re-queued, landed */
            q->pending = 0;
            continue;
        }
        if (!match) {
            if (!q->warned)
                mhfu_log("[hook] '%s' @0x%08lx waits: 0x%08lx is not the expected 0x%08lx",
                         q->owner, (unsigned long)q->addr,
                         (unsigned long)WORD(q->addr), (unsigned long)q->expect[0]);
            q->warned = 1;
            continue;
        }
        q->pending = 0;
        hook_record_t *r;
        mhfu_hook_rc_t rc = claim(HK_CODE, q->addr, q->n, q->owner, &r);
        if (rc != MHFU_HOOK_OK) {
            mhfu_log("[hook] dropped '%s' @0x%08lx: rc %d, held by '%s'", q->owner,
                     (unsigned long)q->addr, rc, rc == MHFU_HOOK_CONFLICT ? g_holder : "-");
            continue;
        }
        write_code(q->addr, q->word, q->n);
        mhfu_log("[hook] quiet-patched '%s' @0x%08lx", q->owner, (unsigned long)q->addr);
    }
    unlock();
}

/* --- hooks.h -------------------------------------------------------------------------- */

extern "C" mhfu_hook_rc_t mhfu_hook_vtable(uint32_t slot_addr, uint32_t fn, const char *owner)
{
    if (!owner || (slot_addr & 3)) return MHFU_HOOK_BADARG;
    lock();
    hook_record_t *r;
    mhfu_hook_rc_t rc = logged(claim(HK_VTABLE, slot_addr, 1, owner, &r), slot_addr, owner);
    if (rc == MHFU_HOOK_OK) data_write(slot_addr, fn);
    unlock();
    return rc;
}

extern "C" mhfu_hook_rc_t mhfu_hook_word(uint32_t addr, uint32_t word, const char *owner)
{
    if (!owner || (addr & 3)) return MHFU_HOOK_BADARG;
    lock();
    mhfu_hook_rc_t rc = logged(patch_now(addr, &word, 1, owner), addr, owner);
    unlock();
    return rc;
}

extern "C" mhfu_hook_rc_t mhfu_hook_word_when_quiet(uint32_t addr, uint32_t expect,
                                                     uint32_t word, const char *owner)
{
    if (!owner || (addr & 3)) return MHFU_HOOK_BADARG;
    lock();
    mhfu_hook_rc_t rc = logged(check_free(addr, 1, owner), addr, owner);
    if (rc == MHFU_HOOK_OK) rc = queue(addr, 1, &expect, &word, owner);
    unlock();
    return rc;
}

extern "C" mhfu_hook_rc_t mhfu_hook_call(uint32_t call_site, uint32_t orig_target,
                                         mhfu_wrap_fn pre, mhfu_wrap_fn post,
                                         const char *owner)
{
    if (!owner || (!pre && !post) || (call_site & 3) || !orig_target || (orig_target & 3))
        return MHFU_HOOK_BADARG;
    lock();
    mhfu_hook_rc_t rc = logged(check_free(call_site, 1, owner), call_site, owner);
    if (rc == MHFU_HOOK_OK) {
        mhfu_wrap_t w;
        memset(&w, 0, sizeof(w));
        w.pre  = pre;
        w.call = orig_target;
        w.post = post;
        w.pc   = call_site;
        uint32_t wr = wrapper_for(call_site, &w);
        uint32_t expect = mips_jal(orig_target), word = mips_jal(wr);
        rc = wr ? queue(call_site, 1, &expect, &word, owner) : MHFU_HOOK_NOSPACE;
    }
    unlock();
    return rc;
}

extern "C" mhfu_hook_rc_t mhfu_hook_detour(uint32_t addr, uint32_t expect0, uint32_t expect1,
                                           mhfu_wrap_fn pre, const char *owner)
{
    if (!owner || !pre || (addr & 3) || is_control(expect0) || is_control(expect1))
        return MHFU_HOOK_BADARG;
    lock();
    mhfu_hook_rc_t rc = logged(check_free(addr, 2, owner), addr, owner);
    if (rc == MHFU_HOOK_OK) {
        mhfu_wrap_t w;
        detour_wrap(&w, addr, expect0, expect1, pre);
        uint32_t wr = wrapper_for(addr, &w);
        const uint32_t expect[2] = {expect0, expect1}, word[2] = {mips_j(wr), MIPS_NOP};
        rc = wr ? queue(addr, 2, expect, word, owner) : MHFU_HOOK_NOSPACE;
    }
    unlock();
    return rc;
}

extern "C" void mhfu_hook_release(const char *owner)
{
    if (!owner) return;
    lock();
    for (int i = 0; i < MAX_QUEUED; i++)
        if (strncmp(g_queue[i].owner, owner, OWNER_LEN - 1) == 0) g_queue[i].pending = 0;
    for (int i = 0; i < MAX_HOOKS; i++)
        if (g_hooks[i].kind != HK_FREE && strncmp(g_hooks[i].owner, owner, OWNER_LEN - 1) == 0)
            restore(&g_hooks[i]);
    unlock();
}

/* --- internal.h ----------------------------------------------------------------------- */

extern "C" mhfu_hook_rc_t mhfu_hook_detour_now(uint32_t addr, uint32_t expect0,
                                               uint32_t expect1, mhfu_wrap_fn pre,
                                               const char *owner)
{
    if (!owner || !pre || (addr & 3) || is_control(expect0) || is_control(expect1))
        return MHFU_HOOK_BADARG;
    lock();
    mhfu_hook_rc_t rc = logged(check_free(addr, 2, owner), addr, owner);
    if (rc == MHFU_HOOK_OK) {
        mhfu_wrap_t w;
        detour_wrap(&w, addr, expect0, expect1, pre);
        uint32_t built = wrapper_find(addr, &w);
        uint32_t w0 = WORD(addr), w1 = WORD(addr + 4);
        if (built && w0 == mips_j(built)) {
            rc = MHFU_HOOK_OK;                                    /* still ours */
        } else if (w0 == expect0 && w1 == expect1) {
            uint32_t wr = wrapper_for(addr, &w);
            const uint32_t word[2] = {mips_j(wr), MIPS_NOP};
            rc = wr ? logged(patch_now(addr, word, 2, owner), addr, owner) : MHFU_HOOK_NOSPACE;
        } else {
            rc = MHFU_HOOK_BADARG;
            int seen = 0, slot = -1;
            for (int i = 0; i < MAX_WRAPPERS; i++) {
                if (g_mismatch_logged[i] == addr) seen = 1;
                if (!g_mismatch_logged[i] && slot < 0) slot = i;
            }
            if (!seen) {
                mhfu_log("[hook] '%s' detour @0x%08lx: found 0x%08lx 0x%08lx, expected "
                         "0x%08lx 0x%08lx", owner, (unsigned long)addr, (unsigned long)w0,
                         (unsigned long)w1, (unsigned long)expect0, (unsigned long)expect1);
                if (slot >= 0) g_mismatch_logged[slot] = addr;
            }
        }
    }
    unlock();
    return rc;
}

extern "C" void mhfu_hook_release_all(void)
{
    lock();
    memset(g_queue, 0, sizeof(g_queue));
    for (int i = 0; i < MAX_HOOKS; i++)
        if (g_hooks[i].kind != HK_FREE) restore(&g_hooks[i]);
    unlock();
}
