/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* Host stand-ins for hooks.cpp's PSP side: game memory is a word array, the log a counter per
 * line, and a built wrapper a fresh address. */
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "internal.h"

#define MEM_WORDS 0x1000

static uint32_t g_mem[MEM_WORDS];
static char     g_log[64][160];
static int      g_log_n;
static uint32_t g_next_wrapper = 0x8000;

extern "C" volatile uint32_t *mhfu_host_word(uint32_t addr)
{
    return &g_mem[(addr >> 2) % MEM_WORDS];
}

extern "C" void mhfu_log(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_log[g_log_n % 64], sizeof(g_log[0]), fmt, ap);
    va_end(ap);
    g_log_n++;
}

extern "C" uint32_t mhfu_wrap_build(const mhfu_wrap_t *w)
{
    (void)w;
    g_next_wrapper += 0x80;
    return g_next_wrapper;
}

extern "C" void host_reset(void)
{
    memset(g_mem, 0, sizeof(g_mem));
    g_log_n = 0;
    g_next_wrapper = 0x8000;
}

extern "C" uint32_t host_peek(uint32_t addr) { return *mhfu_host_word(addr); }
extern "C" void host_poke(uint32_t addr, uint32_t v) { *mhfu_host_word(addr) = v; }
extern "C" uint32_t host_last_wrapper(void) { return g_next_wrapper; }

/* how many lines logged since the last drain contain needle; then forgets them */
extern "C" int host_log_drain(const char *needle)
{
    int n = 0;
    for (int i = 0; i < g_log_n && i < 64; i++)
        if (strstr(g_log[i], needle)) n++;
    g_log_n = 0;
    return n;
}
