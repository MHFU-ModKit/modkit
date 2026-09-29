/*
 * Extra RAM for injected files: PPSSPP's raw memory=64 window, or on a real PSP the
 * 4 MB volatile partition. Also the real-PSP diagnostics log both inject files use.
 */
#include "xram.h"
#include "mhfu/log.h"
#include "mhfu/world.h"        /* mhfu_world_ms0_io_safe, mhfu_world_area_index */
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
static int      g_prelock_armed;                      /* nothing sets it: prelock is inert */

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

/* Poll thread, 10 Hz: once armed, stage every relocate entry; 0 = volatile busy, retry. */
extern "C" int mhfu_xram_prelock(void)
{
    if (!g_prelock_armed) return 1;
    if (!mhfu_xram_mode) mhfu_xram_init();
    if (mhfu_xram_mode != XRAM_VOLATILE) return 1;
    static uint32_t s_throttle = 0;                 /* retry at 1 Hz, not every tick */
    if ((s_throttle++ % 10) != 0) return 0;
    int all = 1;
    for (int i = 0; i < MHFU_INJECT_MAX; i++) {
        inject_entry_t *e = &mhfu_inject_tab[i];
        if (!e->used || !e->relocate || e->buf) continue;
        e->staged = 0;                              /* allow a retry */
        if (!mhfu_inject_stage_relocate(e)) all = 0;
    }
    return all;
}

/* Quest exit: unlock our volatile so the reward save does not freeze. The entries
 * staged in it are dropped and staged again next quest. */
extern "C" void mhfu_xram_release(void)
{
    g_prelock_armed = 0;
    if (mhfu_xram_mode != XRAM_VOLATILE || g_vol_locked <= 0) return;
    for (int i = 0; i < MHFU_INJECT_MAX; i++) {
        inject_entry_t *e = &mhfu_inject_tab[i];
        if (e->used && e->relocate) {
            e->buf = 0; e->buf_cap = 0; e->staged = 0; e->redirects = 0;
        }
    }
    g_vol_base = g_vol_size = g_vol_bump = 0;
    sceKernelVolatileMemUnlock(0);
    g_vol_locked = 0;
    mhfu_xram_log("[xram] VOLATILE released (quest exit) -> save-safe; re-stage next quest");
    mhfu_log("[inject] volatile released on quest exit (save-safe)");
}

/* Recon probe (compiled off): does the game need volatile during an in-quest section
 * load? Lock at quest depart, hold across the load, release on quest exit, menu or a
 * 15 s timeout; the log says whether the section loaded while held. Holding would
 * deadlock the game's blocking Lock, which is why it stays off. */
static int      g_recon_enabled = 0;
static int      g_recon_held;
static uint32_t g_recon_base, g_recon_size;
static int      g_recon_arm_log;           /* 0 none, 1 acquired, 2 busy; the poll logs it */
static uint32_t g_recon_arm_rc;

/* Quest-commit thread: non-blocking TryLock only, no ms0 I/O here. */
extern "C" void mhfu_xram_recon_arm(void)
{
    if (!g_recon_enabled || g_recon_held) return;
    if (!mhfu_xram_mode) mhfu_xram_init();
    if (mhfu_xram_mode != XRAM_VOLATILE) return;
    void *ptr = 0; int size = 0;
    int rc = sceKernelVolatileMemTryLock(0, &ptr, &size);
    if (rc >= 0 && ptr && size > 0) {
        g_recon_held = 1;
        g_recon_base = (uint32_t)(uintptr_t)ptr;
        g_recon_size = (uint32_t)size;
        g_recon_arm_log = 1;
    } else {
        g_recon_arm_log = 2;
        g_recon_arm_rc  = (uint32_t)rc;
    }
}

/* Poll thread, 10 Hz: log the acquire, release when due, probe the village, heartbeat. */
extern "C" void mhfu_xram_recon_tick(uint8_t scr)
{
    if (!g_recon_enabled) return;
    if (!mhfu_xram_mode) mhfu_xram_init();
    if (mhfu_xram_mode != XRAM_VOLATILE) return;

    static uint8_t  s_prev     = 0xFF;
    static uint32_t s_beat     = 0;
    static uint32_t s_vprobe   = 0;
    static int      s_in_quest = 0;     /* reached scr 17 while holding */
    static uint32_t s_hold_tk  = 0;
    static int      s_rel_log  = 0;     /* pending release reason: 1 exit, 2 menu, 3 timeout */

    /* the acquire happens during the depart load, where ms0 is off: log it once safe */
    if (g_recon_arm_log && mhfu_world_ms0_io_safe()) {
        if (g_recon_arm_log == 1)
            mhfu_xram_log("[recon] ACQUIRED volatile at quest-depart base=0x%08X size=%uKB -> HELD into load",
                          (unsigned)g_recon_base, (unsigned)(g_recon_size / 1024));
        else
            mhfu_xram_log("[recon] quest-depart TryLock BUSY rc=0x%08X (already held at depart = Scenario 2?)",
                          (unsigned)g_recon_arm_rc);
        g_recon_arm_log = 0;
    }

    /* Release on quest exit (only after reaching the quest: the load screen is not 17),
     * at title or menu, or after 15 s without reaching the quest. */
    if (g_recon_held) {
        s_hold_tk++;
        if (scr == 17 && !s_in_quest) {
            s_in_quest = 1;
            mhfu_xram_log("[recon] HELD volatile across load INTO quest (scr=17) area=%u "
                          "-> Scenario 1 (game did NOT need volatile to load the section)",
                          (unsigned)mhfu_world_area_index());
        }
        int leaving = (s_in_quest && s_prev == 17 && scr != 17);
        int at_menu = (scr == 0x01 || scr == 0x04);
        int timeout = (!s_in_quest && s_hold_tk > 150);
        if (leaving || at_menu || timeout) {
            sceKernelVolatileMemUnlock(0);
            g_recon_held = 0;
            s_rel_log = leaving ? 1 : (at_menu ? 2 : 3);
            s_in_quest = 0; s_hold_tk = 0;
            g_recon_base = g_recon_size = 0;
        }
    }
    if (s_rel_log && mhfu_world_ms0_io_safe()) {
        mhfu_xram_log("[recon] RELEASED volatile (%s) -> save-safe",
                      s_rel_log == 1 ? "quest-exit" : (s_rel_log == 2 ? "menu" : "abort-timeout"));
        s_rel_log = 0;
    }

    /* village: is volatile free at idle? TryLock and give it straight back */
    if (!g_recon_held && scr == 22 && (s_vprobe++ % 20) == 0) {
        void *ptr = 0; int size = 0;
        int rc = sceKernelVolatileMemTryLock(0, &ptr, &size);
        if (rc >= 0 && ptr && size > 0) {
            sceKernelVolatileMemUnlock(0);
            mhfu_xram_log("[recon] village(22): volatile FREE base=0x%08X size=%uKB",
                          (unsigned)(uintptr_t)ptr, (unsigned)(size / 1024));
        } else {
            mhfu_xram_log("[recon] village(22): volatile BUSY rc=0x%08X", (unsigned)rc);
        }
    }

    if (g_recon_held && (s_beat++ % 10) == 0)
        mhfu_xram_log("[recon] HOLDING volatile scr=%u area=%u base=0x%08X (load survived)",
                      (unsigned)scr, (unsigned)mhfu_world_area_index(), (unsigned)g_recon_base);

    s_prev = scr;
}
