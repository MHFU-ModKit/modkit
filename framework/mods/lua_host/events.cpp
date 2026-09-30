/* The event bridge: mhfu.on_<event>(fn [, priority]) stores fn and subscribes a C trampoline
 * (owner lua_host); game-thread events run their Lua on the exec thread (marshal.h). */
#include "mhfu/mhfu.h"
#include "lua_host.h"

static int r_quest   = LUA_NOREF;       /* registry ref to the Lua handler */
static int r_spawn   = LUA_NOREF;
static int r_death   = LUA_NOREF;
static int r_damaged = LUA_NOREF;
static int r_overlay = LUA_NOREF;
static int r_slot    = LUA_NOREF;
static int r_input   = LUA_NOREF;
static int r_decided = LUA_NOREF;
static int r_action  = LUA_NOREF;

/* Replace the stored ref with the function at stack index 1; this is what makes a
 * hot reload rebind the running hooks to the fresh closures. */
static void store_ref(lua_State *L, int *slot)
{
    luaL_checktype(L, 1, LUA_TFUNCTION);
    if (*slot != LUA_NOREF) luaL_unref(L, LUA_REGISTRYINDEX, *slot);
    lua_pushvalue(L, 1);
    *slot = luaL_ref(L, LUA_REGISTRYINDEX);
}

/* An error's text without converting it: a conversion allocates, which may fail. */
static const char *err_text(lua_State *L)
{
    return lua_type(L, -1) == LUA_TSTRING ? lua_tostring(L, -1) : "(not a string)";
}

/* Push the action ctx table { entity, type, slot, input }. */
static void push_action_ctx(lua_State *L, uint32_t ent, uint8_t type,
                            uint8_t slot, uint16_t input)
{
    lua_createtable(L, 0, 4);
    lua_pushinteger(L, (lua_Integer)ent);   lua_setfield(L, -2, "entity");
    lua_pushinteger(L, type);               lua_setfield(L, -2, "type");
    lua_pushinteger(L, slot);               lua_setfield(L, -2, "slot");
    lua_pushinteger(L, input);              lua_setfield(L, -2, "input");
}

/* ---- requests: game-thread events marshal, poll-thread events enter directly ---- */
enum { REQ_NONE = 0, REQ_INPUT, REQ_DECIDED, REQ_SLOT, REQ_OVERLAY, REQ_QUEST,
       REQ_ACTIONSEL };

static const char *const k_req_name[] = {
    "?", "action_input", "action_decided", "slot_picked", "overlay_loaded",
    "quest_targets_building", "action",
};

/* ---- one request's Lua, run by lua_pcall so an allocation failure is an error, not a
 * panic that would park the exec thread with the game thread still waiting ---- */

static int run_request(lua_State *L)
{
    mhfu_lua_req_t *q = (mhfu_lua_req_t *)lua_touserdata(L, 1);
    switch (q->kind) {
    case REQ_INPUT:     /* function(ctx) -> new vt8_input */
        lua_rawgeti(L, LUA_REGISTRYINDEX, r_input);
        push_action_ctx(L, q->entity, q->mtype, q->slot, (uint16_t)q->in);
        lua_call(L, 1, 1);
        if (lua_isnumber(L, -1)) q->out = (uint16_t)lua_tointeger(L, -1);
        break;
    case REQ_DECIDED:   /* function(ctx, engine_value) -> value */
        lua_rawgeti(L, LUA_REGISTRYINDEX, r_decided);
        push_action_ctx(L, q->entity, q->mtype, q->slot, q->aux);
        lua_pushinteger(L, (lua_Integer)q->in);
        lua_call(L, 2, 1);
        if (lua_isnumber(L, -1)) q->out = (uint32_t)lua_tointeger(L, -1);
        break;
    case REQ_ACTIONSEL: /* function({ entity, type, action_id }) -> new action id, which
                         * the executor fans out to every body slot itself */
        lua_rawgeti(L, LUA_REGISTRYINDEX, r_action);
        lua_createtable(L, 0, 3);
        lua_pushinteger(L, (lua_Integer)q->entity); lua_setfield(L, -2, "entity");
        lua_pushinteger(L, q->mtype);               lua_setfield(L, -2, "type");
        lua_pushinteger(L, (lua_Integer)q->in);     lua_setfield(L, -2, "action_id");
        lua_call(L, 1, 1);
        if (lua_isnumber(L, -1)) q->out = (uint32_t)lua_tointeger(L, -1);
        break;
    case REQ_SLOT:      /* function({ entity, type, slot, count }, cur) -> slot */
        lua_rawgeti(L, LUA_REGISTRYINDEX, r_slot);
        lua_createtable(L, 0, 4);
        lua_pushinteger(L, (lua_Integer)q->entity); lua_setfield(L, -2, "entity");
        lua_pushinteger(L, q->mtype);               lua_setfield(L, -2, "type");
        lua_pushinteger(L, q->slot);                lua_setfield(L, -2, "slot");
        lua_pushinteger(L, q->aux);                 lua_setfield(L, -2, "count");
        lua_pushinteger(L, (lua_Integer)q->in);
        lua_call(L, 2, 1);
        if (lua_isnumber(L, -1)) {
            lua_Integer s = lua_tointeger(L, -1);
            if (s >= 0 && s < (lua_Integer)q->aux) q->out = (uint8_t)s;   /* else a crash */
        }
        break;
    case REQ_OVERLAY:
        lua_rawgeti(L, LUA_REGISTRYINDEX, r_overlay);
        lua_call(L, 0, 0);
        break;
    case REQ_QUEST:     /* function(quest_ptr) */
        lua_rawgeti(L, LUA_REGISTRYINDEX, r_quest);
        lua_pushinteger(L, (lua_Integer)q->entity);
        lua_call(L, 1, 0);
        break;
    default:
        break;
    }
    return 0;
}

void mhfu_lua_serve(mhfu_lua_req_t *q)
{
    if (!mhfu_lua_enter()) return;
    lua_State *L = mhfu_lua_vm;
    lua_pushcfunction(L, run_request);
    lua_pushlightuserdata(L, q);
    if (lua_pcall(L, 1, 0, 0) != LUA_OK) {
        mhfu_log("[lua_host] %s err: %s", k_req_name[q->kind], err_text(L));
        lua_pop(L, 1);
        q->out = q->in;
    }
    mhfu_lua_leave();
}

static uint32_t marshal(int kind, uint32_t entity, uint8_t mtype,
                        uint8_t slot, uint16_t aux, uint32_t in)
{
    mhfu_lua_req_t q;
    q.kind   = kind;
    q.entity = entity;
    q.mtype  = mtype;
    q.slot   = slot;
    q.aux    = aux;
    q.in     = in;
    return mhfu_lua_marshal(&q);
}

/* ---- trampolines ---- */

static uint32_t tramp_decided(const mhfu_bigmonster_action_decided_ctx_t *ctx,
                              uint32_t engine_value)
{
    if (r_decided == LUA_NOREF) return engine_value;
    return marshal(REQ_DECIDED, ctx->entity_ptr, ctx->monster_type, ctx->slot,
                   ctx->vt8_input, engine_value);
}

static uint16_t tramp_input(const mhfu_bigmonster_action_input_ctx_t *ctx, uint16_t cur)
{
    if (r_input == LUA_NOREF) return cur;
    return (uint16_t)marshal(REQ_INPUT, ctx->entity_ptr, ctx->monster_type,
                             ctx->slot, 0, cur);
}

/* fires at the executor's entry */
static uint32_t tramp_action(const mhfu_bigmonster_action_ctx_t *ctx, uint32_t a1)
{
    if (r_action == LUA_NOREF) return a1;
    return marshal(REQ_ACTIONSEL, ctx->entity_ptr, ctx->monster_type, 0, 0, a1);
}

static uint8_t tramp_slot(const mhfu_bigmonster_slot_picked_ctx_t *ctx, uint8_t cur)
{
    if (r_slot == LUA_NOREF) return cur;
    return (uint8_t)marshal(REQ_SLOT, ctx->entity_ptr, ctx->monster_type,
                            ctx->original_slot, ctx->action_count, cur);
}

static void tramp_overlay(const mhfu_ai_overlay_loaded_ctx_t *ctx)
{
    (void)ctx;
    if (r_overlay == LUA_NOREF) return;
    marshal(REQ_OVERLAY, 0, 0, 0, 0, 0);
}

static void tramp_spawn(const mhfu_bigmonster_spawn_ctx_t *ctx)
{
    if (r_spawn == LUA_NOREF || !mhfu_lua_enter()) return;   /* poll thread = direct */
    lua_State *L = mhfu_lua_vm;
    lua_rawgeti(L, LUA_REGISTRYINDEX, r_spawn);
    lua_pushinteger(L, (lua_Integer)ctx->entity_ptr);
    lua_pushinteger(L, ctx->monster_type);
    lua_pushinteger(L, ctx->slot);
    lua_pushinteger(L, ctx->initial_hp);
    if (lua_pcall(L, 4, 0, 0) != LUA_OK) {
        mhfu_log("[lua_host] spawn err: %s", err_text(L));
        lua_pop(L, 1);
    }
    mhfu_lua_leave();
}

static void tramp_death(const mhfu_bigmonster_death_ctx_t *ctx)
{
    if (r_death == LUA_NOREF || !mhfu_lua_enter()) return;
    lua_State *L = mhfu_lua_vm;
    lua_rawgeti(L, LUA_REGISTRYINDEX, r_death);
    lua_pushinteger(L, (lua_Integer)ctx->entity_ptr);
    lua_pushinteger(L, ctx->monster_type);
    lua_pushinteger(L, ctx->slot);
    if (lua_pcall(L, 3, 0, 0) != LUA_OK) {
        mhfu_log("[lua_host] death err: %s", err_text(L));
        lua_pop(L, 1);
    }
    mhfu_lua_leave();
}

/* poll thread; Lua: function(entity_ptr, monster_type, amount, hp, slot) */
static void tramp_damaged(const mhfu_bigmonster_damaged_ctx_t *ctx)
{
    if (r_damaged == LUA_NOREF || !mhfu_lua_enter()) return;
    lua_State *L = mhfu_lua_vm;
    lua_rawgeti(L, LUA_REGISTRYINDEX, r_damaged);
    lua_pushinteger(L, (lua_Integer)ctx->entity_ptr);
    lua_pushinteger(L, ctx->monster_type);
    lua_pushinteger(L, ctx->amount);
    lua_pushinteger(L, ctx->hp);
    lua_pushinteger(L, ctx->slot);
    if (lua_pcall(L, 5, 0, 0) != LUA_OK) {
        mhfu_log("[lua_host] damaged err: %s", err_text(L));
        lua_pop(L, 1);
    }
    mhfu_lua_leave();
}

/* the quest-load thread is a game thread, so this one marshals too */
static void tramp_quest(const mhfu_quest_ctx_t *ctx)
{
    if (r_quest == LUA_NOREF) return;
    marshal(REQ_QUEST, (uint32_t)ctx->quest, 0, 0, 0, 0);
}

/* ---- mhfu.on_<event>(fn [, priority]): a second call rebinds fn; the first priority stays ---- */

static int prio(lua_State *L) { return (int)luaL_optinteger(L, 2, 0); }

static void check(const char *api, mhfu_hook_rc_t rc)
{
    if (rc != MHFU_HOOK_OK) mhfu_log("[lua_host] mhfu.%s: not subscribed (rc=%d)", api, (int)rc);
}

static int lb_on_quest(lua_State *L)
{
    store_ref(L, &r_quest);
    check("on_quest_targets_building",
          mhfu_on_quest_targets_building(tramp_quest, prio(L), MHFU_LUA_HOST_ID));
    return 0;
}
static int lb_on_spawn(lua_State *L)
{
    store_ref(L, &r_spawn);
    check("on_bigmonster_spawn", mhfu_on_bigmonster_spawn(tramp_spawn, prio(L), MHFU_LUA_HOST_ID));
    return 0;
}
static int lb_on_death(lua_State *L)
{
    store_ref(L, &r_death);
    check("on_bigmonster_death", mhfu_on_bigmonster_death(tramp_death, prio(L), MHFU_LUA_HOST_ID));
    return 0;
}
static int lb_on_damaged(lua_State *L)
{
    store_ref(L, &r_damaged);
    check("on_bigmonster_damaged",
          mhfu_on_bigmonster_damaged(tramp_damaged, prio(L), MHFU_LUA_HOST_ID));
    return 0;
}
static int lb_on_overlay(lua_State *L)
{
    store_ref(L, &r_overlay);
    check("on_ai_overlay_loaded",
          mhfu_on_ai_overlay_loaded(tramp_overlay, prio(L), MHFU_LUA_HOST_ID));
    return 0;
}
static int lb_on_slot(lua_State *L)
{
    store_ref(L, &r_slot);
    check("on_bigmonster_slot_picked",
          mhfu_on_bigmonster_slot_picked(tramp_slot, prio(L), MHFU_LUA_HOST_ID));
    return 0;
}
static int lb_on_input(lua_State *L)
{
    store_ref(L, &r_input);
    check("on_bigmonster_action_input",
          mhfu_on_bigmonster_action_input(tramp_input, prio(L), MHFU_LUA_HOST_ID));
    return 0;
}
static int lb_on_decided(lua_State *L)
{
    store_ref(L, &r_decided);
    check("on_bigmonster_action_decided",
          mhfu_on_bigmonster_action_decided(tramp_decided, prio(L), MHFU_LUA_HOST_ID));
    return 0;
}
static int lb_on_action(lua_State *L)
{
    store_ref(L, &r_action);
    check("on_bigmonster_action",
          mhfu_on_bigmonster_action(tramp_action, prio(L), MHFU_LUA_HOST_ID));
    return 0;
}

static const luaL_Reg k_api[] = {
    { "on_quest_targets_building",  lb_on_quest },
    { "on_bigmonster_spawn",        lb_on_spawn },
    { "on_bigmonster_death",        lb_on_death },
    { "on_bigmonster_damaged",      lb_on_damaged },
    { "on_ai_overlay_loaded",       lb_on_overlay },
    { "on_bigmonster_slot_picked",  lb_on_slot },
    { "on_bigmonster_action_input", lb_on_input },
    { "on_bigmonster_action_decided", lb_on_decided },
    { "on_bigmonster_action",       lb_on_action },
    { 0, 0 },
};

void mhfu_lua_bind_events(lua_State *L) { luaL_setfuncs(L, k_api, 0); }
