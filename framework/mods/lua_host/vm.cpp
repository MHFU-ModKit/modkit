/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* The Lua VM: its slab allocator, the lock every entry takes and the panic handler. */
#include <pspthreadman.h>
#include <pspsysmem.h>

#include "mhfu/mhfu.h"
#include "lua_host.h"

/* ------------------------------------------------------------------ slab
 * An implicit-free-list allocator over one fixed slab (malloc/free/realloc,
 * forward coalescing), so Lua never competes with the game for heap.
 *
 * The size is a band: a bigger slab starves the section asset load and the game
 * exits; a smaller one fails to compile a mod at boot, before the log reaches
 * framework.log. Size it off the live and peak that `VM ready` logs. */
#define LUA_SLAB_BYTES (256 * 1024)

typedef struct blk_hdr { unsigned size; unsigned free; } blk_hdr; /* 8 bytes */

static char     *g_slab;          /* slab base (8-byte aligned)      */
static unsigned  g_slab_bytes;
static SceUID    g_slab_uid = -1;
unsigned         mhfu_lua_slab_peak;

static unsigned align8(unsigned n) { return (n + 7u) & ~7u; }

static void slab_init(void *base, unsigned bytes)
{
    g_slab = (char *)base;
    g_slab_bytes = bytes;
    blk_hdr *h = (blk_hdr *)g_slab;
    h->size = bytes;          /* whole slab = one free block (incl hdr) */
    h->free = 1;
}

unsigned mhfu_lua_slab_live(void)
{
    unsigned used = 0, off = 0;
    while (off + sizeof(blk_hdr) <= g_slab_bytes) {
        blk_hdr *h = (blk_hdr *)(g_slab + off);
        if (h->size == 0) break;
        if (!h->free) used += h->size;
        off += h->size;
    }
    return used;
}

static void slab_coalesce(void)
{
    unsigned off = 0;
    while (off + sizeof(blk_hdr) <= g_slab_bytes) {
        blk_hdr *h = (blk_hdr *)(g_slab + off);
        if (h->size == 0) break;
        if (h->free) {
            unsigned noff = off + h->size;
            while (noff + sizeof(blk_hdr) <= g_slab_bytes) {
                blk_hdr *n = (blk_hdr *)(g_slab + noff);
                if (n->size == 0 || !n->free) break;
                h->size += n->size;            /* merge */
                noff += n->size;
            }
        }
        off += h->size;
    }
}

static void *slab_malloc(unsigned n)
{
    unsigned need = align8(n) + sizeof(blk_hdr);
    if (need < sizeof(blk_hdr) + 8) need = sizeof(blk_hdr) + 8;
    unsigned off = 0;
    while (off + sizeof(blk_hdr) <= g_slab_bytes) {
        blk_hdr *h = (blk_hdr *)(g_slab + off);
        if (h->size == 0) break;
        if (h->free && h->size >= need) {
            unsigned rem = h->size - need;
            if (rem >= sizeof(blk_hdr) + 8) {     /* split */
                h->size = need;
                blk_hdr *s = (blk_hdr *)(g_slab + off + need);
                s->size = rem;
                s->free = 1;
            }
            h->free = 0;
            unsigned live = mhfu_lua_slab_live();
            if (live > mhfu_lua_slab_peak) mhfu_lua_slab_peak = live;
            return (char *)h + sizeof(blk_hdr);
        }
        off += h->size;
    }
    return 0; /* OOM */
}

static void slab_free(void *p)
{
    if (!p) return;
    blk_hdr *h = (blk_hdr *)((char *)p - sizeof(blk_hdr));
    h->free = 1;
    slab_coalesce();
}

static unsigned slab_payload(void *p)
{
    blk_hdr *h = (blk_hdr *)((char *)p - sizeof(blk_hdr));
    return h->size - sizeof(blk_hdr);
}

static void *slab_realloc(void *p, unsigned n)
{
    if (!p) return slab_malloc(n);
    unsigned cur = slab_payload(p);
    if (n <= cur) return p;                 /* shrink/keep in place */
    void *np = slab_malloc(n);
    if (!np) return 0;
    unsigned copy = cur < n ? cur : n;
    for (unsigned i = 0; i < copy; i++) ((char *)np)[i] = ((char *)p)[i];
    slab_free(p);
    return np;
}

static void *lua_slab_alloc(void *ud, void *ptr, size_t osize, size_t nsize)
{
    (void)ud; (void)osize;
    if (nsize == 0) { slab_free(ptr); return 0; }
    if (!ptr)       return slab_malloc((unsigned)nsize);
    return slab_realloc(ptr, (unsigned)nsize);
}

/* ------------------------------------------------------------------ VM */

lua_State           *mhfu_lua_vm;
volatile int         mhfu_lua_have_tick;
static volatile int  g_paniced;     /* set by panic handler          */
volatile int         mhfu_lua_ready;
static SceUID        g_lua_sema = -1;
static volatile int  g_holder = -1;   /* the thread inside the VM */

/* The VM is not reentrant: the exec, poll and worker threads all take this lock. */
int mhfu_lua_enter(void)
{
    if (!mhfu_lua_vm || g_paniced || !mhfu_lua_ready) return 0;
    sceKernelWaitSema(g_lua_sema, 1, 0);
    if (g_paniced) { sceKernelSignalSema(g_lua_sema, 1); return 0; }
    g_holder = sceKernelGetThreadId();
    return 1;
}
void mhfu_lua_leave(void)
{
    g_holder = -1;
    sceKernelSignalSema(g_lua_sema, 1);
}

int mhfu_lua_vm_held(void) { return g_holder >= 0 && g_holder == sceKernelGetThreadId(); }

/* Lua's default panic aborts, which exits the game: release the lock and answer a blocked
 * game thread so no waiter hangs, then park this thread. */
static int lua_host_panic(lua_State *L)
{
    const char *msg = lua_type(L, -1) == LUA_TSTRING ? lua_tostring(L, -1) : 0;
    mhfu_log("[lua_host] PANIC (VM disabled, game kept alive): %s",
             msg ? msg : "?");
    g_paniced = 1;
    g_holder = -1;
    mhfu_lua_exec_panic();
    if (g_lua_sema >= 0) sceKernelSignalSema(g_lua_sema, 1);
    for (;;) sceKernelDelayThread(1000 * 1000);
    return 0; /* unreachable */
}

int mhfu_lua_vm_init(void)
{
    mhfu_log("[lua_host] free mem before slab: max=%u total=%u",
             (unsigned)sceKernelMaxFreeMemSize(),
             (unsigned)sceKernelTotalFreeMemSize());

    g_lua_sema = sceKernelCreateSema("mhfu_lua_lock", 0, 1, 1, 0);
    if (g_lua_sema < 0) { mhfu_log("[lua_host] CreateSema FAILED rc=0x%08X", g_lua_sema); return -1; }

    g_slab_uid = sceKernelAllocPartitionMemory(
        2 /* PSP_MEMORY_PARTITION_USER */, "mhfu_lua_slab",
        PSP_SMEM_Low, LUA_SLAB_BYTES + 64, 0);
    if (g_slab_uid < 0) {
        mhfu_log("[lua_host] AllocPartitionMemory FAILED rc=0x%08X", g_slab_uid);
        return -1;
    }
    mhfu_log("[lua_host] free mem after %uKB slab: max=%u total=%u",
             (unsigned)(LUA_SLAB_BYTES / 1024),
             (unsigned)sceKernelMaxFreeMemSize(),
             (unsigned)sceKernelTotalFreeMemSize());
    void *base = sceKernelGetBlockHeadAddr(g_slab_uid);
    unsigned a = ((unsigned)base + 7u) & ~7u;
    slab_init((void *)a, LUA_SLAB_BYTES);
    return 0;
}

int mhfu_lua_vm_open(void)
{
    mhfu_lua_vm = lua_newstate(lua_slab_alloc, 0);
    if (!mhfu_lua_vm) { mhfu_log("[lua_host] lua_newstate FAILED (slab OOM?)"); return -1; }
    lua_atpanic(mhfu_lua_vm, lua_host_panic);
    return 0;
}

void mhfu_lua_vm_close(void)
{
    if (mhfu_lua_vm) { lua_close(mhfu_lua_vm); mhfu_lua_vm = 0; }
    if (g_slab_uid >= 0) { sceKernelFreePartitionMemory(g_slab_uid); g_slab_uid = -1; }
    if (g_lua_sema >= 0) { sceKernelDeleteSema(g_lua_sema); g_lua_sema = -1; }
}
