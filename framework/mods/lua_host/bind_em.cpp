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
/* the op names in addresses.toml's order: luaL_checkoption's index + 1 is the op */
static const char *const k_conds[] = { MHFU_EM_COND_NAMES, 0 };
static const char *const k_effects[] = { MHFU_EM_EFFECT_NAMES, 0 };

/* t[key] = { "op", arg, value, ... } at most `max` of them, arg and value default 0 */
static void tbl_ops(lua_State *L, int idx, const char *key, const char *const *names,
                    mhfu_em_op_t *out, int max)
{
    lua_getfield(L, idx, key);
    if (!lua_isnil(L, -1)) {
        luaL_checktype(L, -1, LUA_TTABLE);
        const int n = (int)luaL_len(L, -1);
        luaL_argcheck(L, n <= max, 2, "too many entries in conds/effects");
        for (int i = 0; i < n; i++) {
            lua_rawgeti(L, -1, i + 1);
            luaL_checktype(L, -1, LUA_TTABLE);
            lua_rawgeti(L, -1, 1);
            out[i].op = (uint8_t)(luaL_checkoption(L, -1, NULL, names) + 1);
            lua_rawgeti(L, -2, 2);
            out[i].arg = lua_isnil(L, -1) ? 0 : (uint8_t)luaL_checkinteger(L, -1);
            lua_rawgeti(L, -3, 3);
            const lua_Integer v = lua_isnil(L, -1) ? 0 : luaL_checkinteger(L, -1);
            luaL_argcheck(L, v >= -32768 && v <= 32767, 2, "a value past a s16");
            out[i].value = (int16_t)v;
            lua_pop(L, 4);
        }
    }
    lua_pop(L, 1);
}

int lb_em_rule(lua_State *L)
{
    int slot = (int)luaL_checkinteger(L, 1);
    if (lua_isnoneornil(L, 2)) { mhfu_em_rule(slot, 0); lua_pushboolean(L, 1); return 1; }
    luaL_checktype(L, 2, LUA_TTABLE);
    mhfu_em_rule_t r = {};
    r.from_mask  = (uint8_t)tbl_int(L, 2, "from_mask", 0);
    r.from_sub   = (uint8_t)tbl_int(L, 2, "from_sub", MHFU_EM_SUB_ANY);
    r.to_main    = (uint8_t)tbl_int(L, 2, "to_main", 0);
    r.to_sub     = (uint8_t)tbl_int(L, 2, "to_sub", 0);
    r.mode       = (uint8_t)tbl_int(L, 2, "mode", 0);
    r.flags      = (uint8_t)((tbl_bool(L, 2, "receding") ? MHFU_EM_RULE_RECEDING : 0)
                           | (tbl_bool(L, 2, "closing")  ? MHFU_EM_RULE_CLOSING  : 0)
                           | (tbl_bool(L, 2, "no_play")  ? MHFU_EM_RULE_NO_PLAY  : 0));
    r.min_frames = (uint32_t)tbl_int(L, 2, "min_frames", 0);
    r.dist_lo    = (float)tbl_num(L, 2, "dist_lo", 0.0);
    r.dist_hi    = (float)tbl_num(L, 2, "dist_hi", 1.0e9);
    r.cooldown   = (uint32_t)tbl_int(L, 2, "cooldown", 0);
    r.count      = (uint32_t)tbl_int(L, 2, "count", (lua_Integer)MHFU_EM_UNLIMITED);
    r.from_move  = (uint8_t)(tbl_int(L, 2, "from_move", -1) + 1);
    r.play_move  = (uint8_t)(tbl_int(L, 2, "play_move", -1) + 1);
    lua_getfield(L, 2, "on");
    r.on = lua_isnil(L, -1) ? 0
         : (uint8_t)(luaL_checkoption(L, -1, NULL, mhfu_monster_event_names) + MHFU_MONSTER_NOTICED);
    lua_pop(L, 1);
    r.part       = (uint8_t)tbl_int(L, 2, "part", MHFU_EM_ANY_PART);
    r.force      = (uint8_t)tbl_bool(L, 2, "force");
    r.signal     = (uint8_t)(tbl_int(L, 2, "signal", -1) + 1);
    tbl_ops(L, 2, "conds", k_conds, r.conds, MHFU_EM_CONDS);
    tbl_ops(L, 2, "effects", k_effects, r.effects, MHFU_EM_EFFECTS);
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
    SF_INT("events_muted", st.events_muted);
    SF_INT("tip_pairs",  st.tip_pairs);
    SF_INT("tip_copies", st.tip_copies);
    SF_INT("cut_waits",  st.cut_waits);
    SF_INT("broken",     st.broken);
    SF_INT("sever_pct",  st.sever_pct);
    SF_INT("gate_refused", st.gate_refused);
    lua_pushboolean(L, (int)st.natural_rage); lua_setfield(L, -2, "natural_rage");
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
    lua_newtable(L);
    for (int i = 0; i < MHFU_EM_VARS; i++) { lua_pushinteger(L, st.vars[i]); lua_rawseti(L, -2, i + 1); }
    lua_setfield(L, -2, "vars");
#undef SF_INT
    return 1;
}

/* the board: em_var(k [, v]) reads or sets counter k; em_signal(k) raises signal k */
int lb_em_var(lua_State *L)
{
    const int k = (int)luaL_checkinteger(L, 1);
    if (!lua_isnoneornil(L, 2)) mhfu_em_set_var(k, (int)luaL_checkinteger(L, 2));
    lua_pushinteger(L, mhfu_em_var(k));
    return 1;
}

int lb_em_signal(lua_State *L)
{
    lua_pushboolean(L, mhfu_em_signal((int)luaL_checkinteger(L, 1)));
    return 1;
}

/* rage and the tail cut's gate */
int lb_em_rage(lua_State *L)
{
    lua_pushboolean(L, mhfu_em_rage(lua_toboolean(L, 1)));
    return 1;
}

int lb_em_natural_rage(lua_State *L)
{
    mhfu_em_natural_rage(lua_toboolean(L, 1));
    lua_pushboolean(L, 1);
    return 1;
}

int lb_em_sever_gate(lua_State *L)
{
    lua_pushboolean(L, mhfu_em_sever_gate((int)luaL_checkinteger(L, 1)));
    return 1;
}

/* {{joint, carrier}, ...} */
int lb_em_tip(lua_State *L)
{
    luaL_checktype(L, 1, LUA_TTABLE);
    uint8_t pairs[MHFU_EM_TIP_MAX][2];
    int n = (int)luaL_len(L, 1);
    if (n > MHFU_EM_TIP_MAX) {
        lua_pushboolean(L, 0);
        return 1;
    }
    for (int i = 0; i < n; i++) {
        lua_rawgeti(L, 1, i + 1);
        luaL_checktype(L, -1, LUA_TTABLE);
        lua_rawgeti(L, -1, 1);
        lua_rawgeti(L, -2, 2);
        pairs[i][0] = (uint8_t)luaL_checkinteger(L, -2);
        pairs[i][1] = (uint8_t)luaL_checkinteger(L, -1);
        lua_pop(L, 3);
    }
    lua_pushboolean(L, mhfu_em_tip(pairs, n));
    return 1;
}

/* --- own moves (mhfu.em_move is in bind_move.cpp, beside the move spec it reads) --- */

int lb_em_moves_clear(lua_State *L)
{
    mhfu_em_moves_clear();
    lua_pushboolean(L, 1);
    return 1;
}

int lb_em_play(lua_State *L)
{
    lua_pushboolean(L, mhfu_em_play((uint32_t)luaL_checkinteger(L, 1),
                                    (int)luaL_checkinteger(L, 2), lua_toboolean(L, 3)));
    return 1;
}

int lb_em_playing(lua_State *L)
{
    lua_pushinteger(L, mhfu_em_playing());
    return 1;
}

int lb_em_moves_status(lua_State *L)
{
    const volatile mhfu_em_moves_t *r = mhfu_em_moves();
    lua_newtable(L);
    if (!r) return 1;
#define SF_INT(name, v) do { lua_pushinteger(L, (lua_Integer)(v)); lua_setfield(L, -2, name); } while (0)
    SF_INT("block", (uintptr_t)r);
    SF_INT("playing", mhfu_em_playing());
    const volatile mhfu_move_state_t *m = mhfu_move_state();
    int ours = m && r->tag_slot != MHFU_EM_NO_MOVE && m->started == r->tag_started;
    SF_INT("last", ours ? (lua_Integer)r->tag_slot : -1);
    SF_INT("plays", r->plays);
    SF_INT("chained", r->chained);
    SF_INT("keys", r->key_top);
#undef SF_INT
    return 1;
}
