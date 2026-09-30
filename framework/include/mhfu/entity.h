/* Entity access: the registry (MHFU_ENTITY_REGISTRY, MHFU_ENTITY_REGISTRY_COUNT slots; slot 0 is
 * never the player, 0 = empty) and typed fields, whose offsets are the MHFU_ENTITY_* macros. */
#ifndef MHFU_ENTITY_H
#define MHFU_ENTITY_H

#include <stdint.h>
#include "ids.h"
#include "types.h"

#ifdef __cplusplus
extern "C" {
#endif

uint32_t mhfu_entity_at(int slot);             /* the pointer in slot, or 0 */
int      mhfu_entity_slot_of(uint32_t ent);    /* ent's slot, or -1 */
int      mhfu_entity_is_alive(uint32_t ent);   /* still in the registry */

/* Up to max live entities of one monster type into out; returns how many. */
int mhfu_entity_list(mhfu_monster_type_t type, uint32_t *out, int max);

/* 1 for the big-monster types (Tigrex, Giadrome). */
int mhfu_entity_is_bigmonster(uint32_t ent);

/* Field access; 0 or a no-op for a pointer outside RAM. */
uint8_t     mhfu_entity_monster_type(uint32_t ent);
uint16_t    mhfu_entity_hp(uint32_t ent);
float       mhfu_entity_size(uint32_t ent);
void        mhfu_entity_set_size(uint32_t ent, float v);       /* every size mirror */
mhfu_vec3_t mhfu_entity_pos(uint32_t ent);
void        mhfu_entity_set_pos(uint32_t ent, mhfu_vec3_t p);  /* and the translation row */
uint16_t    mhfu_entity_yaw(uint32_t ent);
void        mhfu_entity_set_yaw(uint32_t ent, uint16_t yaw);
uint8_t     mhfu_entity_ai_state(uint32_t ent);                /* byte at ENTITY.ANIM_SPEED; 2 = engaged */
void        mhfu_entity_set_ai_state(uint32_t ent, uint8_t s);
int         mhfu_entity_engaged(uint32_t ent);                 /* ENGAGE >= 0.5 */
void        mhfu_entity_set_engaged(uint32_t ent, int engaged);
uint16_t    mhfu_entity_section(uint32_t ent);
void        mhfu_entity_set_section(uint32_t ent, uint16_t section);

/* Render a swapped monster in section (pass the player's area index): sets the two things
 * MHFU_VISIBILITY_GATE checks, SECTION and FLAGS bit 0x8000. */
void mhfu_entity_make_visible(uint32_t ent, uint16_t section);

/* Engage target the way the engine does, in one go: PURSUIT, ENGAGE = 1.0, ai state 2 and
 * TARGET_ACQUIRED. For where the engine's own target resolver finds nothing. */
void mhfu_entity_force_aggro(uint32_t ent, mhfu_vec3_t target);

/* Zero ENGAGE and both DETECT_RANGES triples; the engine never rewrites the ranges. */
void mhfu_entity_calm(uint32_t ent);

/* Same-species clone into scratch RAM: a deep copy with its self-pointers rebased, spliced
 * onto the update chain and into a free registry slot, calm. It shares the source's model
 * and overlay, so that species must be resident. Returns the clone, or 0. */
uint32_t mhfu_entity_clone(uint32_t src);

/* Collision nodes. An entity damages the player only with a node in the list
 * MHFU_COLLISION_TEST walks, and a clone gets none of its own. */

/* Copy a native's node tmpl for ent, link both ways and put it at the list head. A nonzero
 * uid also becomes the node's id and is registered with the player (16 at most); a large id
 * crashes the engine, which indexes by it. Call once per clone. */
uint32_t mhfu_entity_node_clone(uint32_t tmpl, uint32_t ent, uint16_t uid);
uint32_t mhfu_entity_node(uint32_t ent);             /* COMBAT_NODE, or 0 */
int      mhfu_entity_node_linked(uint32_t node);     /* reachable from the list head */
int      mhfu_entity_node_relink(uint32_t node);     /* re-insert after a section change; 1 if it did */
void     mhfu_entity_node_sync(uint32_t node, uint32_t ent);   /* copy ent's position */
void     mhfu_entity_node_detach(uint32_t node);     /* before teardown: a dangling node crashes */

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* MHFU_ENTITY_H */
