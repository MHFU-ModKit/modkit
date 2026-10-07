/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* mhfu.on_bigmonster_<event> over the monster events (mhfu/monster_events.h); declared in
 * lua/meta/mhfu.d.lua. They arrive on the registry poll thread, which enters the VM directly. */
#include "mhfu/mhfu.h"
#include "mhfu/monster_events.h"
#include "lua_host.h"

#define KINDS (MHFU_MONSTER_TAIL_CUT - MHFU_MONSTER_NOTICED + 1)

static int r_kind[KINDS] = {LUA_NOREF, LUA_NOREF, LUA_NOREF, LUA_NOREF, LUA_NOREF, LUA_NOREF};

static const char *const *const k_name = mhfu_monster_event_names;

static void set_int(lua_State *L, const char *k, lua_Integer v)
{
    lua_pushinteger(L, v);
    lua_setfield(L, -2, k);
}

/* the mhfu.MonsterEvent table */
static void push_event(lua_State *L, const mhfu_monster_event_ctx_t *c)
{
    const mhfu_monster_event_t *e = &c->ev;
    lua_createtable(L, 0, 11);
    set_int(L, "entity", (lua_Integer)e->entity);
    lua_pushstring(L, k_name[e->kind - MHFU_MONSTER_NOTICED]);
    lua_setfield(L, -2, "kind");
    set_int(L, "frame", (lua_Integer)e->frame);
    set_int(L, "usec", (lua_Integer)e->usec);
    set_int(L, "delay", (lua_Integer)c->delay);
    set_int(L, "main", e->main_state);
    set_int(L, "sub", e->sub_state);
    set_int(L, "data", e->data);
    if (e->part != MHFU_MONSTER_NO_PART) set_int(L, "part", e->part);
}

static void tramp(const mhfu_monster_event_ctx_t *c)
{
    int k = c->ev.kind - MHFU_MONSTER_NOTICED;
    if (k < 0 || k >= KINDS || r_kind[k] == LUA_NOREF || !mhfu_lua_enter()) return;
    lua_State *L = mhfu_lua_vm;
    lua_rawgeti(L, LUA_REGISTRYINDEX, r_kind[k]);
    push_event(L, c);
    if (lua_pcall(L, 1, 0, 0) != LUA_OK) {
        mhfu_log("[lua_host] %s err: %s", k_name[k],
                 lua_type(L, -1) == LUA_TSTRING ? lua_tostring(L, -1) : "(not a string)");
        lua_pop(L, 1);
    }
    mhfu_lua_leave();
}

/* fn at stack index 1 replaces the kind's handler; the trampoline subscribes once */
static int on(lua_State *L, int kind)
{
    int k = kind - MHFU_MONSTER_NOTICED;
    luaL_checktype(L, 1, LUA_TFUNCTION);
    if (r_kind[k] != LUA_NOREF) luaL_unref(L, LUA_REGISTRYINDEX, r_kind[k]);
    lua_pushvalue(L, 1);
    r_kind[k] = luaL_ref(L, LUA_REGISTRYINDEX);
    mhfu_event_id_t id = (mhfu_event_id_t)(MHFU_EVENT_BIGMONSTER_NOTICED + k);
    mhfu_hook_rc_t rc = mhfu_on_monster_event(id, tramp, (int)luaL_optinteger(L, 2, 0),
                                              MHFU_LUA_HOST_ID);
    if (rc != MHFU_HOOK_OK)
        mhfu_log("[lua_host] mhfu.on_bigmonster_%s: not subscribed (rc=%d)", k_name[k], (int)rc);
    return 0;
}

int lb_on_bigmonster_noticed(lua_State *L)        { return on(L, MHFU_MONSTER_NOTICED); }
int lb_on_bigmonster_combat_entered(lua_State *L) { return on(L, MHFU_MONSTER_COMBAT_ENTERED); }
int lb_on_bigmonster_combat_left(lua_State *L)    { return on(L, MHFU_MONSTER_COMBAT_LEFT); }
int lb_on_bigmonster_flinch(lua_State *L)         { return on(L, MHFU_MONSTER_FLINCH); }
int lb_on_bigmonster_part_broken(lua_State *L)    { return on(L, MHFU_MONSTER_PART_BROKEN); }
int lb_on_bigmonster_tail_cut(lua_State *L)       { return on(L, MHFU_MONSTER_TAIL_CUT); }

/* mhfu.MonsterState of ent, nil for none */
int lb_monster_state(lua_State *L)
{
    mhfu_monster_state_t st;
    if (!mhfu_monster_state((uint32_t)luaL_checkinteger(L, 1), &st)) {
        lua_pushnil(L);
        return 1;
    }
    lua_createtable(L, 0, 9);
    lua_pushboolean(L, st.aware);    lua_setfield(L, -2, "aware");
    lua_pushboolean(L, st.combat);   lua_setfield(L, -2, "combat");
    lua_pushboolean(L, st.noticing); lua_setfield(L, -2, "noticing");
    lua_pushboolean(L, st.dead);     lua_setfield(L, -2, "dead");
    lua_pushboolean(L, st.severed);  lua_setfield(L, -2, "severed");
    set_int(L, "main", st.main_state);
    set_int(L, "sub", st.sub_state);
    set_int(L, "flinched", st.flinched);
    set_int(L, "broken", st.broken);
    return 1;
}

/* the block (struct MONSTER_EVENTS), for a debugger */
int lb_monster_events_block(lua_State *L)
{
    lua_pushinteger(L, (lua_Integer)(uintptr_t)mhfu_monster_events());
    return 1;
}
