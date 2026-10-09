/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* Private contract between the core's translation units; mods include only include/mhfu. */
#ifndef MHFU_CORE_INTERNAL_H
#define MHFU_CORE_INTERNAL_H

#include <stdint.h>
#ifndef MHFU_HOST
#include <pspkerneltypes.h>
#else
typedef unsigned int SceSize;   /* host tests compile the core's pure parts */
#endif
#include "mhfu/events.h"
#include "mhfu/move.h"
#include "wrap.h"

#ifdef __cplusplus
extern "C" {
#endif

/* --- events (events.cpp; the world events in world_events.cpp) --- */
typedef struct {
    mhfu_event_fn_t fn;
    int             priority;
    const char     *owner;
} mhfu_handler_t;
/* Copies id's callbacks, highest priority first, into out (MHFU_EVENT_MAX_HANDLERS of them);
 * returns how many. A copy, so a release on another thread never tears a running dispatch. */
#define MHFU_EVENT_MAX_HANDLERS 16
int  mhfu_event_handlers(mhfu_event_id_t id, mhfu_handler_t *out);
int  mhfu_event_count(mhfu_event_id_t id);
/* Calls every callback of a notify event with ctx. */
void mhfu_event_fire(mhfu_event_id_t id, const void *ctx);
/* The quest-event anchors' helpers, installed by the event install thread. */
void mhfu_event_dispatch_quest_beginning(mhfu_regs_t *regs);
void mhfu_event_dispatch_quest_entered(mhfu_regs_t *regs);
int  mhfu_event_spawn_poll_thread(SceSize args, void *argp);

/* --- code cave (cave.cpp) --- */
uint32_t *mhfu_cave_alloc(int n_insns);   /* bump allocator; 0 if exhausted */

/* --- hook arbitration (hooks.cpp, install.cpp, anchors.cpp) --- */
void mhfu_hook_init(void);
/* mhfu_hook_detour patched now, for overlay code that has just loaded and is still JIT-cold.
 * One wrapper per addr, reused by every re-patch; OK when addr already holds our J, BADARG
 * (logged once per addr) when it holds neither that nor the expected words. */
mhfu_hook_rc_t mhfu_hook_detour_now(uint32_t addr, uint32_t expect0, uint32_t expect1,
                                    mhfu_wrap_fn pre, const char *owner);
/* Every owner's patches, on module stop. */
void mhfu_hook_release_all(void);
/* Lands the queued patches whose words match; call only on a JIT-cold screen. */
void mhfu_hook_land_queued(void);
/* Ranged invalidate of one word: the write PPSSPP notices in a translated block. */
void mhfu_smc_patch_word(uint32_t addr, uint32_t word);
int  mhfu_hook_deferred_thread(SceSize args, void *argp);
/* Installs the quest-event anchors, then re-installs one whenever it loses our J. */
int  mhfu_event_install_thread(SceSize args, void *argp);
#ifdef MHFU_HOST
/* host tests: the game word at addr, in memory the test owns */
volatile uint32_t *mhfu_host_word(uint32_t addr);
#endif

/* --- mod table (modtable.cpp) --- */
void mhfu_mod_init_all(void);
void mhfu_mod_shutdown_all(void);

/* --- AI events (ai.cpp) --- */
void mhfu_ai_on_monster_spawn(int slot, uint32_t entity, uint8_t type, uint16_t hp);
void mhfu_ai_poll_death(void);
/* The hooks the registry installs for an event's first subscriber; <0 on failure. */
int  mhfu_ai_install_overlay_loaded(void);
int  mhfu_ai_install_slot_picked(void);
int  mhfu_ai_install_picker(void);       /* ACTION_INPUT and ACTION_DECIDED */
int  mhfu_ai_install_ai_step(void);
int  mhfu_ai_install_action(void);
/* fn(buffer, bytes) for every DATA.BIN chunk once it is decrypted, on the game thread, from the
 * overlay loader hook (installed here); <0 when the listeners are full or the hook failed. */
typedef void (*mhfu_chunk_fn)(uint32_t buf, uint32_t bytes);
int  mhfu_ai_on_chunk(mhfu_chunk_fn fn);

/* --- quest (quest.cpp) --- */
/* The buildTargets wrapper, queued for the first QUEST_TARGETS_BUILDING subscriber. */
int  mhfu_quest_install_targets(void);

/* --- em_vhook (em_vhook.cpp) --- */
int  mhfu_em_init(void);
/* 1 while entity's tail cut waits for its drop: nothing of ours enters a pair meanwhile. */
int  mhfu_em_cut_waits(uint32_t entity);

/* --- joint fix (joint_fix.cpp) --- */
void mhfu_joint_fix_init(void);
/* The skeleton the joint builder gets when the engine passes it a bad one: the relocated
 * PAC's skeleton sub, or 0 for none. */
void mhfu_joint_fix_skeleton(uint32_t skel);
/* The stub's words into out (cap words), loading the skeleton from skel_word; the word
 * count, or 0 when it does not fit. Pure, for the host test. */
int  mhfu_joint_fix_emit(uint32_t *out, int cap, uint32_t skel_word);

/* --- extra RAM (xram.cpp) --- */
/* First word of a PAC's skeleton sub; the joint builder reads a bone count after it. */
#define MHFU_SKELETON_MAGIC 0xC0000000u
/* Picks the injection scratch region once: the emulator's raw extra RAM or the real PSP's
 * volatile partition. */
void mhfu_xram_init(void);
/* Quest exit: unlocks our volatile lock and drops the entries staged in it. */
void mhfu_xram_release(void);

/* --- real-PSP volatile interposer (volatile_interposer.cpp) --- */
/* Repoints the game's volatile Lock/Unlock import stubs to our wrappers; real PSP only,
 * idempotent. */
void mhfu_vol_install(void);
/* Drains the Lock/Unlock call log; poll thread, skipped while ms0 I/O is unsafe. */
void mhfu_vol_flush(void);

/* --- capture (capture.cpp) --- */
/* Low-fps framebuffer capture to ms0: or host0:; nothing is allocated until started. */
int  mhfu_capture_set(int on);                             /* 1 start, 0 stop; returns running */
int  mhfu_capture_status(int *frames, int *kb, int *err);  /* returns active (0/1) */
/* ignored while active */
void mhfu_capture_configure(int scale, int interval_ms, const char *path);

/* --- move player (move.cpp) --- */
/* Its block and its quest handler; em_vhook's init calls it. */
int  mhfu_move_init(void);
/* The brain's rules on the flinch (em_vhook.cpp) own the reaction replacement through these: a
 * flinch of entity in parts (ENTITY.FLINCH_MASK bits) enters the carrier; entity 0 turns it off.
 * The move is not armed: the brain hands it over at the hit, while a replaced reaction waits for
 * the step. */
void mhfu_move_react_arm(uint32_t entity, uint8_t parts, uint8_t carrier_main,
                         uint8_t carrier_sub);
int  mhfu_move_react_pending(uint32_t entity);
void mhfu_move_react_take(const mhfu_move_t *mv, const volatile mhfu_steer_spec_t *s);

/* --- monster events (monster_events.cpp) --- */
/* Its block and its quest handler; the move player's init calls it. */
int  mhfu_monster_events_init(void);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* MHFU_CORE_INTERNAL_H */
