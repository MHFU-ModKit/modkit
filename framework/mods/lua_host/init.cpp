/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* lua_host: a sandboxed Lua 5.4 VM running mods from the memory stick against the
 * mhfu.* API. Setup, the 10 Hz worker and the mod descriptor. */
#include <pspctrl.h>
#include <pspthreadman.h>
#include <string.h>

#include "mhfu/mhfu.h"
#include "lua_host.h"
#include "lua_api.gen.inc"        /* k_mhfu_funcs, k_mhfu_consts */

/* embed.S: NUL-terminated copies of lua/lib/_prelude.lua and the generated address table */
extern "C" const char mhfu_lua_prelude[];
extern "C" const char mhfu_lua_addresses[];

/* mhfu.addr, every address and struct offset, is built when a script first reads it: the table
 * takes about 20 KB of the slab, and most scripts never use it. */
static int mhfu_index(lua_State *L)
{
    if (lua_type(L, 2) != LUA_TSTRING || strcmp(lua_tostring(L, 2), "addr") != 0) return 0;
    if (luaL_loadstring(L, mhfu_lua_addresses) != LUA_OK) return lua_error(L);
    lua_call(L, 0, 1);
    lua_pushvalue(L, -1);
    lua_setfield(L, 1, "addr");
    return 1;
}

static void register_mhfu_api(lua_State *L)
{
    lua_newtable(L);                       /* the mhfu table */
    luaL_setfuncs(L, k_mhfu_funcs, 0);
    for (int i = 0; k_mhfu_consts[i].name; i++) {
        lua_pushinteger(L, k_mhfu_consts[i].value);
        lua_setfield(L, -2, k_mhfu_consts[i].name);
    }
    lua_newtable(L);                       /* its metatable: mhfu.addr on first use */
    lua_pushcfunction(L, mhfu_index);
    lua_setfield(L, -2, "__index");
    lua_setmetatable(L, -2);
    lua_setglobal(L, "mhfu");
}

static void remove_unsafe_globals(lua_State *L)
{
    static const char *unsafe[] = {
        "dofile", "loadfile", "load", "loadstring",
        "collectgarbage", "rawget", "rawset", "rawequal", "rawlen", 0
    };
    for (int i = 0; unsafe[i]; i++) {
        lua_pushnil(L);
        lua_setglobal(L, unsafe[i]);
    }
}

/* Only base, table, string, math and a require limited to mods/lib are opened; io, os and
 * debug are not linked. */
static int lua_host_setup(void)
{
    if (mhfu_lua_vm_open() != 0) return -1;
    lua_State *L = mhfu_lua_vm;

    luaL_requiref(L, "_G",     luaopen_base,   1); lua_pop(L, 1);
    luaL_requiref(L, "table",  luaopen_table,  1); lua_pop(L, 1);
    luaL_requiref(L, "string", luaopen_string, 1); lua_pop(L, 1);
    luaL_requiref(L, "math",   luaopen_math,   1); lua_pop(L, 1);
    mhfu_lua_install_require(L);
    remove_unsafe_globals(L);
    register_mhfu_api(L);

    /* the OO layer (mhfu.world, mhfu.entity, mhfu.mem) before any mod; the flat
     * API works without it */
    if (luaL_loadstring(L, mhfu_lua_prelude) == LUA_OK
        && lua_pcall(L, 0, 0, 0) == LUA_OK) {
        /* ok */
    } else {
        mhfu_log("[lua_host] prelude FAILED: %s", lua_tostring(L, -1));
        lua_pop(L, 1);
    }

    int loaded = mhfu_lua_load_dir(L);
    mhfu_log("[lua_host] %d mod(s) loaded", loaded);
    mhfu_lua_prime_tracked();

    lua_getglobal(L, "mhfu_tick");
    mhfu_lua_have_tick = lua_isfunction(L, -1);
    lua_pop(L, 1);

    mhfu_log("[lua_host] VM ready: api=%d lua_Number=%dB live=%uB peak=%uB tick=%d",
             MHFU_LUA_API_VERSION, (int)sizeof(lua_Number), mhfu_lua_slab_live(),
             mhfu_lua_slab_peak, mhfu_lua_have_tick);
    /* the scripts were just read from ms0, so a boot error need not wait for gameplay */
    mhfu_log_flush_held();
    return 0;
}

static void call_tick(void)
{
    if (!mhfu_lua_have_tick) return;
    /* mod ticks touch game memory: running one while the savedata utility loads
     * the save froze it, so only during gameplay */
    if (!mhfu_world_ms0_io_safe()) return;
    if (!mhfu_lua_enter()) return;
    lua_getglobal(mhfu_lua_vm, "mhfu_tick");
    if (lua_pcall(mhfu_lua_vm, 0, 0, 0) != LUA_OK) {
        mhfu_log("[lua_host] mhfu_tick error: %s", lua_tostring(mhfu_lua_vm, -1));
        lua_pop(mhfu_lua_vm, 1);
    }
    mhfu_lua_leave();
}

/* Injection at 10 Hz, to catch the raw model buffer before the overlay
 * transform reads it; mhfu_tick and hot reload every fifth pass (2 Hz). */
static int worker(SceSize args, void *argp)
{
    (void)args; (void)argp;
    sceKernelDelayThread(3 * 1000 * 1000);   /* let game + framework settle */
    int sub = 0;
    for (;;) {
        sceKernelDelayThread(100 * 1000);    /* 10 Hz base */
        mhfu_inject_tick();
        if (++sub >= 5) {
            sub = 0;
            call_tick();
            mhfu_lua_hot_reload_scan();
        }
    }
    return 0;
}

static int lua_host_init(void)
{
    if (mhfu_lua_vm_init() != 0) return -1;
    mhfu_lua_scripts_init();

    if (lua_host_setup() != 0) return -1;

    /* freecam is not installed (freecam.cpp) */

    /* Only now may the event trampolines enter the VM: setup may already have
     * installed framework hooks. */
    mhfu_lua_ready = 1;

    if (mhfu_lua_exec_start(mhfu_lua_serve) != 0) return -1;

    SceUID th = sceKernelCreateThread("mhfu_lua_host",
                                      (SceKernelThreadEntry)worker,
                                      0x18, 0x10000, 0, 0);
    if (th < 0) { mhfu_log("[lua_host] CreateThread FAILED"); return -1; }
    sceKernelStartThread(th, 0, 0);
    mhfu_log("[lua_host] init OK, exec + poll threads started");
    return 0;
}

static void lua_host_shutdown(void)
{
    mhfu_lua_ready = 0;
    mhfu_lua_exec_stop();
    mhfu_lua_vm_close();
    mhfu_lua_exec_free();
}

MHFU_MOD(.id = MHFU_LUA_HOST_ID, .version = "0.4",
         .needs = 0, .conflicts = 0,
         .init = lua_host_init, .shutdown = lua_host_shutdown);
