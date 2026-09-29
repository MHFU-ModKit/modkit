/* Install helpers: a queue of code patches applied only while the JIT is cold (title, menu),
 * and call-site wrappers built in the code cave. */
#include <pspthreadman.h>

#include "mhfu/hooks.h"
#include "mhfu/mips.h"
#include "mhfu/world.h"
#include "mhfu/log.h"
#include "internal.h"

#define MAX_DEFERRED 16

typedef struct {
    int         pending;
    uint32_t    addr;
    uint32_t    expect;
    uint32_t    word;
    const char *owner;
} deferred_t;

static deferred_t g_deferred[MAX_DEFERRED];

extern "C" mhfu_hook_rc_t mhfu_hook_word_when_quiet(uint32_t addr, uint32_t expect,
                                                     uint32_t word, const char *owner)
{
    if (!owner) return MHFU_HOOK_BADARG;
    for (int i = 0; i < MAX_DEFERRED; i++) {
        if (g_deferred[i].pending && g_deferred[i].addr == addr) return MHFU_HOOK_OK;
    }
    for (int i = 0; i < MAX_DEFERRED; i++) {
        if (g_deferred[i].pending) continue;
        g_deferred[i].addr    = addr;
        g_deferred[i].expect  = expect;
        g_deferred[i].word    = word;
        g_deferred[i].owner   = owner;
        g_deferred[i].pending = 1;
        return MHFU_HOOK_OK;
    }
    return MHFU_HOOK_NOSPACE;
}

extern "C" int mhfu_hook_deferred_thread(SceSize args, void *argp)
{
    (void)args; (void)argp;
    for (;;) {
        sceKernelDelayThread(100 * 1000);   /* 10 Hz */
        uint8_t scr = mhfu_world_screen_state();

        /* Volatile-memory work waits for the first village visit: the savedata utility
         * borrows the volatile partition at character select; touching it then froze the load. */
        static int g_village_seen = 0;
        if (scr == MHFU_WORLD_SCREEN_VILLAGE) g_village_seen = 1;
        if (g_village_seen) {
            mhfu_vol_install();
            mhfu_vol_flush();
            /* no-ops while nothing arms the prelock; release on leaving the quest */
            mhfu_xram_prelock();
            {
                static uint8_t s_prev_scr = 0xFF;
                if (s_prev_scr == MHFU_WORLD_SCREEN_IN_AREA && scr != MHFU_WORLD_SCREEN_IN_AREA)
                    mhfu_xram_release();
                s_prev_scr = scr;
            }
            mhfu_xram_recon_tick(scr);
        }

        /* JIT-cold screens only */
        if (scr != MHFU_WORLD_SCREEN_MENU && scr != MHFU_WORLD_SCREEN_TITLE) continue;
        for (int i = 0; i < MAX_DEFERRED; i++) {
            deferred_t *d = &g_deferred[i];
            if (!d->pending) continue;
            if (*(volatile uint32_t *)d->addr != d->expect) continue;  /* not loaded, or changed */
            if (mhfu_hook_word(d->addr, d->word, d->owner) == MHFU_HOOK_OK) {
                d->pending = 0;
                mhfu_log("[install] quiet-patched 0x%08lx for '%s'",
                         (unsigned long)d->addr, d->owner);
            }
        }
    }
    return 0;
}

extern "C" mhfu_hook_rc_t mhfu_hook_call(uint32_t call_site, uint32_t orig_target,
                                                    void (*helper)(uint32_t), int mode,
                                                    const char *owner)
{
    if (!helper || !owner) return MHFU_HOOK_BADARG;
    uint32_t *s = mhfu_cave_alloc(16);
    if (!s) return MHFU_HOOK_NOSPACE;
    uint32_t h = (uint32_t)(uintptr_t)helper;
    int i = 0;
    s[i++] = mips_addiu(MIPS_REG_SP, MIPS_REG_SP, -0x20);
    s[i++] = mips_sw(MIPS_REG_RA, 0x18, MIPS_REG_SP);
    s[i++] = mips_sw(MIPS_REG_A0, 0x10, MIPS_REG_SP);
    if (mode == MHFU_HOOK_WRAP_POSTFIX) {
        s[i++] = mips_jal(orig_target); s[i++] = MIPS_NOP;
        s[i++] = mips_lw(MIPS_REG_A0, 0x10, MIPS_REG_SP);
        s[i++] = mips_jal(h);           s[i++] = MIPS_NOP;
    } else { /* PREFIX */
        s[i++] = mips_jal(h);           s[i++] = MIPS_NOP;
        s[i++] = mips_lw(MIPS_REG_A0, 0x10, MIPS_REG_SP);
        s[i++] = mips_jal(orig_target); s[i++] = MIPS_NOP;
    }
    s[i++] = mips_lw(MIPS_REG_RA, 0x18, MIPS_REG_SP);
    s[i++] = mips_addiu(MIPS_REG_SP, MIPS_REG_SP, 0x20);
    s[i++] = mips_jr(MIPS_REG_RA);
    s[i++] = MIPS_NOP;
    while (i < 16) s[i++] = MIPS_NOP;

    mhfu_hook_flush_caches();
    return mhfu_hook_word_when_quiet(call_site, mips_jal(orig_target),
                                      mips_jal((uint32_t)(uintptr_t)s), owner);
}
