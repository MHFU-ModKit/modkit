/* Monster-entity access (mhfu/entity.h). */
#include "mhfu/entity.h"
#include "mhfu/memory.h"
#include "mhfu/log.h"
#include "addresses.gen.h"
#include <pspsysmem.h>

static const uint32_t SIZE_MIRRORS[5] = {
    MHFU_ENTITY_SIZE_SCALE, MHFU_ENTITY_RENDER_SCALE + 0, MHFU_ENTITY_RENDER_SCALE + 4,
    MHFU_ENTITY_RENDER_SCALE + 8, MHFU_ENTITY_SIZE_RADIUS,
};

/* User RAM plus the memory=64 extra RAM, where clones live; the engine ticks entities there too. */
static int in_ram(uint32_t a) { return a >= MHFU_MAIN_RAM && a < MHFU_EXTRA_RAM_END; }

extern "C" {

uint32_t mhfu_entity_at(int slot)
{
    if (slot < 0 || slot >= MHFU_ENTITY_REGISTRY_COUNT) return 0;
    return *(volatile uint32_t *)(MHFU_ENTITY_REGISTRY + slot * 4);
}

int mhfu_entity_slot_of(uint32_t ent)
{
    for (int s = 1; s < MHFU_ENTITY_REGISTRY_COUNT; s++)
        if (mhfu_entity_at(s) == ent) return s;
    return -1;
}

int mhfu_entity_is_alive(uint32_t ent)
{
    return ent && mhfu_entity_slot_of(ent) >= 0;
}

uint8_t mhfu_entity_monster_type(uint32_t ent)
{
    return in_ram(ent) ? *(volatile uint8_t *)(ent + MHFU_ENTITY_SPECIES) : 0;
}

uint16_t mhfu_entity_hp(uint32_t ent)
{
    return in_ram(ent) ? *(volatile uint16_t *)(ent + MHFU_ENTITY_HP) : 0;
}

float mhfu_entity_size(uint32_t ent)
{
    return in_ram(ent) ? mhfu_mem_read_f32(ent + MHFU_ENTITY_SIZE_SCALE) : 0.0f;
}

void mhfu_entity_set_size(uint32_t ent, float v)
{
    if (!in_ram(ent)) return;
    for (int i = 0; i < 5; i++) mhfu_mem_write_f32(ent + SIZE_MIRRORS[i], v);
}

mhfu_vec3_t mhfu_entity_pos(uint32_t ent)
{
    mhfu_vec3_t p = { 0.0f, 0.0f, 0.0f };
    if (!in_ram(ent)) return p;
    p.x = mhfu_mem_read_f32(ent + MHFU_ENTITY_POSITION + 0);
    p.y = mhfu_mem_read_f32(ent + MHFU_ENTITY_POSITION + 4);
    p.z = mhfu_mem_read_f32(ent + MHFU_ENTITY_POSITION + 8);
    return p;
}

void mhfu_entity_set_pos(uint32_t ent, mhfu_vec3_t p)
{
    if (!in_ram(ent)) return;
    mhfu_mem_write_f32(ent + MHFU_ENTITY_POSITION + 0, p.x);
    mhfu_mem_write_f32(ent + MHFU_ENTITY_POSITION + 4, p.y);
    mhfu_mem_write_f32(ent + MHFU_ENTITY_POSITION + 8, p.z);
    /* keep the transform translation row in sync */
    mhfu_mem_write_f32(ent + MHFU_ENTITY_TRANSLATION + 0, p.x);
    mhfu_mem_write_f32(ent + MHFU_ENTITY_TRANSLATION + 4, p.y);
    mhfu_mem_write_f32(ent + MHFU_ENTITY_TRANSLATION + 8, p.z);
}

int mhfu_entity_list(mhfu_monster_type_t type, uint32_t *out, int max)
{
    int n = 0;
    for (int s = 1; s < MHFU_ENTITY_REGISTRY_COUNT && n < max; s++) {
        uint32_t p = mhfu_entity_at(s);
        if (p && mhfu_entity_monster_type(p) == (uint8_t)type) out[n++] = p;
    }
    return n;
}

uint16_t mhfu_entity_yaw(uint32_t ent)
{
    return in_ram(ent) ? *(volatile uint16_t *)(ent + MHFU_ENTITY_YAW) : 0;
}
void mhfu_entity_set_yaw(uint32_t ent, uint16_t yaw)
{
    if (in_ram(ent)) *(volatile uint16_t *)(ent + MHFU_ENTITY_YAW) = yaw;
}

uint8_t mhfu_entity_ai_state(uint32_t ent)
{
    return in_ram(ent) ? *(volatile uint8_t *)(ent + MHFU_ENTITY_ANIM_SPEED) : 0;
}
void mhfu_entity_set_ai_state(uint32_t ent, uint8_t s)
{
    if (in_ram(ent)) *(volatile uint8_t *)(ent + MHFU_ENTITY_ANIM_SPEED) = s;
}

int mhfu_entity_engaged(uint32_t ent)
{
    return in_ram(ent) && mhfu_mem_read_f32(ent + MHFU_ENTITY_ENGAGE) >= 0.5f;
}
void mhfu_entity_set_engaged(uint32_t ent, int engaged)
{
    if (in_ram(ent)) mhfu_mem_write_f32(ent + MHFU_ENTITY_ENGAGE, engaged ? 1.0f : 0.0f);
}

uint16_t mhfu_entity_section(uint32_t ent)
{
    return in_ram(ent) ? *(volatile uint16_t *)(ent + MHFU_ENTITY_SECTION) : 0;
}
void mhfu_entity_set_section(uint32_t ent, uint16_t section)
{
    if (in_ram(ent)) *(volatile uint16_t *)(ent + MHFU_ENTITY_SECTION) = section;
}

void mhfu_entity_make_visible(uint32_t ent, uint16_t section)
{
    if (!in_ram(ent)) return;
    if (*(volatile uint16_t *)(ent + MHFU_ENTITY_SECTION) != section)
        *(volatile uint16_t *)(ent + MHFU_ENTITY_SECTION) = section;
    uint32_t fl = mhfu_mem_read_u32(ent + MHFU_ENTITY_FLAGS);
    if ((fl & 0x8000u) == 0) mhfu_mem_write_u32(ent + MHFU_ENTITY_FLAGS, fl | 0x8000u);
}

void mhfu_entity_force_aggro(uint32_t ent, mhfu_vec3_t target)
{
    if (!in_ram(ent)) return;
    mhfu_vec3_t p = mhfu_entity_pos(ent);
    mhfu_mem_write_f32(ent + MHFU_ENTITY_PURSUIT + 0, target.x - p.x);
    mhfu_mem_write_f32(ent + MHFU_ENTITY_PURSUIT + 4, target.y - p.y);
    mhfu_mem_write_f32(ent + MHFU_ENTITY_PURSUIT + 8, target.z - p.z);
    mhfu_mem_write_f32(ent + MHFU_ENTITY_ENGAGE, 1.0f);
    *(volatile uint8_t *)(ent + MHFU_ENTITY_ANIM_SPEED)   = 2;
    *(volatile uint8_t *)(ent + MHFU_ENTITY_TARGET_ACQUIRED) = 1;
}

void mhfu_entity_calm(uint32_t ent)
{
    if (!in_ram(ent)) return;
    /* detection ranges: the engine reads them on evaluate and never writes them */
    static const uint32_t det_offs[6] = {
        MHFU_ENTITY_DETECT_RANGES + 0,   MHFU_ENTITY_DETECT_RANGES + 4,
        MHFU_ENTITY_DETECT_RANGES + 8,   MHFU_ENTITY_DETECT_RANGES_B + 0,
        MHFU_ENTITY_DETECT_RANGES_B + 4, MHFU_ENTITY_DETECT_RANGES_B + 8,
    };
    for (int k = 0; k < 6; k++) mhfu_mem_write_f32(ent + det_offs[k], 0.0f);
    mhfu_mem_write_f32(ent + MHFU_ENTITY_ENGAGE, 0.0f);
}

/* ---------------------------------------------------------------- cloning */

/* Per-species entity size, the block a clone copies; the entity pool mixes sizes. */
static uint32_t clone_stride(uint8_t type)
{
    switch (type) {
    case MHFU_MONSTER_POPO:   return 0x4A60u;
    case MHFU_MONSTER_ANTEKA: return 0x4530u;
    default:         return 0x7A00u;   /* Tigrex, any big monster */
    }
}

/* Clone scratch, bump-allocated from one pool taken on the first clone. */
#define CLONE_POOL_SLOTS   16u
#define CLONE_SLOT_BYTES   0x8000u   /* >= the largest entity size, 256-aligned */
static uint32_t g_clone_bump = 0;
static uint32_t g_clone_end  = 0;

static uint32_t clone_alloc(uint32_t bytes)
{
    bytes = (bytes + 0xFFu) & ~0xFFu;
    if (!g_clone_bump) {
        uint32_t need = CLONE_SLOT_BYTES * CLONE_POOL_SLOTS;
        /* the user partition rarely has this much free; then use the memory=64 extra RAM */
        SceUID uid = sceKernelAllocPartitionMemory(2, "mhfu_clones", PSP_SMEM_High, need, 0);
        if (uid < 0) uid = sceKernelAllocPartitionMemory(2, "mhfu_clones", PSP_SMEM_Low, need, 0);
        if (uid >= 0) {
            g_clone_bump = (uint32_t)sceKernelGetBlockHeadAddr(uid);
            g_clone_end  = g_clone_bump + need;
            mhfu_log("[clone] pool (partmem) @0x%08X..0x%08X",
                     (unsigned)g_clone_bump, (unsigned)g_clone_end);
        } else {
            /* probe-write: is the extra RAM mapped? */
            uint32_t cand = MHFU_CLONE_POOL;
            int mapped = 0;
            if (cand + need <= MHFU_EXTRA_RAM_END) {
                volatile uint32_t *p = (volatile uint32_t *)cand;
                uint32_t save = *p; *p = 0xA5C30001u;
                mapped = (*p == 0xA5C30001u); *p = save;
            }
            if (!mapped) {
                mhfu_log("[clone] no partmem + no extra RAM (add `memory = 64` to plugin.ini)");
                return 0;
            }
            g_clone_bump = cand;
            g_clone_end  = MHFU_EXTRA_RAM_END;
            mhfu_log("[clone] pool (extra RAM) @0x%08X..0x%08X",
                     (unsigned)g_clone_bump, (unsigned)g_clone_end);
        }
    }
    if (g_clone_bump + bytes > g_clone_end) { mhfu_log("[clone] pool exhausted"); return 0; }
    uint32_t a = g_clone_bump;
    g_clone_bump += bytes;
    return a;
}

uint32_t mhfu_entity_clone(uint32_t src)
{
    if (!in_ram(src)) return 0;
    uint8_t  type   = mhfu_entity_monster_type(src);
    uint32_t stride = clone_stride(type);
    uint32_t dst    = clone_alloc(stride);
    if (!dst) return 0;
    int32_t  delta  = (int32_t)dst - (int32_t)src;

    /* 1. copy the whole struct */
    for (uint32_t o = 0; o < stride; o += 4)
        mhfu_mem_write_u32(dst + o, mhfu_mem_read_u32(src + o));

    /* 2. rebase pointers into the struct; shared model, skeleton and species buffers stay */
    for (uint32_t o = 0; o < stride; o += 4) {
        uint32_t v = mhfu_mem_read_u32(dst + o);
        if (v >= src && v < src + stride)
            mhfu_mem_write_u32(dst + o, (uint32_t)((int32_t)v + delta));
    }

    /* 3. fresh list links, spawn calm */
    mhfu_mem_write_u32(dst + MHFU_ENTITY_NEXT_OBJ, 0);
    mhfu_mem_write_u32(dst + MHFU_ENTITY_PREV_OBJ, 0);
    mhfu_mem_write_f32(dst + MHFU_ENTITY_ENGAGE, 0.0f);

    /* 4. append to the live tail of the update list (walked forward through NEXT_OBJ) */
    uint32_t tail = src, next;
    for (int g = 0; g < 64; g++) {
        next = mhfu_mem_read_u32(tail + MHFU_ENTITY_NEXT_OBJ);
        if (!next || !in_ram(next)) break;
        tail = next;
    }
    mhfu_mem_write_u32(tail + MHFU_ENTITY_NEXT_OBJ, dst);
    mhfu_mem_write_u32(dst  + MHFU_ENTITY_PREV_OBJ, tail);

    /* 5. publish in the first free registry slot */
    for (int s = 1; s < MHFU_ENTITY_REGISTRY_COUNT; s++) {
        if (mhfu_entity_at(s) == 0) {
            *(volatile uint32_t *)(MHFU_ENTITY_REGISTRY + s * 4) = dst;
            break;
        }
    }
    return dst;
}

/* ----------------------------------------------------------- combat nodes
 * The hit test (MHFU_COLLISION_TEST) walks a singly linked node list each frame; only a listed
 * node can damage the player. A clone gets a copy of a native node spliced in. */
static uint32_t coll_head_cell(void)
{
    uint32_t g = mhfu_mem_read_u32(MHFU_COLLISION_WORLD_PTR);
    if (!in_ram(g)) return 0;
    return g + MHFU_COLLISION_WORLD_LIST_HEAD;
}

uint32_t mhfu_entity_node(uint32_t ent)
{
    return in_ram(ent) ? mhfu_mem_read_u32(ent + MHFU_ENTITY_COMBAT_NODE) : 0;
}

int mhfu_entity_node_linked(uint32_t node)
{
    uint32_t hc = coll_head_cell();
    if (!hc || !in_ram(node)) return 0;
    uint32_t n = mhfu_mem_read_u32(hc);
    for (int i = 0; i < 64 && in_ram(n); i++) {
        if (n == node) return 1;
        n = mhfu_mem_read_u32(n + MHFU_COLLISION_NODE_NEXT);
    }
    return 0;
}

int mhfu_entity_node_relink(uint32_t node)
{
    if (!in_ram(node) || mhfu_entity_node_linked(node)) return 0;
    uint32_t hc = coll_head_cell();
    if (!hc) return 0;
    mhfu_mem_write_u32(node + MHFU_COLLISION_NODE_NEXT, mhfu_mem_read_u32(hc));
    mhfu_mem_write_u32(hc, node);
    return 1;
}

void mhfu_entity_node_sync(uint32_t node, uint32_t ent)
{
    if (!in_ram(node) || !in_ram(ent)) return;
    mhfu_vec3_t p = mhfu_entity_pos(ent);
    mhfu_mem_write_f32(node + MHFU_COLLISION_NODE_POSITION + 0, p.x);
    mhfu_mem_write_f32(node + MHFU_COLLISION_NODE_POSITION + 4, p.y);
    mhfu_mem_write_f32(node + MHFU_COLLISION_NODE_POSITION + 8, p.z);
}

void mhfu_entity_node_detach(uint32_t node)
{
    uint32_t hc = coll_head_cell();
    if (!hc || !in_ram(node)) return;
    uint32_t nxt  = mhfu_mem_read_u32(node + MHFU_COLLISION_NODE_NEXT);
    uint32_t head = mhfu_mem_read_u32(hc);
    if (head == node) { mhfu_mem_write_u32(hc, nxt); return; }
    uint32_t cur = head;
    for (int i = 0; i < 64 && in_ram(cur); i++) {
        uint32_t cn = mhfu_mem_read_u32(cur + MHFU_COLLISION_NODE_NEXT);
        if (cn == node) { mhfu_mem_write_u32(cur + MHFU_COLLISION_NODE_NEXT, nxt); return; }
        cur = cn;
    }
}

uint32_t mhfu_entity_node_clone(uint32_t tmpl, uint32_t ent, uint16_t uid)
{
    if (!in_ram(tmpl) || !in_ram(ent)) return 0;
    uint32_t hc = coll_head_cell();
    if (!hc) return 0;
    uint32_t node = clone_alloc(MHFU_COLLISION_NODE_SIZE);
    if (!node) return 0;
    int32_t delta = (int32_t)node - (int32_t)tmpl;

    /* copy the template node and rebase its pointers into itself */
    for (uint32_t o = 0; o < MHFU_COLLISION_NODE_SIZE; o += 4)
        mhfu_mem_write_u32(node + o, mhfu_mem_read_u32(tmpl + o));
    for (uint32_t o = 0; o < MHFU_COLLISION_NODE_SIZE; o += 4) {
        uint32_t v = mhfu_mem_read_u32(node + o);
        if (v >= tmpl && v < tmpl + MHFU_COLLISION_NODE_SIZE)
            mhfu_mem_write_u32(node + o, (uint32_t)((int32_t)v + delta));
    }

    /* The id stays the template's by default: the engine may index a table with it, and a
     * large id crashes. uid != 0 overrides it with a known-safe small id, registered with
     * the player. */
    mhfu_mem_write_u32(node + MHFU_COLLISION_NODE_ENTITY, ent);
    mhfu_mem_write_u32(node + MHFU_COLLISION_NODE_PLAYER, MHFU_PLAYER_ENTITY);
    if (*(volatile uint8_t *)(node + MHFU_COLLISION_NODE_STATE) == 0xFF)
        *(volatile uint8_t *)(node + MHFU_COLLISION_NODE_STATE) = 0;
    mhfu_entity_node_sync(node, ent);

    /* entity -> node links, as MHFU_COMBAT_REGISTER sets them */
    mhfu_mem_write_u32(ent + MHFU_ENTITY_COMBAT_NODE, node);
    *(volatile uint8_t *)(ent + MHFU_ENTITY_COMBAT_ENGAGED) = 1;
    *(volatile uint8_t *)(ent + MHFU_ENTITY_COMBAT_REGISTERED) = 1;

    if (uid != 0) {
        *(volatile uint16_t *)(node + MHFU_COLLISION_NODE_ID) = uid;
        *(volatile uint16_t *)(node + MHFU_COLLISION_NODE_REGISTER_ID) = uid;
        volatile uint8_t *cnt =
            (volatile uint8_t *)(MHFU_PLAYER_ENTITY + MHFU_ENTITY_COMBATANT_COUNT);
        uint8_t c = *cnt;
        if (c < 0x10) {
            *(volatile uint16_t *)(MHFU_PLAYER_ENTITY + MHFU_ENTITY_COMBATANT_IDS + c * 2) = uid;
            mhfu_mem_write_u32(ent + MHFU_ENTITY_COMBAT_LINK, c);
            *cnt = (uint8_t)(c + 1);
        }
    }

    /* head-insert, as MHFU_COMBAT_REGISTER does */
    mhfu_mem_write_u32(node + MHFU_COLLISION_NODE_NEXT, mhfu_mem_read_u32(hc));
    mhfu_mem_write_u32(hc, node);
    return node;
}

} /* extern "C" */
