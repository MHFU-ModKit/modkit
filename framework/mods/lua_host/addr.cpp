/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* mhfu.addr: an entry of addresses.toml becomes a Lua value the first time a script reads it, so
 * the slab holds only the entries scripts use. */
#include <string.h>

#include "mhfu/mhfu.h"
#include "lua_host.h"
#include "addresses_table.gen.inc"   /* k_mhfu_addr */

static void push_entry(lua_State *L, const mhfu_addr_entry *e)
{
    int n = 0;
    if (e->fields) {
        while (e->fields[n].name) n++;
        lua_createtable(L, 0, n);
        for (int i = 0; i < n; i++) {
            lua_pushinteger(L, (lua_Integer)e->fields[i].value);
            lua_setfield(L, -2, e->fields[i].name);
        }
    } else if (e->names) {
        while (e->names[n]) n++;
        lua_createtable(L, n, 0);
        for (int i = 0; i < n; i++) {
            lua_pushstring(L, e->names[i]);
            lua_rawseti(L, -2, i + 1);
        }
    } else {
        lua_pushinteger(L, (lua_Integer)e->value);
    }
}

/* __index: builds the entry and keeps it in mhfu.addr. */
static int addr_index(lua_State *L)
{
    if (lua_type(L, 2) != LUA_TSTRING) return 0;
    const char *key = lua_tostring(L, 2);
    for (const mhfu_addr_entry *e = k_mhfu_addr; e->name; e++) {
        if (strcmp(e->name, key) != 0) continue;
        push_entry(L, e);
        lua_pushvalue(L, 2);
        lua_pushvalue(L, -2);
        lua_rawset(L, 1);
        return 1;
    }
    return 0;
}

/* __pairs: builds every entry, then iterates with the base library's next (upvalue 1). */
static int addr_pairs(lua_State *L)
{
    for (const mhfu_addr_entry *e = k_mhfu_addr; e->name; e++) {
        lua_getfield(L, 1, e->name);
        lua_pop(L, 1);
    }
    lua_pushvalue(L, lua_upvalueindex(1));
    lua_pushvalue(L, 1);
    lua_pushnil(L);
    return 3;
}

void mhfu_lua_addr_install(lua_State *L)
{
    lua_newtable(L);
    lua_createtable(L, 0, 2);
    lua_pushcfunction(L, addr_index);
    lua_setfield(L, -2, "__index");
    lua_getglobal(L, "next");
    lua_pushcclosure(L, addr_pairs, 1);
    lua_setfield(L, -2, "__pairs");
    lua_setmetatable(L, -2);
    lua_setfield(L, -2, "addr");
}
