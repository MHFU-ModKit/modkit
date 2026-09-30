/* mhfu.buttons binding; the CTRL_* masks are declared in lua/meta/mhfu.d.lua. */
#include <pspctrl.h>

#include "mhfu/mhfu.h"
#include "lua_host.h"

/* mhfu.buttons() -> buttons:u32, lx:0..255, ly:0..255: the live controller;
 * the stick centres near 128, buttons are the mhfu.CTRL_* bits. */
int lb_buttons(lua_State *L)
{
    SceCtrlData pad;
    sceCtrlPeekBufferPositive(&pad, 1);
    lua_pushinteger(L, (lua_Integer)pad.Buttons);
    lua_pushinteger(L, (lua_Integer)pad.Lx);
    lua_pushinteger(L, (lua_Integer)pad.Ly);
    return 3;
}
