/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* The game for move.cpp and monster_events.cpp on the host: game memory is one array, the engine
 * calls and raised events are recorded, and em_vhook's reaction replacement is modelled. */
#include <stdint.h>
#include <string.h>
#include "mhfu/em_vhook.h"
#include "mhfu/move.h"
#include "mhfu/monster_events.h"
#include "addresses.gen.h"

#define BASE 0x09000000u   /* the fake game memory; noaddr */
#define NODE_VTABLE 0x1234u
#define SIZE 0x4000u

static uint8_t g_mem[SIZE];
static mhfu_em_step_fn g_step;
static uint32_t g_calls[64][5];
static int g_n;
static int g_lands = 1;          /* enter-action writes the pair cells */
static float g_clip_end = 228;   /* what the executor installs on every part */

static uint8_t *at(uint32_t a, uint32_t n)
{
    return (a >= BASE && a + n <= BASE + SIZE) ? g_mem + (a - BASE) : 0;
}

static void record(uint32_t kind, uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
    if (g_n < 64) {
        uint32_t *r = g_calls[g_n++];
        r[0] = kind; r[1] = a; r[2] = b; r[3] = c; r[4] = d;
    }
}

extern "C" {
uint8_t  mhfu_mem_read_u8(uint32_t a)  { uint8_t *p = at(a, 1); return p ? *p : 0; }
uint16_t mhfu_mem_read_u16(uint32_t a) { uint16_t v = 0; uint8_t *p = at(a, 2); if (p) memcpy(&v, p, 2); return v; }
uint32_t mhfu_mem_read_u32(uint32_t a) { uint32_t v = 0; uint8_t *p = at(a, 4); if (p) memcpy(&v, p, 4); return v; }
float    mhfu_mem_read_f32(uint32_t a) { float v = 0; uint8_t *p = at(a, 4); if (p) memcpy(&v, p, 4); return v; }
void     mhfu_mem_write_u8(uint32_t a, uint8_t v) { uint8_t *p = at(a, 1); if (p) *p = v; }
void    *mhfu_host_at(uint32_t a) { return at(a, 4); }
void mhfu_log(const char *, ...) {}
int  mhfu_em_installed(void) { return 1; }
void mhfu_em_step(mhfu_em_step_fn fn) { g_step = fn; }
void mhfu_em_mute_events(uint32_t entity) { record('M', entity, 0, 0, 0); }

/* em_vhook's reaction entry, as its slot-32 stub applies it (tests/test_em_vhook_stubs.py runs
 * the stub itself) */
static uint32_t g_react[7], g_react_hits, g_react_last;
void mhfu_em_react(uint32_t e, uint8_t m, uint32_t mask, uint16_t gate, uint8_t parts, uint8_t tm,
                   uint8_t ts)
{
    g_react[0] = e; g_react[1] = m; g_react[2] = mask; g_react[3] = gate;
    g_react[4] = tm; g_react[5] = ts; g_react[6] = parts;
}
uint32_t mhfu_em_react_hits(uint32_t *last) { if (last) *last = g_react_last; return g_react_hits; }

static uint32_t g_usec = 1000;
uint32_t mhfu_host_usec(void) { return g_usec += 33; }

static mhfu_monster_event_ctx_t g_raised[64];
static int g_nraised;
void mhfu_event_fire(mhfu_event_id_t id, const void *ctx)
{
    (void)id;
    if (g_nraised < 64) memcpy(&g_raised[g_nraised++], ctx, sizeof(mhfu_monster_event_ctx_t));
}

/* the engine: what the test arranged happens, and every call is kept */
void mhfu_host_enter(uint32_t e, uint32_t m, uint32_t s, uint32_t mode)
{
    record('E', e, m, s, mode);
    uint8_t *cell = at(e + MHFU_ENTITY_MAIN_STATE, 2);
    if (g_lands && cell) { cell[0] = (uint8_t)m; cell[1] = (uint8_t)s; }
}
void mhfu_host_execute(uint32_t e, uint32_t entry)
{
    record('X', e, entry, 0, 0);
    for (uint32_t k = 0; k < MHFU_ENTITY_CLIP_BLOCKS_COUNT; k++) {
        uint32_t b = e + MHFU_ENTITY_CLIP_BLOCKS + k * MHFU_CLIP_BLOCK_SIZE;
        uint32_t node = 0x5000u + entry;
        float zero = 0;
        uint16_t playing = 1;
        memcpy(at(b + MHFU_CLIP_BLOCK_NODE, 4), &node, 4);
        memcpy(at(b + MHFU_CLIP_BLOCK_END, 4), &g_clip_end, 4);
        memcpy(at(b + MHFU_CLIP_BLOCK_PHASE, 4), &zero, 4);
        memcpy(at(b + MHFU_CLIP_BLOCK_FLAGS, 2), &playing, 2);
    }
}
/* a node: in the game memory, owner e, live with the test's vtable */
uint32_t mhfu_host_spawn(uint32_t sp, uint32_t e, uint32_t id)
{
    record('S', sp, e, id, 0);
    uint32_t node = BASE + 0x3000u + 0x100u * (id & 7u);
    uint32_t vt = NODE_VTABLE;
    uint8_t active = 2;
    memcpy(at(node, 4), &vt, 4);
    memcpy(at(node + MHFU_ATTACK_NODE_OWNER, 4), &e, 4);
    memcpy(at(node + MHFU_ATTACK_NODE_STATE, 1), &active, 1);
    return node;
}
void mhfu_host_end(uint32_t node, uint32_t vtable)
{
    record('K', node, vtable, 0, 0);
    uint8_t ended = 0;
    memcpy(at(node + MHFU_ATTACK_NODE_STATE, 1), &ended, 1);
}

/* the engine's reaction entering (main, sub) mode 2, through the replacement when it matches */
void host_react_enter(uint32_t e, uint32_t m, uint32_t s)
{
    uint8_t *gate = at(e + (g_react[3] & 0x7FF), 1);
    if (g_react[0] == e && m == g_react[1] && s < 32 && (g_react[2] >> s) & 1 && gate
        && (*gate & g_react[6])) {
        g_react_hits++;
        g_react_last = (2u << 16) | (m << 8) | s;
        m = g_react[4];
        s = g_react[5];
    }
    mhfu_host_enter(e, m, s, 2);
}
int host_raised(mhfu_monster_event_ctx_t *out)
{
    memcpy(out, g_raised, sizeof(g_raised));
    int n = g_nraised;
    g_nraised = 0;
    return n;
}

uint8_t *host_mem(void) { return g_mem; }
void     host_set(int lands, float clip_end)
{
    g_lands = lands; g_clip_end = clip_end; g_n = 0; g_step = 0; g_nraised = 0;
    memset(g_react, 0, sizeof(g_react));
}
int      host_has_step(void) { return g_step != 0; }
int      host_calls(uint32_t *out) { memcpy(out, g_calls, sizeof(g_calls)); int n = g_n; g_n = 0; return n; }
uint32_t host_frame(uint32_t ent) { return g_step ? g_step(ent) : 0xFFFFFFFFu; }
}
