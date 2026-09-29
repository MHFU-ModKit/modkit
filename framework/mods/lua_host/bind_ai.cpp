/* mhfu.action_ptr_for binding. */
#include "mhfu/mhfu.h"
#include "lua_host.h"

static int lb_action_ptr_for(lua_State *L)
{
    lua_pushinteger(L, (lua_Integer)mhfu_ai_action_ptr_for(
        (uint8_t)luaL_checkinteger(L,1), (uint16_t)luaL_checkinteger(L,2)));
    return 1;
}

static const luaL_Reg k_api[] = {
    { "action_ptr_for",   lb_action_ptr_for },
    { 0, 0 },
};

void mhfu_lua_bind_ai(lua_State *L) { luaL_setfuncs(L, k_api, 0); }
