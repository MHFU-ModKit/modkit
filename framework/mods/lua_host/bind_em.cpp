/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* mhfu.em_* bindings over em_vhook, the big-monster vtable seams (src/core/em_vhook.cpp);
 * declared in lua/meta/mhfu.d.lua. */
#include "mhfu/mhfu.h"
#include "lua_host.h"

int lb_em_installed(lua_State *L)
{
    lua_pushboolean(L, mhfu_em_installed());
    return 1;
}
int lb_em_request(lua_State *L)
{
    int ok = mhfu_em_request((uint8_t)luaL_checkinteger(L, 1),
                              (uint8_t)luaL_checkinteger(L, 2),
                              (uint8_t)luaL_optinteger(L, 3, 0));
    lua_pushboolean(L, ok);
    return 1;
}
int lb_em_substitute(lua_State *L)
{
    mhfu_em_substitute((int)luaL_checkinteger(L, 1),
                        (uint8_t)luaL_checkinteger(L, 2),
                        (uint8_t)luaL_checkinteger(L, 3),
                        (uint8_t)luaL_checkinteger(L, 4),
                        (uint8_t)luaL_checkinteger(L, 5),
                        (uint32_t)luaL_checkinteger(L, 6));
    lua_pushboolean(L, 1);
    return 1;
}
static lua_Integer tbl_int(lua_State *L, int idx, const char *k, lua_Integer def)
{
    lua_getfield(L, idx, k);
    lua_Integer v = lua_isnil(L, -1) ? def : luaL_checkinteger(L, -1);
    lua_pop(L, 1);
    return v;
}
static lua_Number tbl_num(lua_State *L, int idx, const char *k, lua_Number def)
{
    lua_getfield(L, idx, k);
    lua_Number v = lua_isnil(L, -1) ? def : luaL_checknumber(L, -1);
    lua_pop(L, 1);
    return v;
}
static int tbl_bool(lua_State *L, int idx, const char *k)
{
    lua_getfield(L, idx, k);
    int v = lua_toboolean(L, -1);
    lua_pop(L, 1);
    return v;
}
int lb_em_rule(lua_State *L)
{
    int slot = (int)luaL_checkinteger(L, 1);
    if (lua_isnoneornil(L, 2)) { mhfu_em_rule(slot, 0); lua_pushboolean(L, 1); return 1; }
    luaL_checktype(L, 2, LUA_TTABLE);
    mhfu_em_rule_t r;
    r.from_mask  = (uint8_t)tbl_int(L, 2, "from_mask", 0);
    r.from_sub   = (uint8_t)tbl_int(L, 2, "from_sub", MHFU_EM_SUB_ANY);
    r.to_main    = (uint8_t)tbl_int(L, 2, "to_main", 0);
    r.to_sub     = (uint8_t)tbl_int(L, 2, "to_sub", 0);
    r.mode       = (uint8_t)tbl_int(L, 2, "mode", 0);
    r.flags      = (uint8_t)((tbl_bool(L, 2, "receding") ? MHFU_EM_RULE_RECEDING : 0)
                           | (tbl_bool(L, 2, "closing")  ? MHFU_EM_RULE_CLOSING  : 0));
    r.min_frames = (uint32_t)tbl_int(L, 2, "min_frames", 0);
    r.dist_lo    = (float)tbl_num(L, 2, "dist_lo", 0.0);
    r.dist_hi    = (float)tbl_num(L, 2, "dist_hi", 1.0e9);
    r.cooldown   = (uint32_t)tbl_int(L, 2, "cooldown", 0);
    r.count      = (uint32_t)tbl_int(L, 2, "count", (lua_Integer)MHFU_EM_UNLIMITED);
    mhfu_em_rule(slot, &r);
    lua_pushboolean(L, 1);
    return 1;
}
int lb_em_clear(lua_State *L)
{
    mhfu_em_clear();
    lua_pushboolean(L, 1);
    return 1;
}
int lb_em_status(lua_State *L)
{
    mhfu_em_status_t st;
    mhfu_em_status(&st);
    lua_newtable(L);
#define SF_INT(name, v) do { lua_pushinteger(L, (lua_Integer)(v)); lua_setfield(L, -2, name); } while (0)
    lua_pushboolean(L, (int)st.installed);   lua_setfield(L, -2, "installed");
    SF_INT("ai_ticks",   st.ai_ticks);
    SF_INT("act_enters", st.act_enters);
    SF_INT("last_main",  (st.last_pair >> 8) & 0xFF);
    SF_INT("last_sub",    st.last_pair & 0xFF);
    SF_INT("frames",     st.frames);
    lua_pushnumber(L, st.dist);              lua_setfield(L, -2, "dist");
    SF_INT("sub_hits",   st.sub_hits);
    SF_INT("sub_landed", st.sub_landed);
    SF_INT("sub_last_main", (st.sub_last_in >> 8) & 0xFF);
    SF_INT("sub_last_sub",   st.sub_last_in & 0xFF);
    SF_INT("sub_last_mode", (st.sub_last_in >> 16) & 0xFF);
    SF_INT("brain_fires", st.brain_fires);
    SF_INT("req_pending", st.req_pending);
    SF_INT("req_done",    st.req_done);
    SF_INT("req_main",   (st.req_result >> 8) & 0xFF);
    SF_INT("req_sub",     st.req_result & 0xFF);
    lua_newtable(L);
    for (int i = 0; i < MHFU_EM_RING; i++) {
        /* oldest first: the newest entry sits at ring_idx */
        uint32_t e = st.ring[(st.ring_idx + 1 + i) % MHFU_EM_RING];
        lua_newtable(L);
        SF_INT("main", (e >> 8) & 0xFF);
        SF_INT("sub",   e & 0xFF);
        SF_INT("mode", (e >> 16) & 0xFF);
        SF_INT("subst", (e >> 24) & 0xFF);
        lua_rawseti(L, -2, i + 1);
    }
    lua_setfield(L, -2, "ring");
    lua_newtable(L);
    for (int i = 0; i < MHFU_EM_RULES; i++) { lua_pushinteger(L, (lua_Integer)st.rule_fired[i]); lua_rawseti(L, -2, i + 1); }
    lua_setfield(L, -2, "rule_fired");
    lua_newtable(L);
    for (int i = 0; i < MHFU_EM_RULES; i++) { lua_pushinteger(L, (lua_Integer)st.rule_left[i]); lua_rawseti(L, -2, i + 1); }
    lua_setfield(L, -2, "rule_left");
    lua_newtable(L);
    for (int i = 0; i < MHFU_EM_SUBS; i++) { lua_pushinteger(L, (lua_Integer)st.sub_left[i]); lua_rawseti(L, -2, i + 1); }
    lua_setfield(L, -2, "sub_left");
#undef SF_INT
    return 1;
}
