/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* mhfu.move_* bindings over the move player (src/core/move.cpp); declared in lua/meta/mhfu.d.lua. */
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

static void spec_of(lua_State *L, int t, mhfu_move_t *mv)
{
    mhfu_move_init_spec(mv, (uint16_t)opt_field(L, t, "entry", 0));
    mv->length = (uint16_t)opt_field(L, t, "length", 0);
    mv->part = (uint8_t)opt_field(L, t, "part", 0);
    mv->spawner = (uint32_t)opt_field(L, t, "spawner", 0);
    lua_getfield(L, t, "skip");
    mv->skip = (uint8_t)lua_toboolean(L, -1);
    lua_pop(L, 1);
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

int lb_move_play(lua_State *L)
{
    uint32_t ent = (uint32_t)luaL_checkinteger(L, 1);
    mhfu_move_t mv;
    if (lua_istable(L, 2)) {
        spec_of(L, 2, &mv);
    } else {
        /* a MOVE struct the debugger wrote, word by word */
        uint32_t at = (uint32_t)luaL_checkinteger(L, 2);
        luaL_argcheck(L, (at & 3) == 0, 2, "a MOVE struct is word aligned");
        uint32_t *w = (uint32_t *)&mv;
        for (unsigned k = 0; k < sizeof(mv) / 4; k++) w[k] = mhfu_mem_read_u32(at + 4 * k);
    }
    lua_pushboolean(L, mhfu_move_play(ent, &mv));
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
