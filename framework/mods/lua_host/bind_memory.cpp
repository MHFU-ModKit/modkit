/* mhfu.* log, raw memory and world-state bindings. */
#include "mhfu/mhfu.h"
#include "lua_host.h"

int lb_log(lua_State *L) { mhfu_log("%s", luaL_checkstring(L, 1)); return 0; }
int lb_read_u8(lua_State *L) { lua_pushinteger(L, mhfu_mem_read_u8 ((uint32_t)luaL_checkinteger(L,1))); return 1; }
int lb_read_u16(lua_State *L) { lua_pushinteger(L, mhfu_mem_read_u16((uint32_t)luaL_checkinteger(L,1))); return 1; }
int lb_read_u32(lua_State *L) { lua_pushinteger(L, (lua_Integer)mhfu_mem_read_u32((uint32_t)luaL_checkinteger(L,1))); return 1; }
int lb_mem_valid(lua_State *L){ lua_pushboolean(L, mhfu_mem_valid((uint32_t)luaL_checkinteger(L,1))); return 1; }

int lb_write_u8(lua_State *L){ mhfu_mem_write_u8 ((uint32_t)luaL_checkinteger(L,1), (uint8_t )luaL_checkinteger(L,2)); return 0; }
int lb_write_u16(lua_State *L){ mhfu_mem_write_u16((uint32_t)luaL_checkinteger(L,1), (uint16_t)luaL_checkinteger(L,2)); return 0; }
int lb_write_u32(lua_State *L){ mhfu_mem_write_u32((uint32_t)luaL_checkinteger(L,1), (uint32_t)luaL_checkinteger(L,2)); return 0; }

/* f32 cells as Lua numbers */
int lb_read_f32(lua_State *L){ lua_pushnumber(L, mhfu_mem_read_f32((uint32_t)luaL_checkinteger(L,1))); return 1; }
int lb_write_f32(lua_State *L){ mhfu_mem_write_f32((uint32_t)luaL_checkinteger(L,1), (float)luaL_checknumber(L,2)); return 0; }

int lb_get_screen_state(lua_State *L){ lua_pushinteger(L, mhfu_world_screen_state()); return 1; }
int lb_get_area_index(lua_State *L){ lua_pushinteger(L, mhfu_world_area_index());  return 1; }
int lb_get_quest_timer(lua_State *L){ lua_pushinteger(L, (lua_Integer)mhfu_world_quest_timer()); return 1; }
int lb_player_pos(lua_State *L)
{
    mhfu_vec3_t p = mhfu_world_player_pos();
    lua_pushnumber(L, p.x); lua_pushnumber(L, p.y); lua_pushnumber(L, p.z);
    return 3;
}
int lb_get_player_hp(lua_State *L){ lua_pushinteger(L, (lua_Integer)mhfu_world_player_hp()); return 1; }
int lb_paint_map(lua_State *L){ (void)L; mhfu_world_paint_map(); return 0; }
