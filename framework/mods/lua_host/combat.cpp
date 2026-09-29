/* Combat for clones and scripted movesets: the engine's attack resolver, its
 * effect spawn, the per-frame clone driver and the clone combat-node swap. */
#include "mhfu/mhfu.h"
#include "internal.h"
#include "lua_host.h"

/* mhfu.resolve_attack(entity): the engine's own per-monster attack resolution
 * (attack timers, hitboxes, damage). The engine resolves only its ~2 managed
 * combatants, so this is how a clone deals damage. A heavy engine call: only from
 * an override callback (game thread blocked), never from mhfu_tick. */
typedef void (*mhfu_attack_resolver_fn)(uint32_t entity);
static int lb_resolve_attack(lua_State *L)
{
    uint32_t e = (uint32_t)luaL_checkinteger(L, 1);
    if (e >= MHFU_MAIN_RAM && e < MHFU_EXTRA_RAM_END) ((mhfu_attack_resolver_fn)MHFU_ATTACK_RESOLVER)(e);
    return 0;
}

/* mhfu.spawn_effect(entity, effect_id, bone) -> handle
 * mhfu.bone_pos(entity, bone)                -> x, y, z
 *
 * One of the engine's own effects at one of the entity's bones. Species code
 * emits effects as literal arguments, so a ported monster only gets its own by a
 * script calling this at the right frame. This is em75's spawn_effect wrapper
 * (MHFU_EM75_SPAWN_EFFECT) rebuilt on addresses resident for every species:
 *
 *     joints = *(u32*)(entity + ENTITY.JOINTS)
 *     pos    = joints + bone * JOINT_SIZE + JOINT.POSITION   ; that bone's world xyz
 *     owner  = EFFECT_OWNER(entity)
 *     base   = BASE_SPECIES[entity species]                  ; s8
 *     EFFECT_SPAWN(entity, id, base, owner, bone, &pos, 3)
 *
 * A zero handle means not spawned, not a bad id: the spawn is dropped silently
 * while the entity is outside the player's section (MHFU_IN_SECTION). Bone
 * indices are the loaded skeleton's; a port's numbering differs from the host's.
 * Only from inside an override callback (it allocates from the effect manager);
 * whether any game-thread context is enough is open. A per-frame driver on the
 * ai_step prefix is withheld until shown safe; the frame-accurate spawn a moveset
 * wants (MHFU_EFFECT_SPAWN_FRAMED) is reached by wrapping an em-overlay vtable
 * slot, not an AI-tick prefix. */
typedef uint32_t (*mhfu_fx_owner_fn)(uint32_t entity);
typedef int (*mhfu_fx_spawn_fn)(uint32_t entity, int effect_id, int base_species,
                                uint32_t owner, int bone, const float *pos, int mode);

static const float *mhfu_bone_world(uint32_t ent, int bone)
{
    if (ent < MHFU_MAIN_RAM || ent >= MHFU_EXTRA_RAM_END) return 0;
    if (bone < 0 || bone > 255) return 0;
    uint32_t joints = mhfu_mem_read_u32(ent + MHFU_ENTITY_JOINTS);
    if (joints < MHFU_MAIN_RAM || joints >= MHFU_EXTRA_RAM_END) return 0;
    return (const float *)(joints + (uint32_t)bone * MHFU_JOINT_SIZE + MHFU_JOINT_POSITION);
}

static int lb_bone_pos(lua_State *L)
{
    const float *p = mhfu_bone_world((uint32_t)luaL_checkinteger(L, 1),
                                     (int)luaL_checkinteger(L, 2));
    if (!p) return 0;
    lua_pushnumber(L, p[0]); lua_pushnumber(L, p[1]); lua_pushnumber(L, p[2]);
    return 3;
}

static int mhfu_fx_spawn(uint32_t ent, int eid, int bone)
{
    const float *p = mhfu_bone_world(ent, bone);
    if (!p) return 0;
    /* the engine takes the vec3 by pointer to a stack copy, as em75 does */
    float pos[3] = { p[0], p[1], p[2] };
    uint8_t species = mhfu_mem_read_u8(ent + MHFU_ENTITY_SPECIES);
    int base = *(volatile int8_t *)(MHFU_BASE_SPECIES + species);
    uint32_t owner = ((mhfu_fx_owner_fn)MHFU_EFFECT_OWNER)(ent);
    return ((mhfu_fx_spawn_fn)MHFU_EFFECT_SPAWN)(ent, eid, base, owner, bone, pos, 3);
}

static int lb_spawn_effect(lua_State *L)
{
    lua_pushinteger(L, mhfu_fx_spawn((uint32_t)luaL_checkinteger(L, 1),
                                     (int)luaL_checkinteger(L, 2),
                                     (int)luaL_optinteger(L, 3, 0)));
    return 1;
}

/* ------------------------------------------------ clone combat driver
 * The engine ticks AI and resolves attacks only for registry-listed monsters, so
 * a clone in extra RAM roams without attacking. This ai_step prefix, fired for
 * the native on the game thread, runs each clone's AI tick and attack resolver
 * inline, the same cadence the native gets. */
typedef void (*mhfu_entity_tick_fn)(uint32_t entity);

/* The live clones, pushed from Lua each tick (mhfu.clones_set); read by the
 * driver and the combat-node dispatch. */
static uint32_t      g_clones[12];
static volatile int  g_clone_count  = 0;
static volatile int  g_clone_resolve = 0;

/* Drives only when fired for the native (low RAM): a clone's own tick re-enters
 * this prefix and must not recurse. */
static void clone_combat_step(const mhfu_bigmonster_ai_step_ctx_t *ctx)
{
    if (!g_clone_resolve) return;
    if (ctx->entity_ptr >= MHFU_USER_RAM_END) return;   /* clone re-entry: bail (no recursion) */
    int n = g_clone_count;
    static int dbg = 0;
    int log_now = ((dbg++ % 180) == 0);   /* ~ every 3 s @60fps */
    if (log_now)
        mhfu_log("[clonecmb] fire nat=0x%08X resolve=%d count=%d c0=0x%08X",
                 ctx->entity_ptr, g_clone_resolve, n, n > 0 ? g_clones[0] : 0);
    for (int i = 0; i < n; i++) {
        uint32_t c = g_clones[i];
        if (c >= MHFU_USER_RAM_END && c < MHFU_EXTRA_RAM_END) {
            uint8_t ai0 = mhfu_mem_read_u8(c + MHFU_ENTITY_ANIM_SPEED);
            /* the clone's own AI picks and drives its attacks; the resolver lands
             * the active one. Do not force a fixed action. */
            ((mhfu_entity_tick_fn)MHFU_AI_TICK)(c);              /* AI: aggro + attack patterns */
            ((mhfu_attack_resolver_fn)MHFU_ATTACK_RESOLVER)(c); /* resolve active attack -> damage */
            if (log_now && i == 0)
                mhfu_log("[clonecmb]  c0=0x%08X AISTATE %d->%d 0x33C=%d eng=%d",
                         c, ai0, mhfu_mem_read_u8(c + MHFU_ENTITY_ANIM_SPEED),
                         mhfu_mem_read_u8(c + MHFU_ENTITY_ATTACK_GATE),
                         (int)mhfu_mem_read_u32(c + MHFU_ENTITY_ENGAGE));
        }
    }
}
/* mhfu.clone_combat(enable). The ai_step detour installs on the first enable, so
 * a script that never enables it leaves the AI tick unpatched. Experimental: the
 * re-entered AI tick can misalign the stack for the engine's VFPU transform code
 * (an alignment crash was seen). */
static int lb_clone_combat(lua_State *L)
{
    int en = lua_toboolean(L, 1);
    g_clone_resolve = en;
    static int s_installed = 0;
    if (en && !s_installed) { mhfu_on_bigmonster_ai_step(clone_combat_step, 50); s_installed = 1; }
    return 0;
}
/* mhfu.clones_set({ptr,ptr,...}) — set the live clone pointers the driver ticks. */
static int lb_clones_set(lua_State *L)
{
    int n = 0;
    if (lua_istable(L, 1)) {
        int len = (int)lua_rawlen(L, 1);
        for (int i = 1; i <= len && n < 12; i++) {
            lua_rawgeti(L, 1, i);
            uint32_t v = (uint32_t)lua_tointeger(L, -1);
            lua_pop(L, 1);
            if (v) g_clones[n++] = v;
        }
    }
    g_clone_count = n;
    return 0;
}

/* ------------------------------------------------ clone combat nodes
 * A monster damages the player only while it owns a node in the per-frame
 * collision list. The engine builds each managed combatant's node every frame
 * with the builder (collision global, entity, idx); its gate is the section check
 * a clone already passes, and the node is per-frame, so teardown is the engine's.
 *
 * The per-frame drive calls the per-monster processor through a function-pointer
 * field of the monster manager. Swapping that field is a data write, so it takes
 * without a JIT-cold window: it points at a cave stub that calls the processor,
 * then builds and populates the clones' nodes in the same frame. */
typedef void (*mhfu_combat_build_fn)(uint32_t g, uint32_t entity, uint32_t idx);
#define COMBAT_IDX 0x0Eu            /* the idx the native's own call passes */
static volatile int g_combat_nodes = 0;

/* the fn-ptr the per-frame drive calls, and the processor it normally holds */
#define MGR_PROC_SLOT (MHFU_MONSTER_MANAGER_SINGLETON + MHFU_MONSTER_MANAGER_PROCESSOR)
#define MGR_PROC_ORIG MHFU_MONSTER_PROCESSOR
static uint32_t g_combat_stub = 0;

/* incremented once per drive (frame) */
#define MGR_FRAME_CTR (MHFU_MONSTER_MANAGER_SINGLETON + MHFU_MONSTER_MANAGER_FRAME_COUNTER)
/* The engine never removes a node we add, so a clone gets one only while it has
 * none (building every frame floods the pool). */
static uint32_t find_node(uint32_t g, uint32_t entity)
{
    uint32_t n = mhfu_mem_read_u32(g + MHFU_COLLISION_WORLD_LIST_HEAD);
    for (int i = 0; i < 96 && n >= MHFU_MAIN_RAM && n < MHFU_EXTRA_RAM_END; i++) {
        if (mhfu_mem_read_u32(n + MHFU_COLLISION_NODE_ENTITY) == entity) return n;
        n = mhfu_mem_read_u32(n + MHFU_COLLISION_NODE_NEXT);
    }
    return 0;
}
/* The first node whose entity backref is a low-RAM (native) monster = the fully
 * engine-populated template. */
static uint32_t find_native_node(uint32_t g)
{
    uint32_t n = mhfu_mem_read_u32(g + MHFU_COLLISION_WORLD_LIST_HEAD);
    for (int i = 0; i < 96 && n >= MHFU_MAIN_RAM && n < MHFU_EXTRA_RAM_END; i++) {
        uint32_t e = mhfu_mem_read_u32(n + MHFU_COLLISION_NODE_ENTITY);
        if (e >= MHFU_MAIN_RAM && e < MHFU_USER_RAM_END && mhfu_mem_read_u8(e + MHFU_ENTITY_SPECIES) == MHFU_MONSTER_TIGREX) return n;
        n = mhfu_mem_read_u32(n + MHFU_COLLISION_NODE_NEXT);
    }
    return 0;
}
/* Called by the stub after the processor, on the game thread. */
extern "C" void mhfu_lua_combat_node_dispatch_c(void)
{
    if (!g_combat_nodes) return;
    static uint32_t last_frame = 0xFFFFFFFFu;
    uint32_t frame = mhfu_mem_read_u32(MGR_FRAME_CTR);
    if (frame == last_frame) return;            /* once per frame */
    last_frame = frame;
    uint32_t g = mhfu_mem_read_u32(MHFU_COLLISION_WORLD_PTR);
    if (g < MHFU_MAIN_RAM || g >= MHFU_EXTRA_RAM_END) return;
    uint32_t natn = find_native_node(g);
    if (!natn) return;                          /* native not engaged yet */
    int n = g_clone_count;
    static int dbg = 0;
    if ((dbg++ % 120) == 0)
        mhfu_log("[combatnode] dispatch frame=%u clones=%d count=%u natnode=0x%08X",
                 (unsigned)frame, n, (unsigned)mhfu_mem_read_u32(g + MHFU_COLLISION_WORLD_NODE_COUNT), natn);
    for (int i = 0; i < n; i++) {
        uint32_t c = g_clones[i];
        if (c < MHFU_USER_RAM_END || c >= MHFU_EXTRA_RAM_END) continue;
        uint32_t cn = find_node(g, c);
        if (!cn) { ((mhfu_combat_build_fn)MHFU_TIGREX_ATTACK_SPAWN)(g, c, COMBAT_IDX); cn = find_node(g, c); }
        if (!cn || cn == natn) continue;
        /* the bare builder leaves the node without the hitbox fields: copy the
         * native's, keep this node's links, rebind entity and position */
        uint32_t nx = mhfu_mem_read_u32(cn + MHFU_COLLISION_NODE_NEXT);
        uint32_t bk = mhfu_mem_read_u32(cn + MHFU_COLLISION_NODE_PREV);
        for (uint32_t o = 0; o < MHFU_COLLISION_NODE_SIZE; o += 4)
            mhfu_mem_write_u32(cn + o, mhfu_mem_read_u32(natn + o));
        mhfu_mem_write_u32(cn + MHFU_COLLISION_NODE_NEXT, nx);
        mhfu_mem_write_u32(cn + MHFU_COLLISION_NODE_PREV, bk);
        mhfu_mem_write_u32(cn + MHFU_COLLISION_NODE_ENTITY, c);            /* entity backref = clone */
        mhfu_vec3_t p = mhfu_entity_pos(c);
        mhfu_mem_write_f32(cn + MHFU_COLLISION_NODE_POSITION, p.x);
        mhfu_mem_write_f32(cn + MHFU_COLLISION_NODE_POSITION + 4, p.y);
        mhfu_mem_write_f32(cn + MHFU_COLLISION_NODE_POSITION + 8, p.z);
    }
}

/* mhfu.combat_swap(): builds the stub once and re-points the field whenever it
 * holds the original (a quest reload reverts it). Call each tick while
 * combat_nodes(true). */
static int lb_combat_swap(lua_State *L)
{
    (void)L;
    if (!g_combat_nodes) return 0;
    if (!g_combat_stub) {
        uint32_t *w = mhfu_cave_alloc(12);
        if (!w) { mhfu_log("[combatnode] cave exhausted"); return 0; }
        int i = 0;
        w[i++] = mips_addiu(MIPS_REG_SP, MIPS_REG_SP, -0x10);
        w[i++] = mips_sw(MIPS_REG_RA, 0x0C, MIPS_REG_SP);
        w[i++] = mips_jal(MGR_PROC_ORIG);                 /* native processor (a0-a3 intact) */
        w[i++] = MIPS_NOP;
        w[i++] = mips_sw(MIPS_REG_V0, 0x08, MIPS_REG_SP); /* preserve its return */
        w[i++] = mips_jal((uint32_t)(uintptr_t)&mhfu_lua_combat_node_dispatch_c);
        w[i++] = MIPS_NOP;
        w[i++] = mips_lw(MIPS_REG_V0, 0x08, MIPS_REG_SP);
        w[i++] = mips_lw(MIPS_REG_RA, 0x0C, MIPS_REG_SP);
        w[i++] = mips_addiu(MIPS_REG_SP, MIPS_REG_SP, 0x10);
        w[i++] = mips_jr(MIPS_REG_RA);
        w[i++] = MIPS_NOP;
        mhfu_hook_flush_caches();
        g_combat_stub = (uint32_t)(uintptr_t)w;
    }
    volatile uint32_t *slot = (volatile uint32_t *)MGR_PROC_SLOT;
    if (*slot == MGR_PROC_ORIG) {
        *slot = g_combat_stub;
        mhfu_hook_flush_caches();
        mhfu_log("[combatnode] field swap @0x%08X -> stub 0x%08X (orig 0x%08X)",
                 MGR_PROC_SLOT, g_combat_stub, MGR_PROC_ORIG);
    }
    return 0;
}

/* mhfu.combat_nodes(enable) — arm/disarm the clone combat-node builder. */
static int lb_combat_nodes(lua_State *L)
{
    g_combat_nodes = lua_toboolean(L, 1);
    return 0;
}
/* mhfu.combat_register_all() — a no-op kept for scripts; the field swap drives it. */
static int lb_combat_register_all(lua_State *L) { (void)L; return 0; }

static const luaL_Reg k_api[] = {
    { "resolve_attack",   lb_resolve_attack },
    { "spawn_effect",     lb_spawn_effect },
    { "bone_pos",         lb_bone_pos },
    { "clone_combat",     lb_clone_combat },
    { "combat_nodes",         lb_combat_nodes },
    { "combat_swap",          lb_combat_swap },
    { "combat_register_all",  lb_combat_register_all },
    { "clones_set",       lb_clones_set },
    { 0, 0 },
};

void mhfu_lua_bind_combat(lua_State *L) { luaL_setfuncs(L, k_api, 0); }
