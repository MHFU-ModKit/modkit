/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* Framework log (mhfu/log.h), written to ms0 only while the savedata utility cannot be running. */
#include <pspiofilemgr.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "mhfu/log.h"
#include "mhfu/world.h"

static SceUID g_log_fd = -1;

/* Lines logged while ms0 is not safe are held here and flushed on the next safe write or
 * mhfu_log_flush_held; overflow is counted. */
#define EARLY_CAP 4096
static char     g_early[EARLY_CAP];
static unsigned g_early_n;
static unsigned g_early_dropped;

static void stash(const char *line, int n)
{
    if (g_early_n + (unsigned)n + 1u >= EARLY_CAP) { g_early_dropped++; return; }
    memcpy(g_early + g_early_n, line, (size_t)n);
    g_early_n += (unsigned)n;
    g_early[g_early_n++] = '\n';
}

static void flush_early(void)
{
    if (!g_early_n) return;
    unsigned n = g_early_n, dropped = g_early_dropped;
    g_early_n = 0;                       /* before the writes: they re-enter */
    g_early_dropped = 0;
    static const char hdr[] = "--- boot log (held until ms0 was safe) ---\n";
    sceIoWrite(g_log_fd, hdr, sizeof(hdr) - 1);
    sceIoWrite(g_log_fd, g_early, n);
    if (dropped) {
        char tail[80];
        int k = snprintf(tail, sizeof(tail), "--- %u more boot line(s) did not fit ---\n",
                         dropped);
        if (k > 0) sceIoWrite(g_log_fd, tail, (size_t)k);
    }
}

static int open_log(void)
{
    if (g_log_fd < 0) {
        g_log_fd = sceIoOpen(
            "ms0:/PSP/PLUGINS/mhfu_framework/framework.log",
            PSP_O_WRONLY | PSP_O_CREAT | PSP_O_APPEND, 0666);
    }
    return g_log_fd >= 0;
}

extern "C" void mhfu_log(const char *fmt, ...)
{
    char buf[256];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n <= 0) return;
    if (n > (int)sizeof(buf) - 1) n = (int)sizeof(buf) - 1;

    /* No ms0 access at all outside gameplay: the savedata utility shares the non-reentrant
     * Memory Stick driver, and writing during a load or save freezes the PSP. */
    if (!mhfu_world_ms0_io_safe()) { stash(buf, n); return; }

    if (open_log()) {
        flush_early();
        sceIoWrite(g_log_fd, buf, (size_t)n);
        sceIoWrite(g_log_fd, "\n", 1);
    }
}

extern "C" void mhfu_log_flush_held(void)
{
    if (open_log()) flush_early();
}

extern "C" void mhfu_log_close(void)
{
    if (g_log_fd >= 0) { sceIoClose(g_log_fd); g_log_fd = -1; }
}
