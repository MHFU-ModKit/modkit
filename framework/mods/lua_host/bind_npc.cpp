/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* mhfu.npc_* bindings over the village NPCs (src/core/npc.cpp); declared in lua/meta/mhfu.d.lua. */
#include "mhfu/mhfu.h"
#include "lua_host.h"

static lua_Number opt_number(lua_State *L, int t, const char *k, lua_Number def)
{
    lua_getfield(L, t, k);
    lua_Number v = luaL_optnumber(L, -1, def);
    lua_pop(L, 1);
    return v;
}

/* the path as inject_register takes it */
int lb_npc_add(lua_State *L)
{
    mhfu_npc_spec_t spec;
    spec.pac = luaL_checkstring(L, 1);
    spec.size = 1;
    spec.character = MHFU_NPC_DEFAULT_CHAR;
    spec.right = 300;
    spec.ahead = 0;
    if (lua_istable(L, 2)) {
        spec.size = (float)opt_number(L, 2, "size", spec.size);
        spec.character = (uint8_t)opt_number(L, 2, "char", spec.character);
        spec.right = (float)opt_number(L, 2, "right", spec.right);
        spec.ahead = (float)opt_number(L, 2, "ahead", spec.ahead);
    }
    int slot = mhfu_npc_add(&spec);
    if (slot < 0) {
        lua_pushnil(L);
        lua_pushfstring(L, "npc_add %s: %d (framework.log says why)", spec.pac, slot);
        return 2;
    }
    lua_pushinteger(L, slot);
    return 1;
}

int lb_npc_object(lua_State *L)
{
    lua_pushinteger(L, (lua_Integer)mhfu_npc_object((int)luaL_checkinteger(L, 1)));
    return 1;
}

int lb_npc_play(lua_State *L)
{
    int slot = (int)luaL_checkinteger(L, 1);
    uint16_t entry = (uint16_t)luaL_checkinteger(L, 2);
    uint8_t blend = (uint8_t)luaL_optinteger(L, 3, 6);
    uint16_t then = (uint16_t)luaL_optinteger(L, 4, MHFU_NPC_NONE);
    mhfu_npc_play(slot, entry, blend, then);
    return 0;
}

static const char *const FACES[] = {"still", "point", "hunter", NULL};
static_assert(MHFU_NPC_FACE_STILL == 0 && MHFU_NPC_FACE_POINT == 1 && MHFU_NPC_FACE_HUNTER == 2,
              "FACES is in MHFU_NPC_FACE_* order");

int lb_npc_face(lua_State *L)
{
    int slot = (int)luaL_checkinteger(L, 1);
    int mode = luaL_checkoption(L, 2, NULL, FACES);
    float x = (float)luaL_optnumber(L, 3, 0);
    float z = (float)luaL_optnumber(L, 4, 0);
    uint16_t rate = (uint16_t)luaL_optinteger(L, 5, 0x200);
    mhfu_npc_face(slot, mode, x, z, rate);
    return 0;
}

int lb_npc_arrive(lua_State *L)
{
    int slot = (int)luaL_checkinteger(L, 1);
    float dist = (float)luaL_checknumber(L, 2);
    uint16_t entry = (uint16_t)luaL_checkinteger(L, 3);
    uint8_t blend = (uint8_t)luaL_optinteger(L, 4, 6);
    uint16_t then = (uint16_t)luaL_optinteger(L, 5, MHFU_NPC_NONE);
    mhfu_npc_arrive(slot, dist, entry, blend, then);
    return 0;
}

int lb_npc_status(lua_State *L)
{
    mhfu_npc_status_t st;
    if (!mhfu_npc_status((int)luaL_checkinteger(L, 1), &st)) {
        lua_pushnil(L);
        return 1;
    }
    lua_createtable(L, 0, 9);
    lua_pushinteger(L, (lua_Integer)st.object);
    lua_setfield(L, -2, "object");
    lua_pushinteger(L, (lua_Integer)st.frames);
    lua_setfield(L, -2, "frames");
    lua_pushinteger(L, st.entry);
    lua_setfield(L, -2, "entry");
    lua_pushboolean(L, st.playing);
    lua_setfield(L, -2, "playing");
    lua_pushinteger(L, st.yaw);
    lua_setfield(L, -2, "yaw");
    lua_pushnumber(L, st.pos.x);
    lua_setfield(L, -2, "x");
    lua_pushnumber(L, st.pos.y);
    lua_setfield(L, -2, "y");
    lua_pushnumber(L, st.pos.z);
    lua_setfield(L, -2, "z");
    lua_pushnumber(L, st.dist);
    lua_setfield(L, -2, "dist");
    return 1;
}
