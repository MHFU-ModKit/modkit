/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* registry_watch.h for the host test, through ctypes. */
#include <string.h>
#include "registry_watch.h"

static registry_watch_t g_w;

extern "C" {
void reg_host_reset(void) { memset(&g_w, 0, sizeof(g_w)); }
void reg_host_forget(void) { watch_forget(&g_w); }
void reg_host_begin(void) { watch_begin(&g_w); }
int reg_host_spawned(int slot, uint32_t cur) { return watch_spawned(&g_w, slot, cur); }
}
