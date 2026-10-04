/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* mhfu.capture bindings over the core's framebuffer capture; declared in lua/meta/mhfu.d.lua. */
#include "mhfu/mhfu.h"
#include "internal.h"
#include "lua_host.h"

int lb_capture(lua_State *L)
{
    int on = lua_toboolean(L, 1);
    if (on && !lua_isnoneornil(L, 2)) {
        int scale = (int)luaL_optinteger(L, 2, 2);
        int iv    = (int)luaL_optinteger(L, 3, 66);
        const char *path = luaL_optstring(L, 4, 0);
        mhfu_capture_configure(scale, iv, path);
    }
    lua_pushboolean(L, mhfu_capture_set(on));
    return 1;
}

int lb_capture_status(lua_State *L)
{
    int f = 0, kb = 0, err = 0;
    int active = mhfu_capture_status(&f, &kb, &err);
    lua_pushboolean(L, active);
    lua_pushinteger(L, f);
    lua_pushinteger(L, kb);
    lua_pushinteger(L, err);
    return 4;
}
