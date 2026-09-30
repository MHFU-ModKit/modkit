/* World events: the quest anchors' helpers raise QUEST_BEGINNING, QUEST_ENTERED and
 * MAP_SECTION_ENTERED; the registry poll raises MONSTER_SPAWNED and drives ai.cpp's spawn,
 * damage and death edges. */
#include <pspthreadman.h>

#include "mhfu/events.h"
#include "mhfu/world.h"
#include "mhfu/entity.h"
#include "internal.h"
#include "addresses.gen.h"

static uint32_t     g_last_quest_timer;
static uint16_t     g_last_area_index;
static volatile int g_quest_armed;   /* QUEST_BEGINNING fired, QUEST_ENTERED not yet */

static void fill_ctx(mhfu_event_ctx_t *c, mhfu_event_id_t id, uint32_t cell,
                     const mhfu_regs_t *r)
{
    c->event_id = id;  c->cell_value = cell;
    c->a0 = r->a0; c->a1 = r->a1; c->a2 = r->a2; c->a3 = r->a3;
    c->v0 = r->v0; c->v1 = r->v1; c->ra = r->ra; c->sp = r->sp; c->pc = r->pc;
}

extern "C" void mhfu_event_dispatch_quest_beginning(mhfu_regs_t *regs)
{
    uint32_t cur  = mhfu_world_quest_timer();
    uint32_t prev = g_last_quest_timer;
    g_last_quest_timer = cur;
    if (prev != 0 || cur == 0) return;   /* the 0 -> non-zero edge: the quest commit */

    g_quest_armed = 1;
    mhfu_event_ctx_t ctx;
    fill_ctx(&ctx, MHFU_EVENT_QUEST_BEGINNING, cur, regs);
    mhfu_event_fire(MHFU_EVENT_QUEST_BEGINNING, &ctx);
}

extern "C" void mhfu_event_dispatch_quest_entered(mhfu_regs_t *regs)
{
    uint16_t cur  = mhfu_world_area_index();
    uint16_t prev = g_last_area_index;
    if (cur == prev) return;
    g_last_area_index = cur;

    mhfu_map_section_ctx_t m;
    m.event_id         = MHFU_EVENT_MAP_SECTION_ENTERED;
    m.section_id       = cur;
    m.prev_section_id  = prev;
    m.quest_timer      = mhfu_world_quest_timer();
    m.screen_state     = mhfu_world_screen_state();
    m.is_in_quest_area = m.screen_state == MHFU_WORLD_SCREEN_IN_AREA;
    m._pad             = 0;
    mhfu_event_fire(MHFU_EVENT_MAP_SECTION_ENTERED, &m);

    if (!g_quest_armed) return;
    g_quest_armed = 0;
    mhfu_event_ctx_t ctx;
    fill_ctx(&ctx, MHFU_EVENT_QUEST_ENTERED, cur, regs);
    mhfu_event_fire(MHFU_EVENT_QUEST_ENTERED, &ctx);
}

/* --- registry poll ----------------------------------------------------------------------- */

/* Main and extra RAM: the engine ticks entity clones in the extra-RAM window too. */
static int entity_in_ram(uint32_t e) { return e >= MHFU_MAIN_RAM && e < MHFU_EXTRA_RAM_END; }

static uint32_t g_last_entity[MHFU_ENTITY_REGISTRY_COUNT];

static void dispatch_monster_spawned(int slot, uint32_t ent)
{
    uint8_t  type = mhfu_entity_monster_type(ent);
    uint16_t hp   = mhfu_entity_hp(ent);
    mhfu_ai_on_monster_spawn(slot, ent, type, hp);   /* every spawn: the HP tracker */

    if (mhfu_event_count(MHFU_EVENT_MONSTER_SPAWNED) == 0) return;
    mhfu_monster_spawn_ctx_t c;
    c.event_id     = MHFU_EVENT_MONSTER_SPAWNED;
    c.slot         = slot;
    c.entity_ptr   = ent;
    c.monster_type = type;
    c.entity_id    = *(volatile uint8_t *)(ent + MHFU_ENTITY_ID);
    c.hp           = hp;
    c.size_scale   = mhfu_entity_size(ent);
    mhfu_event_fire(MHFU_EVENT_MONSTER_SPAWNED, &c);
}

extern "C" int mhfu_event_spawn_poll_thread(SceSize args, void *argp)
{
    (void)args; (void)argp;
    for (;;) {
        sceKernelDelayThread(200 * 1000);   /* 5 Hz */
        /* reading game memory while the savedata utility starts froze the save load */
        if (!mhfu_world_ms0_io_safe()) continue;
        for (int slot = 1; slot < MHFU_ENTITY_REGISTRY_COUNT; slot++) {
            uint32_t cur = mhfu_entity_at(slot);
            if (!entity_in_ram(cur)) cur = 0;
            if (cur == g_last_entity[slot]) continue;
            g_last_entity[slot] = cur;
            if (cur) dispatch_monster_spawned(slot, cur);
        }
        mhfu_ai_poll_death();
    }
    return 0;
}
