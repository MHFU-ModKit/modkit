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
    r.from_move  = (uint8_t)(tbl_int(L, 2, "from_move", -1) + 1);
    r.play_move  = (uint8_t)(tbl_int(L, 2, "play_move", -1) + 1);
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

/* --- own moves --- */

/* t[i] of an integer list, def where missing */
static int list_at(lua_State *L, int t, int i, int def)
{
    lua_rawgeti(L, t, i);
    int v = lua_isnil(L, -1) ? def : (int)luaL_checkinteger(L, -1);
    lua_pop(L, 1);
    return v;
}

/* the move table mhfu.move_play takes (lua/meta/mhfu.d.lua mhfu.Move) */
static void own_move(lua_State *L, int t, mhfu_move_t *mv)
{
    mhfu_move_init_spec(mv, (uint16_t)tbl_int(L, t, "entry", 0));
    mv->length = (uint16_t)tbl_int(L, t, "length", 0);
    mv->part = (uint8_t)tbl_int(L, t, "part", 0);
    mv->spawner = (uint32_t)tbl_int(L, t, "spawner", 0);
    mv->skip = (uint8_t)tbl_bool(L, t, "skip");
    mv->host_attacks = (uint8_t)tbl_bool(L, t, "host_attacks");
    if (lua_getfield(L, t, "carrier") == LUA_TTABLE) {
        int c = lua_gettop(L);
        mv->carrier_main = (uint8_t)list_at(L, c, 1, 0);
        mv->carrier_sub = (uint8_t)list_at(L, c, 2, 2);
    }
    lua_pop(L, 1);
    if (lua_getfield(L, t, "back") == LUA_TTABLE) {
        int b = lua_gettop(L);
        mv->back_main = (uint8_t)list_at(L, b, 1, 0);
        mv->back_sub = (uint8_t)list_at(L, b, 2, 0);
        mv->back_mode = (uint8_t)list_at(L, b, 3, 0);
    }
    lua_pop(L, 1);
    if (lua_getfield(L, t, "attacks") == LUA_TTABLE) {
        int a = lua_gettop(L);
        int n = (int)luaL_len(L, a);
        for (int i = 1; i <= n && mv->attack_count < MHFU_MOVE_MAX_ATTACKS; i++) {
            if (lua_rawgeti(L, a, i) == LUA_TTABLE) {
                int one = lua_gettop(L);
                mhfu_move_attack_t *at = &mv->attacks[mv->attack_count++];
                at->frame = (uint16_t)list_at(L, one, 1, 0);
                at->id = (uint16_t)list_at(L, one, 2, 0);
                at->end = (uint16_t)list_at(L, one, 3, 0);
            }
            lua_pop(L, 1);
        }
    }
    lua_pop(L, 1);
}

static const char *const TURNS[] = {"still", "hunter", "away", "fixed", NULL};

static int hex(int c)
{
    return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : -1;
}

/* t.steer (lua/meta/mhfu.d.lua mhfu.MoveSteer) */
static void own_steer(lua_State *L, int t, mhfu_steer_spec_t *sp)
{
    mhfu_steer_init_spec(sp);
    if (lua_getfield(L, t, "steer") != LUA_TTABLE) {
        lua_pop(L, 1);
        return;
    }
    int s = lua_gettop(L);
    lua_getfield(L, s, "turn");
    if (!lua_isnil(L, -1)) sp->steer.turn = (uint8_t)luaL_checkoption(L, -1, NULL, TURNS);
    lua_pop(L, 1);
    sp->steer.walls = (uint8_t)tbl_bool(L, s, "walls");
    sp->steer.rate = (uint16_t)tbl_int(L, s, "rate", 64);
    sp->steer.frames = (uint16_t)tbl_int(L, s, "frames", 1);
    sp->steer.total = (int32_t)(tbl_num(L, s, "total", 0) * MHFU_STEER_TURN / 360);
    sp->steer.dir = (uint16_t)(int32_t)(tbl_num(L, s, "dir", 0) * MHFU_STEER_TURN / 360);
    if (lua_getfield(L, s, "stuck") == LUA_TTABLE) {
        int k = lua_gettop(L);
        sp->stuck_main = (uint8_t)list_at(L, k, 1, sp->stuck_main);
        sp->stuck_sub = (uint8_t)list_at(L, k, 2, sp->stuck_sub);
        sp->stuck_mode = (uint8_t)list_at(L, k, 3, sp->stuck_mode);
    }
    lua_pop(L, 1);
    size_t n = 0;
    lua_getfield(L, s, "curve");
    const char *c = lua_isstring(L, -1) ? lua_tolstring(L, -1, &n) : NULL;
    for (size_t i = 0; c && i + 4 <= n && sp->key_count < MHFU_STEER_KEYS; i += 4) {
        int v = 0;
        for (size_t j = 0; j < 4; j++) {
            int d = hex(c[i + j]);
            luaL_argcheck(L, d >= 0, 2, "steer.curve is hex digits, 4 a key");
            v = v * 16 + d;
        }
        sp->keys[sp->key_count++] = (uint16_t)v;
    }
    lua_pop(L, 2);
}

int lb_em_move(lua_State *L)
{
    int slot = (int)luaL_checkinteger(L, 1);
    luaL_checktype(L, 2, LUA_TTABLE);
    mhfu_move_t mv;
    own_move(L, 2, &mv);
    mhfu_steer_spec_t sp;
    own_steer(L, 2, &sp);
    uint8_t after = (uint8_t)tbl_int(L, 2, "after", MHFU_EM_NO_MOVE);
    lua_pushboolean(L, mhfu_em_move(slot, &mv, &sp, after));
    return 1;
}

int lb_em_moves_clear(lua_State *L)
{
    mhfu_em_moves_clear();
    lua_pushboolean(L, 1);
    return 1;
}

int lb_em_play(lua_State *L)
{
    lua_pushboolean(L, mhfu_em_play((uint32_t)luaL_checkinteger(L, 1),
                                    (int)luaL_checkinteger(L, 2)));
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
