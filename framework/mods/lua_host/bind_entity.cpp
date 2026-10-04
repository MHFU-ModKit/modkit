/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* mhfu.* entity, entity-clone and collision-node bindings; declared in lua/meta/mhfu.d.lua. */
#include "mhfu/mhfu.h"
#include "lua_host.h"

int lb_entity_at(lua_State *L){ lua_pushinteger(L, (lua_Integer)mhfu_entity_at((int)luaL_checkinteger(L,1))); return 1; }
int lb_entity_type(lua_State *L){ lua_pushinteger(L, mhfu_entity_monster_type((uint32_t)luaL_checkinteger(L,1))); return 1; }
int lb_entity_hp(lua_State *L){ lua_pushinteger(L, mhfu_entity_hp  ((uint32_t)luaL_checkinteger(L,1))); return 1; }
int lb_entity_size(lua_State *L){ lua_pushnumber (L, mhfu_entity_size((uint32_t)luaL_checkinteger(L,1))); return 1; }
int lb_entity_set_size(lua_State *L){ mhfu_entity_set_size((uint32_t)luaL_checkinteger(L,1), (float)luaL_checknumber(L,2)); return 0; }
int lb_entity_alive(lua_State *L){ lua_pushboolean(L, mhfu_entity_is_alive((uint32_t)luaL_checkinteger(L,1))); return 1; }

int lb_entity_pos(lua_State *L)
{
    mhfu_vec3_t p = mhfu_entity_pos((uint32_t)luaL_checkinteger(L,1));
    lua_pushnumber(L, p.x); lua_pushnumber(L, p.y); lua_pushnumber(L, p.z);
    return 3;
}
int lb_entity_set_pos(lua_State *L)
{
    mhfu_vec3_t p = { (float)luaL_checknumber(L,2), (float)luaL_checknumber(L,3),
                      (float)luaL_checknumber(L,4) };
    mhfu_entity_set_pos((uint32_t)luaL_checkinteger(L,1), p);
    return 0;
}
int lb_entity_yaw(lua_State *L){ lua_pushinteger(L, mhfu_entity_yaw((uint32_t)luaL_checkinteger(L,1))); return 1; }
int lb_entity_set_yaw(lua_State *L){ mhfu_entity_set_yaw((uint32_t)luaL_checkinteger(L,1), (uint16_t)luaL_checkinteger(L,2)); return 0; }
int lb_entity_ai_state(lua_State *L){ lua_pushinteger(L, mhfu_entity_ai_state((uint32_t)luaL_checkinteger(L,1))); return 1; }
int lb_entity_set_ai_state(lua_State *L){ mhfu_entity_set_ai_state((uint32_t)luaL_checkinteger(L,1), (uint8_t)luaL_checkinteger(L,2)); return 0; }
int lb_entity_engaged(lua_State *L){ lua_pushboolean(L, mhfu_entity_engaged((uint32_t)luaL_checkinteger(L,1))); return 1; }
int lb_entity_set_engaged(lua_State *L){ mhfu_entity_set_engaged((uint32_t)luaL_checkinteger(L,1), lua_toboolean(L,2)); return 0; }
int lb_entity_calm(lua_State *L){ mhfu_entity_calm((uint32_t)luaL_checkinteger(L,1)); return 0; }
int lb_entity_section(lua_State *L){ lua_pushinteger(L, mhfu_entity_section((uint32_t)luaL_checkinteger(L,1))); return 1; }
int lb_entity_set_section(lua_State *L){ mhfu_entity_set_section((uint32_t)luaL_checkinteger(L,1), (uint16_t)luaL_checkinteger(L,2)); return 0; }

int lb_entity_make_visible(lua_State *L)
{
    mhfu_entity_make_visible((uint32_t)luaL_checkinteger(L,1), (uint16_t)luaL_checkinteger(L,2));
    return 0;
}
int lb_entity_force_aggro(lua_State *L)
{
    mhfu_vec3_t t = { (float)luaL_checknumber(L,2), (float)luaL_checknumber(L,3),
                      (float)luaL_checknumber(L,4) };
    mhfu_entity_force_aggro((uint32_t)luaL_checkinteger(L,1), t);
    return 0;
}

int lb_entities_of_type(lua_State *L)
{
    int type = (int)luaL_checkinteger(L, 1);
    uint32_t buf[16];
    int n = mhfu_entity_list((mhfu_monster_type_t)type, buf, 16);
    lua_createtable(L, n, 0);
    for (int i = 0; i < n; i++) {
        lua_pushinteger(L, (lua_Integer)buf[i]);
        lua_rawseti(L, -2, i + 1);
    }
    return 1;
}
int lb_entity_clone(lua_State *L)
{
    lua_pushinteger(L, (lua_Integer)mhfu_entity_clone((uint32_t)luaL_checkinteger(L,1)));
    return 1;
}
int lb_node_clone(lua_State *L)
{
    lua_pushinteger(L, (lua_Integer)mhfu_entity_node_clone(
        (uint32_t)luaL_checkinteger(L,1), (uint32_t)luaL_checkinteger(L,2),
        (uint16_t)luaL_checkinteger(L,3)));
    return 1;
}
int lb_node_of(lua_State *L)
{
    lua_pushinteger(L, (lua_Integer)mhfu_entity_node((uint32_t)luaL_checkinteger(L,1)));
    return 1;
}
int lb_node_linked(lua_State *L)
{
    lua_pushboolean(L, mhfu_entity_node_linked((uint32_t)luaL_checkinteger(L,1)));
    return 1;
}
int lb_node_relink(lua_State *L)
{
    lua_pushboolean(L, mhfu_entity_node_relink((uint32_t)luaL_checkinteger(L,1)));
    return 1;
}
int lb_node_sync(lua_State *L)
{
    mhfu_entity_node_sync((uint32_t)luaL_checkinteger(L,1), (uint32_t)luaL_checkinteger(L,2));
    return 0;
}
int lb_node_detach(lua_State *L)
{
    mhfu_entity_node_detach((uint32_t)luaL_checkinteger(L,1));
    return 0;
}
