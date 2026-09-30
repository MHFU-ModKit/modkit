/* mhfu.buttons binding, declared with its CTRL_* masks in lua/meta/mhfu.d.lua. */
#include <pspctrl.h>

#include "mhfu/mhfu.h"
#include "lua_host.h"

int lb_buttons(lua_State *L)
{
    SceCtrlData pad;
    sceCtrlPeekBufferPositive(&pad, 1);
    lua_pushinteger(L, (lua_Integer)pad.Buttons);
    lua_pushinteger(L, (lua_Integer)pad.Lx);
    lua_pushinteger(L, (lua_Integer)pad.Ly);
    return 3;
}
