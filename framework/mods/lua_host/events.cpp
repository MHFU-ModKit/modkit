/* The event bridge: mhfu.on_<event>(fn [, priority]) stores fn and installs a C
 * trampoline once; game-thread events run their Lua on the exec thread. */
#include <pspthreadman.h>

#include "mhfu/mhfu.h"
#include "lua_host.h"

static int g_inst_quest   = 0;          /* C trampoline installed?         */
static int g_inst_spawn   = 0;
static int g_inst_death   = 0;
static int g_inst_damaged = 0;
static int g_inst_overlay = 0;
static int g_inst_slot    = 0;
static int g_inst_input   = 0;
static int g_inst_decided = 0;
static int g_inst_action  = 0;

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

/* ------------------------------------------------------------ marshal
 * Lua heap allocation on the engine's game thread (the AI tick that runs the
 * override callbacks) is corrupted: a fresh table's keys all read back as the
 * last value. The same code is fine on our own threads, so game-thread callbacks
 * hand their request to the exec thread and block for the result. The poll
 * thread (spawn, death, damage) and the worker (mhfu_tick) enter the VM directly. */
enum { REQ_NONE = 0, REQ_INPUT, REQ_DECIDED, REQ_SLOT, REQ_OVERLAY, REQ_QUEST,
       REQ_ACTIONSEL };

typedef struct {
    int      kind;
    uint32_t entity;     /* entity_ptr, or quest ptr for REQ_QUEST            */
    uint8_t  mtype;
    uint8_t  slot;
    uint16_t aux;        /* action_count (slot) / vt8_input (decided ctx)     */
    uint32_t in;         /* engine_value / cur                                */
    uint32_t out;        /* result, defaults to `in` (passthrough)            */
} lua_req_t;

static volatile lua_req_t g_req;
static SceUID g_req_sema  = -1;   /* exec thread blocks here for work          */
static SceUID g_resp_sema = -1;   /* the game-thread caller blocks here        */
static volatile int g_exec_ready;

/* ---- handlers, run on the exec thread ---- */

/* on_bigmonster_action_input: function(ctx) -> new vt8_input. */
static uint32_t dispatch_input(lua_State *L, const volatile lua_req_t *q)
{
    uint32_t ret = q->in;
    lua_rawgeti(L, LUA_REGISTRYINDEX, r_input);
    push_action_ctx(L, q->entity, q->mtype, q->slot, (uint16_t)q->in);
    if (lua_pcall(L, 1, 1, 0) == LUA_OK) {
        if (lua_isnumber(L, -1)) ret = (uint16_t)lua_tointeger(L, -1);
    } else {
        mhfu_log("[lua_host] action_input err: %s", lua_tostring(L, -1));
    }
    lua_pop(L, 1);
    return ret;
}

static uint32_t dispatch_decided(lua_State *L, const volatile lua_req_t *q)
{
    uint32_t ret = q->in;
    lua_rawgeti(L, LUA_REGISTRYINDEX, r_decided);
    push_action_ctx(L, q->entity, q->mtype, q->slot, q->aux);
    lua_pushinteger(L, (lua_Integer)q->in);
    if (lua_pcall(L, 2, 1, 0) == LUA_OK) {
        if (lua_isnumber(L, -1)) ret = (uint32_t)lua_tointeger(L, -1);
    } else {
        mhfu_log("[lua_host] action_decided err: %s", lua_tostring(L, -1));
    }
    lua_pop(L, 1);
    return ret;
}

/* on_bigmonster_action: function({ entity, type, action_id }) -> new action id,
 * which the executor fans out to every body slot itself. */
static uint32_t dispatch_actionsel(lua_State *L, const volatile lua_req_t *q)
{
    uint32_t ret = q->in;
    lua_rawgeti(L, LUA_REGISTRYINDEX, r_action);
    lua_createtable(L, 0, 3);
    lua_pushinteger(L, (lua_Integer)q->entity); lua_setfield(L, -2, "entity");
    lua_pushinteger(L, q->mtype);               lua_setfield(L, -2, "type");
    lua_pushinteger(L, (lua_Integer)q->in);     lua_setfield(L, -2, "action_id");
    if (lua_pcall(L, 1, 1, 0) == LUA_OK) {
        if (lua_isnumber(L, -1)) ret = (uint32_t)lua_tointeger(L, -1);
    } else {
        mhfu_log("[lua_host] action(sel) err: %s", lua_tostring(L, -1));
    }
    lua_pop(L, 1);
    return ret;
}

static uint32_t dispatch_slot(lua_State *L, const volatile lua_req_t *q)
{
    uint32_t ret = q->in;
    lua_rawgeti(L, LUA_REGISTRYINDEX, r_slot);
    lua_createtable(L, 0, 4);
    lua_pushinteger(L, (lua_Integer)q->entity); lua_setfield(L, -2, "entity");
    lua_pushinteger(L, q->mtype);               lua_setfield(L, -2, "type");
    lua_pushinteger(L, q->slot);                lua_setfield(L, -2, "slot");
    lua_pushinteger(L, q->aux);                 lua_setfield(L, -2, "count");
    lua_pushinteger(L, (lua_Integer)q->in);
    if (lua_pcall(L, 2, 1, 0) == LUA_OK) {
        if (lua_isnumber(L, -1)) {
            int s = (int)lua_tointeger(L, -1);
            /* an out-of-range slot crashes the engine */
            if (s >= 0 && s < (int)q->aux) ret = (uint8_t)s;
        }
    } else {
        mhfu_log("[lua_host] slot_picked err: %s", lua_tostring(L, -1));
    }
    lua_pop(L, 1);
    return ret;
}

static void dispatch_overlay(lua_State *L)
{
    lua_rawgeti(L, LUA_REGISTRYINDEX, r_overlay);
    if (lua_pcall(L, 0, 0, 0) != LUA_OK) {
        mhfu_log("[lua_host] overlay_loaded err: %s", lua_tostring(L, -1));
        lua_pop(L, 1);
    }
}

static void dispatch_quest(lua_State *L, const volatile lua_req_t *q)
{
    lua_rawgeti(L, LUA_REGISTRYINDEX, r_quest);
    lua_pushinteger(L, (lua_Integer)q->entity);   /* quest ptr stored in entity */
    if (lua_pcall(L, 1, 0, 0) != LUA_OK) {
        mhfu_log("[lua_host] quest_targets_building err: %s", lua_tostring(L, -1));
        lua_pop(L, 1);
    }
}

/* Serves one request per loop, and always answers so the game thread never hangs. */
static int exec_thread(SceSize args, void *argp)
{
    (void)args; (void)argp;
    for (;;) {
        sceKernelWaitSema(g_req_sema, 1, 0);
        uint32_t out = g_req.in;          /* default = passthrough */
        if (mhfu_lua_enter()) {
            lua_State *L = mhfu_lua_vm;
            switch (g_req.kind) {
            case REQ_INPUT:   out = dispatch_input(L, &g_req);   break;
            case REQ_DECIDED: out = dispatch_decided(L, &g_req); break;
            case REQ_SLOT:    out = dispatch_slot(L, &g_req);    break;
            case REQ_OVERLAY: dispatch_overlay(L);               break;
            case REQ_QUEST:   dispatch_quest(L, &g_req);         break;
            case REQ_ACTIONSEL: out = dispatch_actionsel(L, &g_req); break;
            default: break;
            }
            mhfu_lua_leave();
        }
        g_req.out = out;
        sceKernelSignalSema(g_resp_sema, 1);
    }
    return 0;
}

/* Single producer (the game thread is blocked between signal and wait), so the
 * request needs no lock. */
static uint32_t marshal(int kind, uint32_t entity, uint8_t mtype,
                        uint8_t slot, uint16_t aux, uint32_t in)
{
    if (!g_exec_ready) return in;
    g_req.kind   = kind;
    g_req.entity = entity;
    g_req.mtype  = mtype;
    g_req.slot   = slot;
    g_req.aux    = aux;
    g_req.in     = in;
    g_req.out    = in;
    sceKernelSignalSema(g_req_sema, 1);
    sceKernelWaitSema(g_resp_sema, 1, 0);
    return g_req.out;
}

/* Priority above the worker's, so it answers the blocked game thread first. */
int mhfu_lua_exec_start(void)
{
    g_req_sema  = sceKernelCreateSema("mhfu_lua_req",  0, 0, 1, 0);
    g_resp_sema = sceKernelCreateSema("mhfu_lua_resp", 0, 0, 1, 0);
    if (g_req_sema < 0 || g_resp_sema < 0) {
        mhfu_log("[lua_host] marshal sema create FAILED"); return -1;
    }
    SceUID ex = sceKernelCreateThread("mhfu_lua_exec",
                                      (SceKernelThreadEntry)exec_thread,
                                      0x11, 0x10000, 0, 0);
    if (ex < 0) { mhfu_log("[lua_host] exec CreateThread FAILED"); return -1; }
    sceKernelStartThread(ex, 0, 0);
    g_exec_ready = 1;
    return 0;
}

void mhfu_lua_exec_stop(void) { g_exec_ready = 0; }

void mhfu_lua_exec_free(void)
{
    if (g_req_sema >= 0) { sceKernelDeleteSema(g_req_sema); g_req_sema = -1; }
    if (g_resp_sema >= 0) { sceKernelDeleteSema(g_resp_sema); g_resp_sema = -1; }
}

/* ---- trampolines: game-thread events marshal, poll-thread events enter directly ---- */

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
        mhfu_log("[lua_host] spawn err: %s", lua_tostring(L, -1));
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
        mhfu_log("[lua_host] death err: %s", lua_tostring(L, -1));
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
        mhfu_log("[lua_host] damaged err: %s", lua_tostring(L, -1));
        lua_pop(L, 1);
    }
    mhfu_lua_leave();
}

/* the quest-load thread is a game thread, so this one marshals too */
static void tramp_quest(const void *vctx)
{
    const mhfu_quest_ctx_t *ctx = (const mhfu_quest_ctx_t *)vctx;
    if (r_quest == LUA_NOREF) return;
    marshal(REQ_QUEST, (uint32_t)ctx->quest, 0, 0, 0, 0);
}

/* ---- mhfu.on_<event>(fn [, priority]) ---- */

static int lb_on_quest(lua_State *L)
{
    store_ref(L, &r_quest);
    int prio = (int)luaL_optinteger(L, 2, 0);
    if (!g_inst_quest) {
        mhfu_hook_event(MHFU_EVENT_QUEST_TARGETS_BUILDING,
                        (mhfu_event_cb_t)tramp_quest, prio);
        g_inst_quest = 1;
    }
    return 0;
}
static int lb_on_spawn(lua_State *L)
{
    store_ref(L, &r_spawn);
    int prio = (int)luaL_optinteger(L, 2, 0);
    if (!g_inst_spawn) { mhfu_on_bigmonster_spawn(tramp_spawn, prio); g_inst_spawn = 1; }
    return 0;
}
static int lb_on_death(lua_State *L)
{
    store_ref(L, &r_death);
    int prio = (int)luaL_optinteger(L, 2, 0);
    if (!g_inst_death) { mhfu_on_bigmonster_death(tramp_death, prio); g_inst_death = 1; }
    return 0;
}
static int lb_on_damaged(lua_State *L)
{
    store_ref(L, &r_damaged);
    int prio = (int)luaL_optinteger(L, 2, 0);
    if (!g_inst_damaged) { mhfu_on_bigmonster_damaged(tramp_damaged, prio); g_inst_damaged = 1; }
    return 0;
}
static int lb_on_overlay(lua_State *L)
{
    store_ref(L, &r_overlay);
    int prio = (int)luaL_optinteger(L, 2, 0);
    if (!g_inst_overlay) { mhfu_on_ai_overlay_loaded(tramp_overlay, prio); g_inst_overlay = 1; }
    return 0;
}
static int lb_on_slot(lua_State *L)
{
    store_ref(L, &r_slot);
    int prio = (int)luaL_optinteger(L, 2, 0);
    if (!g_inst_slot) { mhfu_on_bigmonster_slot_picked(tramp_slot, prio); g_inst_slot = 1; }
    return 0;
}
static int lb_on_input(lua_State *L)
{
    store_ref(L, &r_input);
    int prio = (int)luaL_optinteger(L, 2, 0);
    if (!g_inst_input) { mhfu_on_bigmonster_action_input(tramp_input, prio); g_inst_input = 1; }
    return 0;
}
static int lb_on_decided(lua_State *L)
{
    store_ref(L, &r_decided);
    int prio = (int)luaL_optinteger(L, 2, 0);
    if (!g_inst_decided) { mhfu_on_bigmonster_action_decided(tramp_decided, prio); g_inst_decided = 1; }
    return 0;
}
static int lb_on_action(lua_State *L)
{
    store_ref(L, &r_action);
    int prio = (int)luaL_optinteger(L, 2, 0);
    if (!g_inst_action) { mhfu_on_bigmonster_action(tramp_action, prio); g_inst_action = 1; }
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
