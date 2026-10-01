/* mhfu.quest_* bindings; declared in lua/meta/mhfu.d.lua. */
#include "mhfu/mhfu.h"
#include "lua_host.h"

int lb_quest_has(lua_State *L)
{
    lua_pushboolean(L, mhfu_quest_has((mhfu_quest_t)luaL_checkinteger(L,1),
                                      (mhfu_monster_type_t)luaL_checkinteger(L,2)));
    return 1;
}
int lb_quest_replace_monster(lua_State *L)
{
    mhfu_hook_rc_t rc = mhfu_quest_replace_monster(
        (mhfu_quest_t)luaL_checkinteger(L,1),
        (mhfu_monster_type_t)luaL_checkinteger(L,2),
        (mhfu_monster_type_t)luaL_checkinteger(L,3));
    lua_pushboolean(L, rc == MHFU_HOOK_OK);
    return 1;
}
int lb_quest_add_monster(lua_State *L)
{
    float x = (float)luaL_optnumber(L, 3, 0.0);
    float z = (float)luaL_optnumber(L, 4, 0.0);
    mhfu_hook_rc_t rc = mhfu_quest_add_monster(
        (mhfu_quest_t)luaL_checkinteger(L,1),
        (mhfu_monster_type_t)luaL_checkinteger(L,2), x, z);
    lua_pushboolean(L, rc == MHFU_HOOK_OK);
    return 1;
}
int lb_quest_monster_count(lua_State *L)
{
    lua_pushinteger(L, mhfu_quest_monster_count((mhfu_quest_t)luaL_checkinteger(L,1)));
    return 1;
}
int lb_quest_first_monster(lua_State *L)
{
    lua_pushinteger(L, mhfu_quest_first_monster((mhfu_quest_t)luaL_checkinteger(L,1)));
    return 1;
}
