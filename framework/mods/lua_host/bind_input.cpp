/* mhfu.buttons binding and the CTRL_* button masks. */
#include <pspctrl.h>

#include "mhfu/mhfu.h"
#include "lua_host.h"

/* mhfu.buttons() -> buttons:u32, lx:0..255, ly:0..255: the live controller;
 * the stick centres near 128, buttons are the mhfu.CTRL_* bits. */
static int lb_buttons(lua_State *L)
{
    SceCtrlData pad;
    sceCtrlPeekBufferPositive(&pad, 1);
    lua_pushinteger(L, (lua_Integer)pad.Buttons);
    lua_pushinteger(L, (lua_Integer)pad.Lx);
    lua_pushinteger(L, (lua_Integer)pad.Ly);
    return 3;
}

static const luaL_Reg k_api[] = {
    { "buttons",          lb_buttons },
    { 0, 0 },
};

void mhfu_lua_bind_input(lua_State *L)
{
    luaL_setfuncs(L, k_api, 0);
    lua_pushinteger(L, PSP_CTRL_SELECT);   lua_setfield(L, -2, "CTRL_SELECT");
    lua_pushinteger(L, PSP_CTRL_START);    lua_setfield(L, -2, "CTRL_START");
    lua_pushinteger(L, PSP_CTRL_UP);       lua_setfield(L, -2, "CTRL_UP");
    lua_pushinteger(L, PSP_CTRL_RIGHT);    lua_setfield(L, -2, "CTRL_RIGHT");
    lua_pushinteger(L, PSP_CTRL_DOWN);     lua_setfield(L, -2, "CTRL_DOWN");
    lua_pushinteger(L, PSP_CTRL_LEFT);     lua_setfield(L, -2, "CTRL_LEFT");
    lua_pushinteger(L, PSP_CTRL_LTRIGGER); lua_setfield(L, -2, "CTRL_L");
    lua_pushinteger(L, PSP_CTRL_RTRIGGER); lua_setfield(L, -2, "CTRL_R");
    lua_pushinteger(L, PSP_CTRL_TRIANGLE); lua_setfield(L, -2, "CTRL_TRIANGLE");
    lua_pushinteger(L, PSP_CTRL_CIRCLE);   lua_setfield(L, -2, "CTRL_CIRCLE");
    lua_pushinteger(L, PSP_CTRL_CROSS);    lua_setfield(L, -2, "CTRL_CROSS");
    lua_pushinteger(L, PSP_CTRL_SQUARE);   lua_setfield(L, -2, "CTRL_SQUARE");
}
