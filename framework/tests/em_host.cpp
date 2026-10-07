/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* The game for em_vhook.cpp's brain with the real move player on the host: the monster's memory
 * and the hunter's are two arrays, the engine calls are recorded. */
#include <stdint.h>
#include <string.h>
#include "addresses.gen.h"
#include "mhfu/events.h"

#define BASE 0x09000000u   /* the fake game memory; noaddr */
#define SIZE 0x4000u
#define NODE_VTABLE 0x1234u

static uint8_t g_mem[SIZE];
static uint8_t g_hunter[0x300];
static uint32_t g_calls[64][5];
static int g_n;
static float g_clip_end = 100;

static uint8_t *at(uint32_t a, uint32_t n)
{
    if (a >= BASE && a + n <= BASE + SIZE) return g_mem + (a - BASE);
    if (a >= MHFU_PLAYER_ENTITY && a + n <= MHFU_PLAYER_ENTITY + sizeof(g_hunter))
        return g_hunter + (a - MHFU_PLAYER_ENTITY);
    return 0;
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
uint32_t mhfu_host_usec(void) { return 0; }
void mhfu_event_fire(mhfu_event_id_t, const void *) {}

void mhfu_host_enter(uint32_t e, uint32_t m, uint32_t s, uint32_t mode)
{
    record('E', e, m, s, mode);
    uint8_t *cell = at(e + MHFU_ENTITY_MAIN_STATE, 2);
    if (cell) { cell[0] = (uint8_t)m; cell[1] = (uint8_t)s; }
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

uint8_t *host_mem(void) { return g_mem; }
uint8_t *host_hunter(void) { return g_hunter; }
void     host_reset(float clip_end)
{
    memset(g_mem, 0, sizeof(g_mem));
    memset(g_hunter, 0, sizeof(g_hunter));
    g_clip_end = clip_end;
    g_n = 0;
}
int host_calls(uint32_t *out) { memcpy(out, g_calls, sizeof(g_calls)); int n = g_n; g_n = 0; return n; }
}
