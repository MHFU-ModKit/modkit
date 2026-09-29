/* mhfu.quest_* bindings. */
#include "mhfu/mhfu.h"
#include "lua_host.h"

static int lb_quest_has(lua_State *L)
{
    lua_pushboolean(L, mhfu_quest_has((mhfu_quest_t)luaL_checkinteger(L,1),
                                      (mhfu_monster_type_t)luaL_checkinteger(L,2)));
    return 1;
}
static int lb_quest_replace_monster(lua_State *L)
{
    mhfu_hook_rc_t rc = mhfu_quest_replace_monster(
        (mhfu_quest_t)luaL_checkinteger(L,1),
        (mhfu_monster_type_t)luaL_checkinteger(L,2),
        (mhfu_monster_type_t)luaL_checkinteger(L,3));
    lua_pushboolean(L, rc == MHFU_HOOK_OK);
    return 1;
}
static int lb_quest_add_monster(lua_State *L)
{
    /* quest_add_monster(quest, id [, x, z]) — ADD a 2nd big monster.
     * MUST be called from on_quest_targets_building. x/z 0 = clone source. */
    float x = (float)luaL_optnumber(L, 3, 0.0);
    float z = (float)luaL_optnumber(L, 4, 0.0);
    mhfu_hook_rc_t rc = mhfu_quest_add_monster(
        (mhfu_quest_t)luaL_checkinteger(L,1),
        (mhfu_monster_type_t)luaL_checkinteger(L,2), x, z);
    lua_pushboolean(L, rc == MHFU_HOOK_OK);
    return 1;
}
static int lb_quest_monster_count(lua_State *L)
{
    lua_pushinteger(L, mhfu_quest_monster_count((mhfu_quest_t)luaL_checkinteger(L,1)));
    return 1;
}
static int lb_quest_first_monster(lua_State *L)
{
    lua_pushinteger(L, mhfu_quest_first_monster((mhfu_quest_t)luaL_checkinteger(L,1)));
    return 1;
}

static const luaL_Reg k_api[] = {
    { "quest_has",            lb_quest_has },
    { "quest_monster_count",  lb_quest_monster_count },
    { "quest_first_monster",  lb_quest_first_monster },
    { "quest_replace_monster", lb_quest_replace_monster },
    { "quest_add_monster",    lb_quest_add_monster },
    { 0, 0 },
};

void mhfu_lua_bind_quest(lua_State *L) { luaL_setfuncs(L, k_api, 0); }
