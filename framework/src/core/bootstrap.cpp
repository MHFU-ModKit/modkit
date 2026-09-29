/* mhfu_framework.prx entry: module_start spawns the framework thread, which brings up the
 * hook manager, poll threads, mods and the quest-event anchors. */
#include <pspkernel.h>
#include <pspsdk.h>
#include <pspthreadman.h>
#include <pspsysmem.h>

#include "mhfu/log.h"
#include "internal.h"
#include "addresses.gen.h"

#define MOD_NAME "mhfu_framework"

/* Linker bounds of this module's loaded image: load base and first byte past .bss. */
extern "C" char _ftext[];
extern "C" char _end[];

/* PPSSPP's plugin loader leaves our image marked free, so the engine's big-monster construction
 * thread can get a stack inside the PRX and overwrite it; reserve our own range first. */
static void reserve_self_memory(void)
{
    uint32_t base = ((uint32_t)(uintptr_t)_ftext) & ~0xFFFu;          /* page down */
    uint32_t end  = (((uint32_t)(uintptr_t)_end) + 0xFFFu) & ~0xFFFu; /* page up   */
    uint32_t size = end - base;
    SceUID guard = sceKernelAllocPartitionMemory(
        PSP_MEMORY_PARTITION_USER, "mhfu_self_guard", PSP_SMEM_Addr, size, (void *)base);
    if (guard >= 0) {
        void *got = sceKernelGetBlockHeadAddr(guard);
        mhfu_log("[framework] self-guard reserved [0x%08X,0x%08X) %uKB blk=0x%X got=0x%08X",
                 (unsigned)base, (unsigned)end, (unsigned)(size / 1024), (unsigned)guard,
                 (unsigned)(uintptr_t)got);
    } else {
        mhfu_log("[framework] self-guard FAILED rc=0x%08X for [0x%08X,0x%08X) — narrow fallback",
                 (unsigned)guard, (unsigned)base, (unsigned)end);
        /* fallback: reserve only the zone where that stack meets our import stubs */
        SceUID b2 = sceKernelAllocPartitionMemory(
            PSP_MEMORY_PARTITION_USER, "mhfu_zone_guard", PSP_SMEM_Addr,
            0x4000, (void *)MHFU_PRX_STACK_GUARD);
        mhfu_log("[framework] narrow self-guard [0x%08X,+0x4000) rc/blk=0x%X",
                 (unsigned)MHFU_PRX_STACK_GUARD, (unsigned)b2);
    }
}

PSP_MODULE_INFO(MOD_NAME, 0, 1, 1);
PSP_MAIN_THREAD_ATTR(0);
/* Caps the newlib heap for incidental malloc; Lua runs on its own slab. */
PSP_HEAP_SIZE_KB(256);

static void start_thread(const char *name, int (*entry)(SceSize, void *))
{
    SceUID th = sceKernelCreateThread(name, (SceKernelThreadEntry)entry,
                                      0x18, 0x1000, 0, NULL);
    if (th >= 0) {
        sceKernelStartThread(th, 0, NULL);
        mhfu_log("[framework] thread '%s' started (uid=0x%08lx)",
                 name, (unsigned long)th);
    } else {
        mhfu_log("[framework] thread '%s' create failed: %d", name, th);
    }
}

/* The boot sequence, on its own thread. No crt0 main or heap setup runs at load: in MHFU's
 * full partition it fails with 0x800200D9 before any log. */
static int framework_main(SceSize args, void *argp)
{
    (void)args; (void)argp;
    mhfu_log("[framework] %s starting", MOD_NAME);

    /* first, before the game can place a thread stack inside our image */
    reserve_self_memory();

    /* before any model registers */
    mhfu_xram_init();
    mhfu_hook_init();
    mhfu_em_init();
    mhfu_joint_fix_init();

    start_thread("mhfu_spawn_poll", mhfu_event_spawn_poll_thread);
    start_thread("mhfu_deferred", mhfu_hook_deferred_thread);

    /* each mod registers its events and hooks and starts its threads in init() */
    mhfu_mod_init_all();

    mhfu_log("[framework] ready");

    /* installs the quest-event anchors, then watches them for good */
    mhfu_event_install_thread(0, NULL);

    for (;;) sceKernelDelayThread(1000 * 1000);
    return 0;
}

/* Runs on the kernel loader thread (tiny stack, FPU off): no I/O here. Spawn the framework
 * thread, with a 256 KB stack for the Lua VM setup, and return at once. */
extern "C" int module_start(SceSize args, void *argp)
{
    SceUID th = sceKernelCreateThread("mhfu_framework", framework_main, 0x18,
                                      0x40000, THREAD_ATTR_USER, NULL);
    if (th >= 0) {
        sceKernelStartThread(th, args, argp);
        return 0;
    }
    return th;
}

/* newlib references _exit; the framework never exits. */
extern "C" void _exit(int status) { (void)status; for (;;) sceKernelDelayThread(1000000); }

extern "C" int module_stop(SceSize args, void *argp)
{
    (void)args; (void)argp;
    mhfu_mod_shutdown_all();
    mhfu_hook_release_all();   /* the anchors, detours and core patches too */
    mhfu_log_close();
    return 0;
}
