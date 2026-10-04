/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* mhfu.* bindings for a relocated species overlay and live model injection; declared in
 * lua/meta/mhfu.d.lua. */
#include "mhfu/mhfu.h"
#include "lua_host.h"

int lb_load_relocated_overlay(lua_State *L)
{
    const char *path = luaL_checkstring(L, 1);
    int run_inits = lua_isnoneornil(L, 2) ? 0 : lua_toboolean(L, 2);
    static mhfu_ovl_region_t reg;   /* keep alive (region stays mapped) */
    int rc = mhfu_ovl_load_relocated(path, &reg);
    if (rc != 0) { lua_pushnil(L); lua_pushinteger(L, rc); return 2; }
    if (run_inits) mhfu_ovl_run_static_inits(&reg);
    lua_pushinteger(L, (lua_Integer)reg.new_load);   /* region_base: the footprint's start */
    lua_pushinteger(L, (lua_Integer)reg.new_load);
    lua_pushinteger(L, (lua_Integer)reg.delta);
    return 3;
}

/* the worker drives mhfu_inject_tick() */
int lb_inject_register(lua_State *L)
{
    uint32_t id = (uint32_t)luaL_checkinteger(L, 1);
    const char *path = luaL_checkstring(L, 2);
    lua_pushboolean(L, mhfu_inject_register(id, path) == 0);
    return 1;
}
int lb_inject_relocate(lua_State *L)
{
    uint32_t id = (uint32_t)luaL_checkinteger(L, 1);
    const char *grown = luaL_checkstring(L, 2);
    const char *orig  = luaL_checkstring(L, 3);
    lua_pushboolean(L, mhfu_inject_register_relocate(id, grown, orig) == 0);
    return 1;
}
int lb_inject_now(lua_State *L)
{
    lua_pushinteger(L, (lua_Integer)mhfu_inject_now((uint32_t)luaL_checkinteger(L, 1)));
    return 1;
}
int lb_inject_locate(lua_State *L)
{
    lua_pushinteger(L, (lua_Integer)mhfu_inject_locate((uint32_t)luaL_checkinteger(L, 1)));
    return 1;
}
