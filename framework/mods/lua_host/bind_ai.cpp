/* mhfu.action_ptr_for binding; declared in lua/meta/mhfu.d.lua. */
#include "mhfu/mhfu.h"
#include "lua_host.h"

int lb_action_ptr_for(lua_State *L)
{
    lua_pushinteger(L, (lua_Integer)mhfu_ai_action_ptr_for(
        (uint8_t)luaL_checkinteger(L,1), (uint16_t)luaL_checkinteger(L,2)));
    return 1;
}
