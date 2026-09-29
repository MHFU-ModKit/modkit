/* mhfu.* entity, entity-clone and collision-node bindings, and the MON_* ids. */
#include "mhfu/mhfu.h"
#include "lua_host.h"

static int lb_entity_at  (lua_State *L){ lua_pushinteger(L, (lua_Integer)mhfu_entity_at((int)luaL_checkinteger(L,1))); return 1; }
static int lb_entity_type(lua_State *L){ lua_pushinteger(L, mhfu_entity_monster_type((uint32_t)luaL_checkinteger(L,1))); return 1; }
static int lb_entity_hp  (lua_State *L){ lua_pushinteger(L, mhfu_entity_hp  ((uint32_t)luaL_checkinteger(L,1))); return 1; }
static int lb_entity_size(lua_State *L){ lua_pushnumber (L, mhfu_entity_size((uint32_t)luaL_checkinteger(L,1))); return 1; }
static int lb_entity_set_size(lua_State *L){ mhfu_entity_set_size((uint32_t)luaL_checkinteger(L,1), (float)luaL_checknumber(L,2)); return 0; }
static int lb_entity_alive(lua_State *L){ lua_pushboolean(L, mhfu_entity_is_alive((uint32_t)luaL_checkinteger(L,1))); return 1; }

static int lb_entity_pos(lua_State *L)
{
    mhfu_vec3_t p = mhfu_entity_pos((uint32_t)luaL_checkinteger(L,1));
    lua_pushnumber(L, p.x); lua_pushnumber(L, p.y); lua_pushnumber(L, p.z);
    return 3;
}
static int lb_entity_set_pos(lua_State *L)
{
    mhfu_vec3_t p = { (float)luaL_checknumber(L,2), (float)luaL_checknumber(L,3),
                      (float)luaL_checknumber(L,4) };
    mhfu_entity_set_pos((uint32_t)luaL_checkinteger(L,1), p);
    return 0;
}
static int lb_entity_yaw(lua_State *L){ lua_pushinteger(L, mhfu_entity_yaw((uint32_t)luaL_checkinteger(L,1))); return 1; }
static int lb_entity_set_yaw(lua_State *L){ mhfu_entity_set_yaw((uint32_t)luaL_checkinteger(L,1), (uint16_t)luaL_checkinteger(L,2)); return 0; }
static int lb_entity_ai_state(lua_State *L){ lua_pushinteger(L, mhfu_entity_ai_state((uint32_t)luaL_checkinteger(L,1))); return 1; }
static int lb_entity_set_ai_state(lua_State *L){ mhfu_entity_set_ai_state((uint32_t)luaL_checkinteger(L,1), (uint8_t)luaL_checkinteger(L,2)); return 0; }
static int lb_entity_engaged(lua_State *L){ lua_pushboolean(L, mhfu_entity_engaged((uint32_t)luaL_checkinteger(L,1))); return 1; }
static int lb_entity_set_engaged(lua_State *L){ mhfu_entity_set_engaged((uint32_t)luaL_checkinteger(L,1), lua_toboolean(L,2)); return 0; }
static int lb_entity_calm(lua_State *L){ mhfu_entity_calm((uint32_t)luaL_checkinteger(L,1)); return 0; }
static int lb_entity_section(lua_State *L){ lua_pushinteger(L, mhfu_entity_section((uint32_t)luaL_checkinteger(L,1))); return 1; }
static int lb_entity_set_section(lua_State *L){ mhfu_entity_set_section((uint32_t)luaL_checkinteger(L,1), (uint16_t)luaL_checkinteger(L,2)); return 0; }

static int lb_entity_make_visible(lua_State *L)
{
    mhfu_entity_make_visible((uint32_t)luaL_checkinteger(L,1), (uint16_t)luaL_checkinteger(L,2));
    return 0;
}
static int lb_entity_force_aggro(lua_State *L)
{
    mhfu_vec3_t t = { (float)luaL_checknumber(L,2), (float)luaL_checknumber(L,3),
                      (float)luaL_checknumber(L,4) };
    mhfu_entity_force_aggro((uint32_t)luaL_checkinteger(L,1), t);
    return 0;
}

/* mhfu.entities_of_type(type) -> { ptr, ptr, ... } */
static int lb_entities_of_type(lua_State *L)
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
/* mhfu.entity_clone(src_ptr) -> clone_ptr (0 on failure): a same-species copy,
 * spliced into the update chain and the registry. */
static int lb_entity_clone(lua_State *L)
{
    lua_pushinteger(L, (lua_Integer)mhfu_entity_clone((uint32_t)luaL_checkinteger(L,1)));
    return 1;
}
/* mhfu.node_clone(template_node, clone_ent, uid) -> node_ptr (0 on failure); the
 * collision node is what lets a clone damage the player */
static int lb_node_clone(lua_State *L)
{
    lua_pushinteger(L, (lua_Integer)mhfu_entity_node_clone(
        (uint32_t)luaL_checkinteger(L,1), (uint32_t)luaL_checkinteger(L,2),
        (uint16_t)luaL_checkinteger(L,3)));
    return 1;
}
/* mhfu.node_of(ent) -> node_ptr (ENTITY.COMBAT_NODE), 0 if none */
static int lb_node_of(lua_State *L)
{
    lua_pushinteger(L, (lua_Integer)mhfu_entity_node((uint32_t)luaL_checkinteger(L,1)));
    return 1;
}
/* mhfu.node_linked(node) -> bool */
static int lb_node_linked(lua_State *L)
{
    lua_pushboolean(L, mhfu_entity_node_linked((uint32_t)luaL_checkinteger(L,1)));
    return 1;
}
/* mhfu.node_relink(node) -> bool (re-linked?) */
static int lb_node_relink(lua_State *L)
{
    lua_pushboolean(L, mhfu_entity_node_relink((uint32_t)luaL_checkinteger(L,1)));
    return 1;
}
/* mhfu.node_sync(node, ent) */
static int lb_node_sync(lua_State *L)
{
    mhfu_entity_node_sync((uint32_t)luaL_checkinteger(L,1), (uint32_t)luaL_checkinteger(L,2));
    return 0;
}
/* mhfu.node_detach(node) */
static int lb_node_detach(lua_State *L)
{
    mhfu_entity_node_detach((uint32_t)luaL_checkinteger(L,1));
    return 0;
}

static const luaL_Reg k_api[] = {
    { "entity_at",        lb_entity_at },
    { "entity_type",      lb_entity_type },
    { "entity_hp",        lb_entity_hp },
    { "entity_size",      lb_entity_size },
    { "entity_set_size",  lb_entity_set_size },
    { "entity_alive",     lb_entity_alive },
    { "entity_pos",       lb_entity_pos },
    { "entity_set_pos",   lb_entity_set_pos },
    { "entity_yaw",       lb_entity_yaw },
    { "entity_set_yaw",   lb_entity_set_yaw },
    { "entity_ai_state",  lb_entity_ai_state },
    { "entity_set_ai_state", lb_entity_set_ai_state },
    { "entity_engaged",   lb_entity_engaged },
    { "entity_set_engaged", lb_entity_set_engaged },
    { "entity_calm",      lb_entity_calm },
    { "entity_section",   lb_entity_section },
    { "entity_set_section", lb_entity_set_section },
    { "entity_make_visible", lb_entity_make_visible },
    { "entity_force_aggro", lb_entity_force_aggro },
    { "entity_clone",     lb_entity_clone },
    { "node_clone",       lb_node_clone },
    { "node_of",          lb_node_of },
    { "node_linked",      lb_node_linked },
    { "node_relink",      lb_node_relink },
    { "node_sync",        lb_node_sync },
    { "node_detach",      lb_node_detach },
    { "entities_of_type", lb_entities_of_type },
    { 0, 0 },
};

void mhfu_lua_bind_entity(lua_State *L)
{
    luaL_setfuncs(L, k_api, 0);
    lua_pushinteger(L, MHFU_MONSTER_ANTEKA);   lua_setfield(L, -2, "MON_ANTEKA");
    lua_pushinteger(L, MHFU_MONSTER_POPO);     lua_setfield(L, -2, "MON_POPO");
    lua_pushinteger(L, MHFU_MONSTER_TIGREX);   lua_setfield(L, -2, "MON_TIGREX");
    lua_pushinteger(L, MHFU_MONSTER_GIADROME); lua_setfield(L, -2, "MON_GIADROME");
}
