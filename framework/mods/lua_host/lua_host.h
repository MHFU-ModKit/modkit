/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* lua_host's shared state between its files; not part of the SDK. */
#ifndef MHFU_LUA_HOST_H
#define MHFU_LUA_HOST_H

#include <stdint.h>

extern "C" {
#include "lua.h"
#include "lualib.h"
#include "lauxlib.h"
}

#include "marshal.h"

/* The mod id, and the owner of every event lua_host registers. */
#define MHFU_LUA_HOST_ID "lua_host"

/* --- the VM (vm.cpp) --- */
extern lua_State   *mhfu_lua_vm;
extern volatile int mhfu_lua_have_tick;   /* a global mhfu_tick is defined */
extern volatile int mhfu_lua_ready;       /* setup done: events may enter the VM */
extern unsigned     mhfu_lua_slab_peak;   /* high-water mark, for the log */

int      mhfu_lua_vm_init(void);          /* the lock and the slab; -1 on failure */
int      mhfu_lua_vm_open(void);          /* the state on the slab; -1 on failure */
void     mhfu_lua_vm_close(void);
unsigned mhfu_lua_slab_live(void);
/* Every entry into the VM, from any thread, sits between these; 0 = VM down. */
int      mhfu_lua_enter(void);
void     mhfu_lua_leave(void);

/* --- the event bridge (events.cpp); the exec thread in marshal.h --- */
/* Runs one marshalled request's Lua; the exec thread's server. */
void mhfu_lua_serve(mhfu_lua_req_t *q);

/* --- memory-stick scripts (scripts.cpp) --- */
void mhfu_lua_scripts_init(void);         /* the read buffer */
int  mhfu_lua_load_dir(lua_State *L);     /* runs every script; returns how many ran */
void mhfu_lua_prime_tracked(void);        /* records each script's stat for hot reload */
void mhfu_lua_hot_reload_scan(void);
void mhfu_lua_install_require(lua_State *L);  /* package, with only the mods/lib searcher */

/* --- the mhfu table: lb_<name> for each function lua/meta/mhfu.d.lua declares --- */
#include "lua_api.gen.h"

#endif /* MHFU_LUA_HOST_H */
