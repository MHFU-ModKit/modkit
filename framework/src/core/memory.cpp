/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* Game-memory access (memory.h) and the world getters that read one fixed cell (world.h). */
#include "mhfu/memory.h"
#include "mhfu/world.h"
#include "addresses.gen.h"

extern "C" {

uint8_t  mhfu_mem_read_u8 (uint32_t a) { return *(volatile uint8_t  *)a; }
uint16_t mhfu_mem_read_u16(uint32_t a) { return *(volatile uint16_t *)a; }
uint32_t mhfu_mem_read_u32(uint32_t a) { return *(volatile uint32_t *)a; }
void     mhfu_mem_write_u8 (uint32_t a, uint8_t  v) { *(volatile uint8_t  *)a = v; }
void     mhfu_mem_write_u16(uint32_t a, uint16_t v) { *(volatile uint16_t *)a = v; }
void     mhfu_mem_write_u32(uint32_t a, uint32_t v) { *(volatile uint32_t *)a = v; }

float mhfu_mem_read_f32(uint32_t a)
{
    union { uint32_t u; float f; } cvt;
    cvt.u = *(volatile uint32_t *)a;
    return cvt.f;
}
void mhfu_mem_write_f32(uint32_t a, float v)
{
    union { uint32_t u; float f; } cvt;
    cvt.f = v;
    *(volatile uint32_t *)a = cvt.u;
}

/* Callers that accept the extra-RAM window (clones, relocated overlays) check it themselves. */
int mhfu_mem_valid(uint32_t a)
{
    return a >= MHFU_MAIN_RAM && a < MHFU_USER_RAM_END;
}

uint8_t mhfu_world_screen_state(void)
{
    return *(volatile uint8_t *)MHFU_SCREEN_STATE;
}

/* Only ms0 I/O is gated; patching RAM is safe in any state. */
int mhfu_world_ms0_io_safe(void)
{
    uint8_t s = mhfu_world_screen_state();
    return (s == MHFU_WORLD_SCREEN_IN_AREA || s == MHFU_WORLD_SCREEN_VILLAGE);
}
int mhfu_world_village_roam(void)
{
    static const uint32_t roam[] = MHFU_FREE_ROAM_SCENES;
    const uint32_t vtable = *(volatile uint32_t *)(MHFU_PLAYER_ENTITY + MHFU_ENTITY_VTABLE);
    if (vtable != MHFU_PLAYER_ENTITY_VTABLE) return 0;
    const uint32_t scene = *(volatile uint32_t *)MHFU_SCENE_OBJECT;
    for (int i = 0; i < MHFU_FREE_ROAM_SCENES_COUNT; i++)
        if (scene == roam[i]) return 1;
    return 0;
}
uint16_t mhfu_world_area_index(void)
{
    return *(volatile uint16_t *)MHFU_AREA_INDEX;
}
uint32_t mhfu_world_quest_timer(void)
{
    return *(volatile uint32_t *)(MHFU_QUEST_SINGLETON + MHFU_QUEST_TIMER);
}
uint32_t mhfu_world_player_hp(void)
{
    return *(volatile uint16_t *)(MHFU_PLAYER_ENTITY + MHFU_ENTITY_HP);
}

} /* extern "C" */
