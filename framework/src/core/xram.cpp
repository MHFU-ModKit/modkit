/*
 * Extra RAM for injected files: PPSSPP's raw memory=64 window, or on a real PSP the
 * 4 MB volatile partition. Also the real-PSP diagnostics log both inject files use.
 */
#include "xram.h"
#include "mhfu/log.h"
#include "mhfu/world.h"        /* mhfu_world_ms0_io_safe */
#include "internal.h"
#include "addresses.gen.h"

#include <pspiofilemgr.h>
#include <string.h>
#include <stdio.h>             /* vsnprintf */
#include <pspsysmem.h>         /* sceKernelMaxFreeMemSize */
#include <pspsuspend.h>        /* sceKernelVolatileMemTryLock */
#include <stdarg.h>

static uint32_t g_xram_bump = MHFU_INJECT_XRAM;   /* raw-mode bump */

void mhfu_xram_log(const char *fmt, ...)
{
    /* never during savedata: the memory-stick driver is not reentrant */
    if (!mhfu_world_ms0_io_safe()) return;
    char line[224];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(line, sizeof(line) - 2, fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if (n > (int)sizeof(line) - 2) n = (int)sizeof(line) - 2;
    line[n++] = '\n';
    line[n] = 0;
    SceUID fd = sceIoOpen("ms0:/PSP/mhfu_brute_debug.txt",
                          PSP_O_WRONLY | PSP_O_CREAT | PSP_O_APPEND, 0777);
    if (fd >= 0) { sceIoWrite(fd, line, n); sceIoClose(fd); }
}

/* Real PSP: xram is the volatile partition, never a grown user partition
 * (CFW "Use Extra Memory: Forced" crashes MHFU at boot). Holding the lock can block
 * the game's own volatile users (savedata, utility dialogs). */
int             mhfu_xram_mode;
static uint32_t g_vol_base, g_vol_size, g_vol_bump;   /* our volatile lock */
static int      g_vol_locked;                         /* 0 never tried, 1 held, -1 last try failed */

/* Write-probe a word. Faults on a real PSP, so call only behind the maxfree gate. */
static int xram_raw_probe(uint32_t addr)
{
    volatile uint32_t *p = (volatile uint32_t *)addr;
    uint32_t save = *p; *p = 0xA5C30F04u;
    int ok = (*p == 0xA5C30F04u); *p = save;
    return ok;
}

/* More than 32 MB free happens only under PPSSPP memory=64, so the raw probe runs
 * only there. Volatile is not locked here, only on first alloc. */
extern "C" void mhfu_xram_init(void)
{
    if (mhfu_xram_mode) return;
    uint32_t maxfree = (uint32_t)sceKernelMaxFreeMemSize();
    mhfu_xram_log("[xram] platform decide: maxfree=%uKB totalfree=%uKB",
                  (unsigned)(maxfree / 1024),
                  (unsigned)(sceKernelTotalFreeMemSize() / 1024));
    if (maxfree > 0x02000000u && xram_raw_probe(MHFU_INJECT_XRAM)) {
        mhfu_xram_mode = XRAM_RAW;
        g_xram_bump = MHFU_INJECT_XRAM;
        mhfu_log("[inject] xram = RAW window 0x%08X (emulator, %uMB free)",
                 MHFU_INJECT_XRAM, (unsigned)(maxfree / (1024 * 1024)));
        mhfu_xram_log("[xram] mode=RAW 0x%08X (emulator)", MHFU_INJECT_XRAM);
    } else {
        mhfu_xram_mode = XRAM_VOLATILE;
        mhfu_log("[inject] xram = VOLATILE 4MB (hardware-safe; locked on first use)");
        mhfu_xram_log("[xram] mode=VOLATILE (hardware; lock deferred)");
    }
}

/* Lock the volatile partition. Only success is cached; a busy lock is retried next call. */
static int volatile_ensure(void)
{
    if (g_vol_locked > 0) return 1;
    void *ptr = 0; int size = 0;
    int rc = sceKernelVolatileMemTryLock(0, &ptr, &size);
    if (rc < 0 || !ptr || size <= 0) {
        if (g_vol_locked != -1)
            mhfu_xram_log("[xram] VOLATILE TryLock busy rc=0x%08X (game holds it) -> retry",
                          (unsigned)rc);
        g_vol_locked = -1;
        return 0;
    }
    g_vol_base = (uint32_t)(uintptr_t)ptr;
    g_vol_size = (uint32_t)size;
    g_vol_bump = g_vol_base;
    g_vol_locked = 1;
    mhfu_xram_log("[xram] VOLATILE lock ok base=0x%08X size=%uKB",
                  (unsigned)g_vol_base, (unsigned)(g_vol_size / 1024));
    return 1;
}

extern "C" uint32_t mhfu_xram_alloc(uint32_t n)
{
    if (!mhfu_xram_mode) mhfu_xram_init();

    if (mhfu_xram_mode == XRAM_RAW) {
        uint32_t a = (g_xram_bump + 15u) & ~15u;
        if (a + n > MHFU_EXTRA_RAM_END) return 0;
        if (!xram_raw_probe(a)) return 0;
        g_xram_bump = a + n;
        return a;
    }
    /* volatile, never the game heap: a high partition-2 alloc lands inside it and
     * runs the game out of memory mid-quest */
    if (!volatile_ensure()) return 0;
    uint32_t a = (g_vol_bump + 15u) & ~15u;
    if (a + n > g_vol_base + g_vol_size) {
        mhfu_xram_log("[xram] VOLATILE exhausted need=%uKB used=%uKB cap=%uKB",
                      (unsigned)(n / 1024), (unsigned)((g_vol_bump - g_vol_base) / 1024),
                      (unsigned)(g_vol_size / 1024));
        return 0;
    }
    g_vol_bump = a + n;
    mhfu_xram_log("[xram] VOLATILE alloc need=%uKB addr=0x%08X (used=%uKB/%uKB)",
                  (unsigned)(n / 1024), (unsigned)a,
                  (unsigned)((g_vol_bump - g_vol_base) / 1024), (unsigned)(g_vol_size / 1024));
    return a;
}

/* Quest exit: unlock our volatile so the reward save does not freeze. The entries
 * staged in it are dropped and staged again next quest. */
extern "C" void mhfu_xram_release(void)
{
    if (mhfu_xram_mode != XRAM_VOLATILE || g_vol_locked <= 0) return;
    for (int i = 0; i < MHFU_INJECT_MAX; i++) {
        inject_entry_t *e = &mhfu_inject_tab[i];
        if (e->used && e->relocate) mhfu_inject_relocate_drop(e);
    }
    g_vol_base = g_vol_size = g_vol_bump = 0;
    sceKernelVolatileMemUnlock(0);
    g_vol_locked = 0;
    mhfu_xram_log("[xram] VOLATILE released (quest exit) -> save-safe; re-stage next quest");
    mhfu_log("[inject] volatile released on quest exit (save-safe)");
}
