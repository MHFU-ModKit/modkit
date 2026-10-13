/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* mhfu.move_* bindings over the move player (src/core/move.cpp), and mhfu.em_move, the own move
 * registry's, which takes the same spec; declared in lua/meta/mhfu.d.lua. */
#include "mhfu/mhfu.h"
#include "lua_host.h"

static int opt_field(lua_State *L, int t, const char *k, int def)
{
    lua_getfield(L, t, k);
    int v = lua_isnil(L, -1) ? def : (int)luaL_checkinteger(L, -1);
    lua_pop(L, 1);
    return v;
}

/* t[k][i] of an integer list, def where missing */
static int list_at(lua_State *L, int t, int i, int def)
{
    lua_rawgeti(L, t, i);
    int v = lua_isnil(L, -1) ? def : (int)luaL_checkinteger(L, -1);
    lua_pop(L, 1);
    return v;
}

/* the move spec (lua/meta/mhfu.d.lua mhfu.Move), the one parser of it */
static void spec_of(lua_State *L, int t, mhfu_move_t *mv)
{
    mhfu_move_init_spec(mv, (uint16_t)opt_field(L, t, "entry", 0));
    mv->length = (uint16_t)opt_field(L, t, "length", 0);
    mv->part = (uint8_t)opt_field(L, t, "part", 0);
    mv->spawner = (uint32_t)opt_field(L, t, "spawner", 0);
    lua_getfield(L, t, "skip");
    mv->skip = (uint8_t)lua_toboolean(L, -1);
    lua_pop(L, 1);
    lua_getfield(L, t, "host_attacks");
    mv->host_attacks = (uint8_t)lua_toboolean(L, -1);
    lua_pop(L, 1);
    if (lua_getfield(L, t, "carrier") == LUA_TTABLE) {
        int c = lua_gettop(L);
        mv->carrier_main = (uint8_t)list_at(L, c, 1, MHFU_MOVE_HUB_MAIN);
        mv->carrier_sub = (uint8_t)list_at(L, c, 2, MHFU_MOVE_HUB_SUB);
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

/* t.steer (mhfu/steer.h): turn = "still" | "hunter" | "away" | "fixed", rate (YAW units a
 * frame), total (degrees) over frames, dir (degrees), walls, stuck = { main, sub, mode }, curve
 * = the entry's string in the port's turns module */
static void steer_of(lua_State *L, int t, mhfu_steer_spec_t *sp)
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
    lua_getfield(L, s, "walls");
    sp->steer.walls = (uint8_t)lua_toboolean(L, -1);
    lua_pop(L, 1);
    sp->steer.rate = (uint16_t)opt_field(L, s, "rate", 64);
    sp->steer.frames = (uint16_t)opt_field(L, s, "frames", 1);
    lua_getfield(L, s, "total");
    sp->steer.total = (int32_t)(luaL_optnumber(L, -1, 0) * MHFU_STEER_TURN / 360);
    lua_pop(L, 1);
    lua_getfield(L, s, "dir");
    sp->steer.dir = (uint16_t)(int32_t)(luaL_optnumber(L, -1, 0) * MHFU_STEER_TURN / 360);
    lua_pop(L, 1);
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

int lb_move_play(lua_State *L)
{
    uint32_t ent = (uint32_t)luaL_checkinteger(L, 1);
    mhfu_move_t mv;
    if (lua_istable(L, 2)) {
        spec_of(L, 2, &mv);
        mv.force = (uint8_t)lua_toboolean(L, 3);
        mhfu_steer_spec_t sp;
        steer_of(L, 2, &sp);
        mhfu_move_steer(&sp);
    } else {
        /* a MOVE struct the debugger wrote, word by word, FORCE included; it wrote
         * STEER_STATE.NEXT itself */
        uint32_t at = (uint32_t)luaL_checkinteger(L, 2);
        luaL_argcheck(L, (at & 3) == 0, 2, "a MOVE struct is word aligned");
        uint32_t *w = (uint32_t *)&mv;
        for (unsigned k = 0; k < sizeof(mv) / 4; k++) w[k] = mhfu_mem_read_u32(at + 4 * k);
    }
    lua_pushboolean(L, mhfu_move_play(ent, &mv));
    return 1;
}

/* an own move: the spec and `after`, the slot played when it ends (mhfu.OwnMove) */
int lb_em_move(lua_State *L)
{
    int slot = (int)luaL_checkinteger(L, 1);
    luaL_checktype(L, 2, LUA_TTABLE);
    mhfu_move_t mv;
    spec_of(L, 2, &mv);
    mhfu_steer_spec_t sp;
    steer_of(L, 2, &sp);
    uint8_t after = (uint8_t)opt_field(L, 2, "after", MHFU_EM_NO_MOVE);
    lua_pushboolean(L, mhfu_em_move(slot, &mv, &sp, after));
    return 1;
}

static const char *const REACTS[] = {"flinch", NULL};

int lb_move_react(lua_State *L)
{
    int kind = luaL_checkoption(L, 1, NULL, REACTS);
    uint32_t ent = (uint32_t)luaL_checkinteger(L, 2);
    if (lua_isnoneornil(L, 3)) {
        lua_pushboolean(L, mhfu_move_react(kind, ent, NULL, NULL));
        return 1;
    }
    luaL_checktype(L, 3, LUA_TTABLE);
    mhfu_move_t mv;
    spec_of(L, 3, &mv);
    mhfu_steer_spec_t sp;
    steer_of(L, 3, &sp);
    lua_pushboolean(L, mhfu_move_react(kind, ent, &mv, &sp));
    return 1;
}

int lb_move_stop(lua_State *L)
{
    mhfu_move_stop();
    lua_pushboolean(L, 1);
    return 1;
}

int lb_move_block(lua_State *L)
{
    lua_pushinteger(L, (lua_Integer)(uintptr_t)mhfu_move_state());
    return 1;
}

static void push_floats(lua_State *L, const volatile float *v, int n, const char *k)
{
    lua_createtable(L, n, 0);
    for (int i = 0; i < n; i++) { lua_pushnumber(L, v[i]); lua_rawseti(L, -2, i + 1); }
    lua_setfield(L, -2, k);
}

int lb_move_status(lua_State *L)
{
    const volatile mhfu_move_state_t *s = mhfu_move_state();
    lua_newtable(L);
    if (!s) return 1;
#define SF_INT(name, v) do { lua_pushinteger(L, (lua_Integer)(v)); lua_setfield(L, -2, name); } while (0)
    SF_INT("started", s->started);
    SF_INT("pending", s->pending);
    SF_INT("state", s->state);
    SF_INT("end_reason", s->end);
    SF_INT("entity", s->entity);
    SF_INT("entry", s->move.entry);
    SF_INT("frames", s->frames);
    SF_INT("skipped", s->skipped);
    SF_INT("end_main", s->end_pair >> 8);
    SF_INT("end_sub", s->end_pair & 0xFF);
    SF_INT("end_frame", s->end_frame);
    SF_INT("waited", s->waited);
    SF_INT("reactions", s->reactions);
    SF_INT("react_entity", s->react_entity);
    SF_INT("react_parts", s->react_parts);
    SF_INT("react_part", s->react_part);
    SF_INT("react_main", (s->react_pair >> 8) & 0xFF);
    SF_INT("react_sub", s->react_pair & 0xFF);
    push_floats(L, s->peak, 3, "peak");
    push_floats(L, s->clip_end, 3, "clip_end");
    lua_createtable(L, s->move.attack_count, 0);
    for (int i = 0; i < s->move.attack_count && i < MHFU_MOVE_MAX_ATTACKS; i++) {
        lua_createtable(L, 0, 5);
        SF_INT("frame", s->spawn_frame[i] == 0xFFFFFFFFu ? -1 : (lua_Integer)s->spawn_frame[i]);
        lua_pushnumber(L, s->spawn_cursor[i]);
        lua_setfield(L, -2, "cursor");
        SF_INT("node", s->spawn_node[i]);
        SF_INT("ended", s->ended_frame[i] == 0xFFFFFFFFu ? -1 : (lua_Integer)s->ended_frame[i]);
        SF_INT("ended_state", s->ended_state[i]);
        lua_rawseti(L, -2, i + 1);
    }
    lua_setfield(L, -2, "spawns");
#undef SF_INT
    return 1;
}
