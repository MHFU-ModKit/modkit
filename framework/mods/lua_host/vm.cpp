/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* The Lua VM: its slab allocator, the lock every entry takes and the panic handler. */
#include <pspthreadman.h>
#include <pspsysmem.h>

#include "mhfu/mhfu.h"
#include "lua_host.h"
#include "tlsf.h"

/* ------------------------------------------------------------------ slab
 * TLSF (third_party/tlsf) over one fixed slab, so Lua never competes with the game for heap.
 *
 * The size is a band: a bigger slab starves the section asset load and the game exits; a
 * smaller one fails to compile a mod at boot. The slab holds what the 96 KB script buffer held
 * before scripts streamed (scripts.cpp), so partition 2 holds what it did with a 256 KB slab.
 * Size it off the live and peak that `VM ready` logs. */
#define LUA_SLAB_BYTES (350 * 1024)

static tlsf_t    g_tlsf;
static SceUID    g_slab_uid = -1;
static unsigned  g_live;           /* bytes in Lua's blocks */
unsigned         mhfu_lua_slab_peak;

unsigned mhfu_lua_slab_live(void) { return g_live; }

static void note_largest(void *ptr, size_t size, int used, void *user)
{
    (void)ptr;
    unsigned *largest = (unsigned *)user;
    if (!used && size > *largest) *largest = (unsigned)size;
}

unsigned mhfu_lua_slab_largest(void)
{
    unsigned largest = 0;
    if (g_tlsf) tlsf_walk_pool(tlsf_get_pool(g_tlsf), note_largest, &largest);
    return largest;
}

static void *lua_slab_alloc(void *ud, void *ptr, size_t osize, size_t nsize)
{
    (void)ud; (void)osize;
    if (ptr) g_live -= (unsigned)tlsf_block_size(ptr);
    if (nsize == 0) { tlsf_free(g_tlsf, ptr); return 0; }
    void *p = tlsf_realloc(g_tlsf, ptr, nsize);
    if (p || ptr) g_live += (unsigned)tlsf_block_size(p ? p : ptr);   /* a failed grow keeps ptr */
    if (g_live > mhfu_lua_slab_peak) mhfu_lua_slab_peak = g_live;
    return p;
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
    g_tlsf = tlsf_create_with_pool((void *)(((unsigned)base + 7u) & ~7u), LUA_SLAB_BYTES);
    if (!g_tlsf) { mhfu_log("[lua_host] tlsf_create_with_pool FAILED"); return -1; }
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
    g_tlsf = 0;
    if (g_slab_uid >= 0) { sceKernelFreePartitionMemory(g_slab_uid); g_slab_uid = -1; }
    if (g_lua_sema >= 0) { sceKernelDeleteSema(g_lua_sema); g_lua_sema = -1; }
}
