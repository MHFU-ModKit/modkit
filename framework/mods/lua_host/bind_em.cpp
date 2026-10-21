/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* mhfu.em_* bindings over em_vhook, the big-monster vtable seams (src/core/em_vhook.cpp);
 * declared in lua/meta/mhfu.d.lua. */
#include "mhfu/mhfu.h"
#include "lua_host.h"

int lb_em_installed(lua_State *L)
{
    lua_pushboolean(L, mhfu_em_installed());
    return 1;
}
int lb_em_request(lua_State *L)
{
    int ok = mhfu_em_request((uint8_t)luaL_checkinteger(L, 1),
                              (uint8_t)luaL_checkinteger(L, 2),
                              (uint8_t)luaL_optinteger(L, 3, 0));
    lua_pushboolean(L, ok);
    return 1;
}
int lb_em_substitute(lua_State *L)
{
    mhfu_em_substitute((int)luaL_checkinteger(L, 1),
                        (uint8_t)luaL_checkinteger(L, 2),
                        (uint8_t)luaL_checkinteger(L, 3),
                        (uint8_t)luaL_checkinteger(L, 4),
                        (uint8_t)luaL_checkinteger(L, 5),
                        (uint32_t)luaL_checkinteger(L, 6));
    lua_pushboolean(L, 1);
    return 1;
}
static lua_Integer tbl_int(lua_State *L, int idx, const char *k, lua_Integer def)
{
    lua_getfield(L, idx, k);
    lua_Integer v = lua_isnil(L, -1) ? def : luaL_checkinteger(L, -1);
    lua_pop(L, 1);
    return v;
}
static lua_Number tbl_num(lua_State *L, int idx, const char *k, lua_Number def)
{
    lua_getfield(L, idx, k);
    lua_Number v = lua_isnil(L, -1) ? def : luaL_checknumber(L, -1);
    lua_pop(L, 1);
    return v;
}
static int tbl_bool(lua_State *L, int idx, const char *k)
{
    lua_getfield(L, idx, k);
    int v = lua_toboolean(L, -1);
    lua_pop(L, 1);
    return v;
}
/* the op names in addresses.toml's order: luaL_checkoption's index + 1 is the op */
static const char *const k_conds[] = { MHFU_EM_COND_NAMES, 0 };
static const char *const k_effects[] = { MHFU_EM_EFFECT_NAMES, 0 };

/* t[key] = { "op", arg, value, ... } at most `max` of them, arg and value default 0 */
static void tbl_ops(lua_State *L, int idx, const char *key, const char *const *names,
                    mhfu_em_op_t *out, int max)
{
    lua_getfield(L, idx, key);
    if (!lua_isnil(L, -1)) {
        luaL_checktype(L, -1, LUA_TTABLE);
        const int n = (int)luaL_len(L, -1);
        luaL_argcheck(L, n <= max, 2, "too many entries in conds/effects");
        for (int i = 0; i < n; i++) {
            lua_rawgeti(L, -1, i + 1);
            luaL_checktype(L, -1, LUA_TTABLE);
            lua_rawgeti(L, -1, 1);
            out[i].op = (uint8_t)(luaL_checkoption(L, -1, NULL, names) + 1);
            lua_rawgeti(L, -2, 2);
            out[i].arg = lua_isnil(L, -1) ? 0 : (uint8_t)luaL_checkinteger(L, -1);
            lua_rawgeti(L, -3, 3);
            const lua_Integer v = lua_isnil(L, -1) ? 0 : luaL_checkinteger(L, -1);
            luaL_argcheck(L, v >= -32768 && v <= 32767, 2, "a value past a s16");
            out[i].value = (int16_t)v;
            lua_pop(L, 4);
        }
    }
    lua_pop(L, 1);
}

int lb_em_rule(lua_State *L)
{
    int slot = (int)luaL_checkinteger(L, 1);
    if (lua_isnoneornil(L, 2)) { mhfu_em_rule(slot, 0); lua_pushboolean(L, 1); return 1; }
    luaL_checktype(L, 2, LUA_TTABLE);
    mhfu_em_rule_t r = {};
    r.from_mask  = (uint8_t)tbl_int(L, 2, "from_mask", 0);
    r.from_sub   = (uint8_t)tbl_int(L, 2, "from_sub", MHFU_EM_SUB_ANY);
    r.to_main    = (uint8_t)tbl_int(L, 2, "to_main", 0);
    r.to_sub     = (uint8_t)tbl_int(L, 2, "to_sub", 0);
    r.mode       = (uint8_t)tbl_int(L, 2, "mode", 0);
    r.flags      = (uint8_t)((tbl_bool(L, 2, "receding") ? MHFU_EM_RULE_RECEDING : 0)
                           | (tbl_bool(L, 2, "closing")  ? MHFU_EM_RULE_CLOSING  : 0)
                           | (tbl_bool(L, 2, "no_play")  ? MHFU_EM_RULE_NO_PLAY  : 0));
    r.min_frames = (uint32_t)tbl_int(L, 2, "min_frames", 0);
    r.dist_lo    = (float)tbl_num(L, 2, "dist_lo", 0.0);
    r.dist_hi    = (float)tbl_num(L, 2, "dist_hi", 1.0e9);
    r.cooldown   = (uint32_t)tbl_int(L, 2, "cooldown", 0);
    r.count      = (uint32_t)tbl_int(L, 2, "count", (lua_Integer)MHFU_EM_UNLIMITED);
    r.from_move  = (uint8_t)(tbl_int(L, 2, "from_move", -1) + 1);
    r.play_move  = (uint8_t)(tbl_int(L, 2, "play_move", -1) + 1);
    lua_getfield(L, 2, "on");
    r.on = lua_isnil(L, -1) ? 0
         : (uint8_t)(luaL_checkoption(L, -1, NULL, mhfu_monster_event_names) + MHFU_MONSTER_NOTICED);
    lua_pop(L, 1);
    r.part       = (uint8_t)tbl_int(L, 2, "part", MHFU_EM_ANY_PART);
    r.force      = (uint8_t)tbl_bool(L, 2, "force");
    r.signal     = (uint8_t)(tbl_int(L, 2, "signal", -1) + 1);
    tbl_ops(L, 2, "conds", k_conds, r.conds, MHFU_EM_CONDS);
    tbl_ops(L, 2, "effects", k_effects, r.effects, MHFU_EM_EFFECTS);
    mhfu_em_rule(slot, &r);
    lua_pushboolean(L, 1);
    return 1;
}
int lb_em_clear(lua_State *L)
{
    mhfu_em_clear();
    lua_pushboolean(L, 1);
    return 1;
}
int lb_em_status(lua_State *L)
{
    mhfu_em_status_t st;
    mhfu_em_status(&st);
    lua_newtable(L);
#define SF_INT(name, v) do { lua_pushinteger(L, (lua_Integer)(v)); lua_setfield(L, -2, name); } while (0)
    lua_pushboolean(L, (int)st.installed);   lua_setfield(L, -2, "installed");
    SF_INT("ai_ticks",   st.ai_ticks);
    SF_INT("act_enters", st.act_enters);
    SF_INT("last_main",  (st.last_pair >> 8) & 0xFF);
    SF_INT("last_sub",    st.last_pair & 0xFF);
    SF_INT("frames",     st.frames);
    lua_pushnumber(L, st.dist);              lua_setfield(L, -2, "dist");
    SF_INT("sub_hits",   st.sub_hits);
    SF_INT("sub_landed", st.sub_landed);
    SF_INT("sub_last_main", (st.sub_last_in >> 8) & 0xFF);
    SF_INT("sub_last_sub",   st.sub_last_in & 0xFF);
    SF_INT("sub_last_mode", (st.sub_last_in >> 16) & 0xFF);
    SF_INT("brain_fires", st.brain_fires);
    SF_INT("events_muted", st.events_muted);
    SF_INT("tip_pairs",  st.tip_pairs);
    SF_INT("tip_copies", st.tip_copies);
    SF_INT("cut_waits",  st.cut_waits);
    SF_INT("broken",     st.broken);
    SF_INT("sever_pct",  st.sever_pct);
    SF_INT("gate_refused", st.gate_refused);
    lua_pushboolean(L, (int)st.natural_rage); lua_setfield(L, -2, "natural_rage");
    SF_INT("req_pending", st.req_pending);
    SF_INT("req_done",    st.req_done);
    SF_INT("req_main",   (st.req_result >> 8) & 0xFF);
    SF_INT("req_sub",     st.req_result & 0xFF);
    SF_INT("own_enters",  st.own_enters);
    if (st.own_last) SF_INT("own_last", st.own_last - 1);
    lua_newtable(L);
    for (int i = 0; i < MHFU_EM_RING; i++) {
        /* oldest first: the newest entry sits at ring_idx */
        uint32_t e = st.ring[(st.ring_idx + 1 + i) % MHFU_EM_RING];
        lua_newtable(L);
        SF_INT("main", (e >> 8) & 0xFF);
        SF_INT("sub",   e & 0xFF);
        SF_INT("mode", (e >> 16) & 0xFF);
        SF_INT("subst", (e >> 24) & 0xFF);
        lua_rawseti(L, -2, i + 1);
    }
    lua_setfield(L, -2, "ring");
    lua_newtable(L);
    for (int i = 0; i < MHFU_EM_RULES; i++) { lua_pushinteger(L, (lua_Integer)st.rule_fired[i]); lua_rawseti(L, -2, i + 1); }
    lua_setfield(L, -2, "rule_fired");
    lua_newtable(L);
    for (int i = 0; i < MHFU_EM_RULES; i++) { lua_pushinteger(L, (lua_Integer)st.rule_left[i]); lua_rawseti(L, -2, i + 1); }
    lua_setfield(L, -2, "rule_left");
    lua_newtable(L);
    for (int i = 0; i < MHFU_EM_SUBS; i++) { lua_pushinteger(L, (lua_Integer)st.sub_left[i]); lua_rawseti(L, -2, i + 1); }
    lua_setfield(L, -2, "sub_left");
    lua_newtable(L);
    for (int i = 0; i < MHFU_EM_VARS; i++) { lua_pushinteger(L, st.vars[i]); lua_rawseti(L, -2, i + 1); }
    lua_setfield(L, -2, "vars");
#undef SF_INT
    return 1;
}

/* the board: em_var(k [, v]) reads or sets counter k; em_signal(k) raises signal k */
int lb_em_var(lua_State *L)
{
    const int k = (int)luaL_checkinteger(L, 1);
    if (!lua_isnoneornil(L, 2)) mhfu_em_set_var(k, (int)luaL_checkinteger(L, 2));
    lua_pushinteger(L, mhfu_em_var(k));
    return 1;
}

int lb_em_signal(lua_State *L)
{
    lua_pushboolean(L, mhfu_em_signal((int)luaL_checkinteger(L, 1)));
    return 1;
}

/* rage and the tail cut's gate */
int lb_em_rage(lua_State *L)
{
    lua_pushboolean(L, mhfu_em_rage(lua_toboolean(L, 1)));
    return 1;
}

int lb_em_natural_rage(lua_State *L)
{
    mhfu_em_natural_rage(lua_toboolean(L, 1));
    lua_pushboolean(L, 1);
    return 1;
}

int lb_em_sever_gate(lua_State *L)
{
    lua_pushboolean(L, mhfu_em_sever_gate((int)luaL_checkinteger(L, 1)));
    return 1;
}

/* {{joint, carrier}, ...} */
int lb_em_tip(lua_State *L)
{
    luaL_checktype(L, 1, LUA_TTABLE);
    uint8_t pairs[MHFU_EM_TIP_MAX][2];
    int n = (int)luaL_len(L, 1);
    if (n > MHFU_EM_TIP_MAX) {
        lua_pushboolean(L, 0);
        return 1;
    }
    for (int i = 0; i < n; i++) {
        lua_rawgeti(L, 1, i + 1);
        luaL_checktype(L, -1, LUA_TTABLE);
        lua_rawgeti(L, -1, 1);
        lua_rawgeti(L, -2, 2);
        pairs[i][0] = (uint8_t)luaL_checkinteger(L, -2);
        pairs[i][1] = (uint8_t)luaL_checkinteger(L, -1);
        lua_pop(L, 3);
    }
    lua_pushboolean(L, mhfu_em_tip(pairs, n));
    return 1;
}

/* --- own moves (mhfu.em_move is in bind_move.cpp, beside the move spec it reads) --- */

int lb_em_moves_clear(lua_State *L)
{
    mhfu_em_moves_clear();
    lua_pushboolean(L, 1);
    return 1;
}

int lb_em_play(lua_State *L)
{
    lua_pushboolean(L, mhfu_em_play((uint32_t)luaL_checkinteger(L, 1),
                                    (int)luaL_checkinteger(L, 2), lua_toboolean(L, 3)));
    return 1;
}

int lb_em_playing(lua_State *L)
{
    lua_pushinteger(L, mhfu_em_playing());
    return 1;
}

int lb_em_moves_status(lua_State *L)
{
    const volatile mhfu_em_moves_t *r = mhfu_em_moves();
    lua_newtable(L);
    if (!r) return 1;
#define SF_INT(name, v) do { lua_pushinteger(L, (lua_Integer)(v)); lua_setfield(L, -2, name); } while (0)
    SF_INT("block", (uintptr_t)r);
    SF_INT("playing", mhfu_em_playing());
    const volatile mhfu_move_state_t *m = mhfu_move_state();
    int ours = m && r->tag_slot != MHFU_EM_NO_MOVE && m->started == r->tag_started;
    SF_INT("last", ours ? (lua_Integer)r->tag_slot : -1);
    SF_INT("plays", r->plays);
    SF_INT("chained", r->chained);
    SF_INT("keys", r->key_top);
#undef SF_INT
    return 1;
}

/* --- fatigue (mhfu/fatigue.h) --- */

/* t[k] as an integer in [lo, hi], def when missing */
static lua_Integer tbl_in(lua_State *L, int idx, const char *k, lua_Integer def, lua_Integer lo,
                          lua_Integer hi)
{
    const lua_Integer v = tbl_int(L, idx, k, def);
    if (v < lo || v > hi) luaL_argerror(L, idx, lua_pushfstring(L, "%s is out of range", k));
    return v;
}

/* t.pairs = { {main, sub, cost}, ... } at the top of the stack, into c */
static int fatigue_pairs(lua_State *L, mhfu_fatigue_cfg_t *c)
{
    const int n = (int)luaL_len(L, -1);
    if (n > MHFU_FATIGUE_PAIRS) return 0;
    for (int i = 0; i < n; i++) {
        lua_rawgeti(L, -1, i + 1);
        luaL_checktype(L, -1, LUA_TTABLE);
        lua_rawgeti(L, -1, 1);
        lua_rawgeti(L, -2, 2);
        lua_rawgeti(L, -3, 3);   /* main, sub, cost at -3, -2, -1 */
        const lua_Integer m = luaL_checkinteger(L, -3), s = luaL_checkinteger(L, -2);
        const lua_Integer cost = luaL_checkinteger(L, -1);
        luaL_argcheck(L, m >= 0 && m <= 255 && s >= 0 && s <= 255, 1, "a pair past a byte");
        luaL_argcheck(L, cost >= -32768 && cost <= 32767, 1, "a cost past a s16");
        c->pairs[i].main = (uint8_t)m;
        c->pairs[i].sub = (uint8_t)s;
        c->pairs[i].cost = (int16_t)cost;
        lua_pop(L, 4);
    }
    c->pair_count = (uint16_t)n;
    return 1;
}

/* t.own = { [slot] = cost }, a table at the top of the stack, into c */
static void fatigue_own(lua_State *L, mhfu_fatigue_cfg_t *c)
{
    lua_pushnil(L);
    while (lua_next(L, -2)) {
        luaL_argcheck(L, lua_isinteger(L, -2), 1, "own is keyed by slot");
        const lua_Integer slot = lua_tointeger(L, -2), cost = luaL_checkinteger(L, -1);
        luaL_argcheck(L, slot >= 0 && slot < MHFU_FATIGUE_OWN, 1, "an own slot past the registry");
        luaL_argcheck(L, cost >= -32768 && cost <= 32767, 1, "a cost past a s16");
        c->own[slot] = (int16_t)cost;
        lua_pop(L, 1);
    }
}

int lb_em_fatigue(lua_State *L)
{
    if (lua_isnoneornil(L, 1)) {
        lua_pushboolean(L, mhfu_em_fatigue(0));
        return 1;
    }
    luaL_checktype(L, 1, LUA_TTABLE);
    mhfu_fatigue_cfg_t c = {};
    c.max = (int32_t)tbl_in(L, 1, "max", 1000, 1, MHFU_FATIGUE_MAX);
    c.refill = (int32_t)tbl_in(L, 1, "refill", c.max / 2 > 0 ? c.max / 2 : 1, 1, c.max);
    c.tired_frames = (uint32_t)tbl_in(L, 1, "tired", 2250, 1, 0x7FFFFFFF);
    c.low_hp_pct = (uint8_t)tbl_in(L, 1, "low_hp", 0, 0, 100);
    c.calm = (uint8_t)tbl_bool(L, 1, "calm");
    c.starts[0] = c.starts[1] = c.max;
    if (lua_getfield(L, 1, "starts") == LUA_TTABLE) {
        lua_rawgeti(L, -1, 1);
        c.starts[0] = c.starts[1] = lua_isnil(L, -1) ? c.max : (int32_t)luaL_checkinteger(L, -1);
        lua_pop(L, 1);
        lua_rawgeti(L, -1, 2);
        if (!lua_isnil(L, -1)) c.starts[1] = (int32_t)luaL_checkinteger(L, -1);
        lua_pop(L, 1);
    }
    lua_pop(L, 1);
    int fits = 1;
    if (lua_getfield(L, 1, "pairs") == LUA_TTABLE) fits = fatigue_pairs(L, &c);
    lua_pop(L, 1);
    if (lua_getfield(L, 1, "own") == LUA_TTABLE) fatigue_own(L, &c);
    lua_pop(L, 1);
    lua_pushboolean(L, fits && mhfu_em_fatigue(&c));
    return 1;
}

int lb_em_fatigue_status(lua_State *L)
{
    mhfu_fatigue_status_t st;
    mhfu_em_fatigue_status(&st);
    lua_newtable(L);
#define SF_INT(name, v) do { lua_pushinteger(L, (lua_Integer)(v)); lua_setfield(L, -2, name); } while (0)
    lua_pushboolean(L, (int)st.armed);  lua_setfield(L, -2, "armed");
    lua_pushboolean(L, (int)st.tired);  lua_setfield(L, -2, "tired");
    SF_INT("stamina", st.stamina);
    SF_INT("left",    st.left);
    SF_INT("charges", st.charges);
    SF_INT("spells",  st.spells);
    SF_INT("gauge",   st.gauge);
#undef SF_INT
    return 1;
}

/* --- charge (mhfu/charge.h) --- */

/* t.ups = { [slot] = { g95, g75, g40, glow } }, a table at the top of the stack, into c: the ups
 * by slot, so the same table is always the same settings. 0 when there are too many. */
static int charge_ups(lua_State *L, mhfu_charge_cfg_t *c)
{
    int n = 0;
    lua_pushnil(L);
    while (lua_next(L, -2)) {
        luaL_argcheck(L, lua_isinteger(L, -2), 1, "ups is keyed by slot");
        luaL_argcheck(L, lua_istable(L, -1), 1, "an up is { g95, g75, g40, glow }");
        if (n >= MHFU_CHARGE_MAX_UPS) {
            lua_pop(L, 2);
            return 0;
        }
        const lua_Integer slot = lua_tointeger(L, -2);
        luaL_argcheck(L, slot >= 0 && slot < MHFU_EM_MOVES, 1, "an up slot past the registry");
        mhfu_charge_up_t u = {};
        u.slot = (uint8_t)slot;
        for (int b = 0; b < 4; b++) {
            lua_rawgeti(L, -1, b + 1);
            const lua_Integer v = lua_isnil(L, -1) ? 0 : luaL_checkinteger(L, -1);
            luaL_argcheck(L, v >= -32768 && v <= 32767, 1, "a gain past a s16");
            u.gain[b] = (int16_t)v;
            lua_pop(L, 1);
        }
        int at = n++;
        for (; at > 0 && c->ups[at - 1].slot > u.slot; at--) c->ups[at] = c->ups[at - 1];
        c->ups[at] = u;
        lua_pop(L, 1);
    }
    c->up_count = (uint8_t)n;
    return 1;
}

/* t.discharge = { {main, sub}, ... } at the top of the stack, into c */
static int charge_pairs(lua_State *L, mhfu_charge_cfg_t *c)
{
    const int n = (int)luaL_len(L, -1);
    if (n > MHFU_CHARGE_MAX_PAIRS) return 0;
    for (int i = 0; i < n; i++) {
        lua_rawgeti(L, -1, i + 1);
        luaL_checktype(L, -1, LUA_TTABLE);
        lua_rawgeti(L, -1, 1);
        lua_rawgeti(L, -2, 2);   /* main, sub at -2, -1 */
        const lua_Integer m = luaL_checkinteger(L, -2), s = luaL_checkinteger(L, -1);
        luaL_argcheck(L, m >= 0 && m <= 255 && s >= 0 && s <= 255, 1, "a pair past a byte");
        c->pairs[i].main = (uint8_t)m;
        c->pairs[i].sub = (uint8_t)s;
        lua_pop(L, 3);
    }
    c->pair_count = (uint8_t)n;
    return 1;
}

int lb_em_charge(lua_State *L)
{
    if (lua_isnoneornil(L, 1)) {
        lua_pushboolean(L, mhfu_em_charge(0));
        return 1;
    }
    luaL_checktype(L, 1, LUA_TTABLE);
    mhfu_charge_cfg_t c = {};
    c.transform = (uint8_t)tbl_in(L, 1, "transform", MHFU_CHARGE_NO_SLOT, 0, 255);
    c.chip_gauge = (int16_t)tbl_in(L, 1, "chip_gauge", 0, -32768, 32767);
    c.chip_durability = (int16_t)tbl_in(L, 1, "chip_durability", 0, -32768, 32767);
    c.cooldown_up = (uint16_t)tbl_in(L, 1, "cooldown_up", 420, 0, 65535);
    c.cooldown_out = (uint16_t)tbl_in(L, 1, "cooldown_out", 900, 0, 65535);
    int fits = 1;
    if (lua_getfield(L, 1, "ups") == LUA_TTABLE) fits = charge_ups(L, &c);
    lua_pop(L, 1);
    if (fits && lua_getfield(L, 1, "discharge") == LUA_TTABLE) fits = charge_pairs(L, &c);
    lua_pop(L, 1);
    lua_pushboolean(L, fits && mhfu_em_charge(&c));
    return 1;
}

int lb_em_charge_status(lua_State *L)
{
    mhfu_charge_status_t st;
    mhfu_em_charge_status(&st);
    lua_newtable(L);
#define SF_INT(name, v) do { lua_pushinteger(L, (lua_Integer)(v)); lua_setfield(L, -2, name); } while (0)
    lua_pushboolean(L, (int)st.armed);    lua_setfield(L, -2, "armed");
    lua_pushboolean(L, (int)st.charged);  lua_setfield(L, -2, "charged");
    SF_INT("gauge",      st.gauge);
    SF_INT("durability", st.durability);
    SF_INT("cooldown",   st.cooldown);
    SF_INT("ups",        st.up_starts);
    SF_INT("transforms", st.transforms);
    SF_INT("discharges", st.discharges);
    SF_INT("hits",       st.hits);
    SF_INT("asked",      st.asked);
#undef SF_INT
    return 1;
}
