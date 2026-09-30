/* mhfu.* bindings for a relocated species overlay and live model injection. */
#include "mhfu/mhfu.h"
#include "lua_host.h"

/* mhfu.load_relocated_overlay(path[, run_inits]) -> region_base, new_load, delta
 * (or nil, errcode): loads a second em*.ovl, relocated to a fresh address. */
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

/* mhfu.inject_register(file_id, path) -> ok:bool
 * Watches a big-monster PAC on the memory stick and overwrites the species'
 * loaded buffer in place; the worker drives mhfu_inject_tick(). */
int lb_inject_register(lua_State *L)
{
    uint32_t id = (uint32_t)luaL_checkinteger(L, 1);
    const char *path = luaL_checkstring(L, 2);
    lua_pushboolean(L, mhfu_inject_register(id, path) == 0);
    return 1;
}
/* mhfu.inject_relocate(file_id, grown_path, orig_path) -> ok:bool
 * A PAC bigger than the engine's raw buffer: the load transform's source
 * (get_subresource a0) is redirected to the grown copy in extra RAM; orig_path
 * recognises the engine's buffer. */
int lb_inject_relocate(lua_State *L)
{
    uint32_t id = (uint32_t)luaL_checkinteger(L, 1);
    const char *grown = luaL_checkstring(L, 2);
    const char *orig  = luaL_checkstring(L, 3);
    lua_pushboolean(L, mhfu_inject_register_relocate(id, grown, orig) == 0);
    return 1;
}
/* mhfu.inject_now(file_id) -> overwrites so far (0 = unknown id): re-read and apply now. */
int lb_inject_now(lua_State *L)
{
    lua_pushinteger(L, (lua_Integer)mhfu_inject_now((uint32_t)luaL_checkinteger(L, 1)));
    return 1;
}
/* mhfu.inject_locate(file_id) -> overwrites so far (0 = unknown id). */
int lb_inject_locate(lua_State *L)
{
    lua_pushinteger(L, (lua_Integer)mhfu_inject_locate((uint32_t)luaL_checkinteger(L, 1)));
    return 1;
}
