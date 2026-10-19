/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* mhfu.brain_* bindings over the memory-stick AI scripts (src/core/brain.cpp); declared in
 * lua/meta/mhfu.d.lua. */
#include "mhfu/mhfu.h"
#include "lua_host.h"

/* the path as inject_register takes it */
int lb_brain_install(lua_State *L)
{
    const char *path = luaL_checkstring(L, 1);
    const int row = mhfu_brain_install(path);
    if (row < 0) {
        lua_pushnil(L);
        lua_pushfstring(L, "brain_install %s: %s", path, mhfu_brain_error(row));
        return 2;
    }
    lua_pushinteger(L, row);
    return 1;
}

int lb_brain_status(lua_State *L)
{
    mhfu_brain_status_t st;
    if (!mhfu_brain_status((int)luaL_checkinteger(L, 1), &st)) {
        lua_pushnil(L);
        return 1;
    }
    lua_createtable(L, 0, 6);
    lua_pushinteger(L, (lua_Integer)st.id);
    lua_setfield(L, -2, "id");
    lua_pushinteger(L, (lua_Integer)st.image);
    lua_setfield(L, -2, "image");
    lua_pushinteger(L, (lua_Integer)st.size);
    lua_setfield(L, -2, "size");
    lua_pushinteger(L, (lua_Integer)st.headers);
    lua_setfield(L, -2, "headers");
    lua_pushinteger(L, (lua_Integer)st.patched);
    lua_setfield(L, -2, "patched");
    lua_createtable(L, MHFU_SCRIPT_HEADERS_COUNT, 0);
    int n = 0;
    for (int k = 0; k < MHFU_SCRIPT_HEADERS_COUNT; k++) {
        if (!(st.words & (1u << k))) continue;
        lua_pushinteger(L, k);
        lua_rawseti(L, -2, ++n);
    }
    lua_setfield(L, -2, "maps");
    return 1;
}
