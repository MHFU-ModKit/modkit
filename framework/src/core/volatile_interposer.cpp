/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/*
 * Real-PSP volatile interposer: wraps the game's volatile Lock/Unlock import stubs and
 * carves the top of the game's own lock for the relocate PACs.
 */
#include "xram.h"
#include "mhfu/log.h"
#include "mhfu/world.h"        /* ms0 gate, screen state, area index */
#include "mhfu/mips.h"
#include "mhfu/hooks.h"        /* mhfu_hook_word */
#include "internal.h"
#include "addresses.gen.h"

#include <pspiofilemgr.h>
#include <psputils.h>          /* sceKernelDcacheWritebackRange */
#include <pspsuspend.h>        /* sceKernelVolatileMemLock */

/* The game calls the blocking Lock, not TryLock. Wrappers log each call to a ring the
 * poll thread drains, since the calls come mid-load where ms0 I/O is unsafe. */
typedef struct { uint8_t kind; uint8_t scr; uint16_t area;
                 uint32_t a0, ptr, size; int32_t rc; uint32_t seq; } vol_evt_t;
#define VOL_RING 32
static vol_evt_t        g_vol_ring[VOL_RING];
static volatile uint32_t g_vol_head, g_vol_tail;   /* game thread pushes, poll drains */
static uint32_t          g_vol_seq;
static int               g_vol_installed;

static const char k_owner[] = "mhfu_vol";

static void vol_push(uint8_t kind, uint32_t a0, uint32_t ptr, uint32_t size, int rc)
{
    uint32_t h = g_vol_head;
    vol_evt_t *e = &g_vol_ring[h % VOL_RING];
    e->kind = kind; e->a0 = a0; e->ptr = ptr; e->size = size; e->rc = rc;
    e->scr = mhfu_world_screen_state(); e->area = mhfu_world_area_index(); e->seq = ++g_vol_seq;
    g_vol_head = h + 1;                              /* publish after fill */
}

/* The game locks the whole partition once at quest depart and never unlocks. Right
 * after its Lock returns (held, on the game thread) we stage the relocate PACs into the
 * top and hand the game a smaller size. Touch volatile only inside that held window. */
static int      g_proxy_enabled = 1;
static uint32_t g_proxy_reserved;       /* bytes carved off the top, 0 = transparent */
static int      g_proxy_done;

/* Read a PAC into [dst, dst+cap); its size, 0 on failure. */
static uint32_t proxy_read_pac(const char *path, uint32_t dst, uint32_t cap)
{
    SceUID fd = sceIoOpen(path, PSP_O_RDONLY, 0);
    if (fd < 0) { mhfu_xram_log("[proxy] open FAILED %s rc=0x%08X", path, (unsigned)fd); return 0; }
    SceOff sz = sceIoLseek(fd, 0, PSP_SEEK_END);
    sceIoLseek(fd, 0, PSP_SEEK_SET);
    uint32_t fsz = (uint32_t)sz;
    if (fsz < 0x40 || fsz > cap) {
        sceIoClose(fd);
        mhfu_xram_log("[proxy] %s size=%uB > cap=%uB -> skip", path, (unsigned)fsz, (unsigned)cap);
        return 0;
    }
    int rd = sceIoRead(fd, (void *)dst, (int)fsz);
    sceIoClose(fd);
    if (rd != (int)fsz) { mhfu_xram_log("[proxy] read short rc=0x%08X", (unsigned)rd); return 0; }
    sceKernelDcacheWritebackRange((void *)dst, fsz);
    return fsz;
}

/* Stage unstaged relocate entries into the top of the lock and point their buf there.
 * Returns the bytes reserved, 0 to stay transparent. */
static uint32_t proxy_stage(uint32_t lock_base, uint32_t lock_size)
{
    if (!g_proxy_enabled || g_proxy_done) return g_proxy_reserved;
    uint32_t reserve = 0;                          /* grown sizes, 16 KB-aligned */
    for (int i = 0; i < MHFU_INJECT_MAX; i++) {
        inject_entry_t *e = &mhfu_inject_tab[i];
        if (!e->used || !e->relocate || e->ready) continue;
        SceUID fd = sceIoOpen(e->path, PSP_O_RDONLY, 0);
        if (fd < 0) continue;
        uint32_t fsz = (uint32_t)sceIoLseek(fd, 0, PSP_SEEK_END);
        sceIoClose(fd);
        reserve += (fsz + 0x3FFFu) & ~0x3FFFu;
    }
    if (reserve == 0) return 0;
    if (reserve > lock_size / 2) {                 /* never take more than half */
        mhfu_xram_log("[proxy] reserve=%uKB too big for %uKB -> stay transparent",
                      (unsigned)(reserve / 1024), (unsigned)(lock_size / 1024));
        g_proxy_done = 1;
        return 0;
    }
    uint32_t bump = lock_base + lock_size - reserve;
    mhfu_xram_log("[proxy] reserve=%uKB top=[0x%08X,0x%08X) game gets [0x%08X,0x%08X)",
                  (unsigned)(reserve / 1024), (unsigned)bump, (unsigned)(lock_base + lock_size),
                  (unsigned)lock_base, (unsigned)bump);
    for (int i = 0; i < MHFU_INJECT_MAX; i++) {
        inject_entry_t *e = &mhfu_inject_tab[i];
        if (!e->used || !e->relocate || e->ready) continue;
        uint32_t cap = (lock_base + lock_size) - bump;
        uint32_t fsz = proxy_read_pac(e->path, bump, cap);
        if (!fsz) continue;
        mhfu_inject_relocate_ready(e, bump, (fsz + 0x3FFFu) & ~0x3FFFu, fsz);
        mhfu_xram_log("[proxy] staged file=%u grown=%uB @0x%08X (volatile top)",
                      (unsigned)e->file_id, (unsigned)fsz, (unsigned)bump);
        bump += e->buf_cap;
    }
    g_proxy_reserved = reserve;
    g_proxy_done = 1;
    return reserve;
}

/* Same ABI as the stubs, so the return lands in the game caller. Game thread, maybe
 * mid-load: log only through the ring. */
extern "C" int mhfu_vol_lock(int unk, void **pptr, int *psize)
{
    int rc = sceKernelVolatileMemLock(unk, pptr, psize);
    if (rc >= 0 && pptr && psize) {
        uint32_t base = (uint32_t)(uintptr_t)*pptr;
        uint32_t full = (uint32_t)*psize;
        uint32_t reserve = proxy_stage(base, full);
        if (reserve) {
            *psize = full - reserve;                         /* the game gets the bottom */
            vol_push(3, (uint32_t)unk, base, *psize, rc);   /* 3 = shrunk */
        } else {
            vol_push(1, (uint32_t)unk, base, full, rc);
        }
    } else {
        vol_push(1, (uint32_t)unk, 0, 0, rc);
    }
    return rc;
}
extern "C" int mhfu_vol_unlock(int unk)
{
    int rc = sceKernelVolatileMemUnlock(unk);
    vol_push(2, (uint32_t)unk, 0, 0, rc);
    return rc;
}

/* Real PSP only, idempotent. The delay slot (the resolved syscall) becomes a NOP before
 * the jump goes in, so the stub is never a jump plus a stale syscall. */
extern "C" void mhfu_vol_install(void)
{
    if (g_vol_installed) return;
    if (!mhfu_xram_mode) mhfu_xram_init();
    if (mhfu_xram_mode != XRAM_VOLATILE) return;
    mhfu_hook_word(MHFU_VOLATILE_LOCK   + 4, MIPS_NOP, k_owner);
    mhfu_hook_word(MHFU_VOLATILE_LOCK,       mips_j((uint32_t)(uintptr_t)&mhfu_vol_lock),   k_owner);
    mhfu_hook_word(MHFU_VOLATILE_UNLOCK + 4, MIPS_NOP, k_owner);
    mhfu_hook_word(MHFU_VOLATILE_UNLOCK,     mips_j((uint32_t)(uintptr_t)&mhfu_vol_unlock), k_owner);
    g_vol_installed = 1;
    mhfu_xram_log("[vobs] installed: Lock stub 0x%08X -> 0x%08X, Unlock stub 0x%08X -> 0x%08X",
                  (unsigned)MHFU_VOLATILE_LOCK,   (unsigned)(uintptr_t)&mhfu_vol_lock,
                  (unsigned)MHFU_VOLATILE_UNLOCK, (unsigned)(uintptr_t)&mhfu_vol_unlock);
}

/* Poll thread: drain the ring once ms0 is safe, so calls made during a load are kept. */
extern "C" void mhfu_vol_flush(void)
{
    if (!g_vol_installed || !mhfu_world_ms0_io_safe()) return;
    while (g_vol_tail != g_vol_head) {
        vol_evt_t *e = &g_vol_ring[g_vol_tail % VOL_RING];
        if (e->kind == 1)
            mhfu_xram_log("[vobs] #%u LOCK(unk=%u) -> rc=0x%08X ptr=0x%08X size=%uKB @scr=%u area=%u",
                          (unsigned)e->seq, (unsigned)e->a0, (unsigned)e->rc, (unsigned)e->ptr,
                          (unsigned)(e->size / 1024), (unsigned)e->scr, (unsigned)e->area);
        else if (e->kind == 3)
            mhfu_xram_log("[vobs] #%u LOCK SHRUNK(unk=%u) -> game gets base=0x%08X size=%uKB "
                          "(we reserved %uKB top) @scr=%u area=%u",
                          (unsigned)e->seq, (unsigned)e->a0, (unsigned)e->ptr,
                          (unsigned)(e->size / 1024), (unsigned)(g_proxy_reserved / 1024),
                          (unsigned)e->scr, (unsigned)e->area);
        else
            mhfu_xram_log("[vobs] #%u UNLOCK(unk=%u) -> rc=0x%08X @scr=%u area=%u",
                          (unsigned)e->seq, (unsigned)e->a0, (unsigned)e->rc,
                          (unsigned)e->scr, (unsigned)e->area);
        g_vol_tail++;
    }
}
