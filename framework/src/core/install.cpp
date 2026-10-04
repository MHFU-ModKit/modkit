/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* The deferred thread: lands queued code patches on the JIT-cold screens (title, menu) and
 * runs the volatile-memory work. */
#include <pspthreadman.h>

#include "mhfu/world.h"
#include "internal.h"

extern "C" int mhfu_hook_deferred_thread(SceSize args, void *argp)
{
    (void)args; (void)argp;
    int     village_seen = 0;
    uint8_t prev_scr = 0xFF;
    for (;;) {
        sceKernelDelayThread(100 * 1000);   /* 10 Hz */
        uint8_t scr = mhfu_world_screen_state();

        /* Volatile-memory work waits for the first village visit: the savedata utility
         * borrows the volatile partition at character select; touching it then froze the load. */
        if (scr == MHFU_WORLD_SCREEN_VILLAGE) village_seen = 1;
        if (village_seen) {
            mhfu_vol_install();
            mhfu_vol_flush();
            if (prev_scr == MHFU_WORLD_SCREEN_IN_AREA && scr != MHFU_WORLD_SCREEN_IN_AREA)
                mhfu_xram_release();
            prev_scr = scr;
        }

        if (scr == MHFU_WORLD_SCREEN_MENU || scr == MHFU_WORLD_SCREEN_TITLE)
            mhfu_hook_land_queued();
    }
    return 0;
}
