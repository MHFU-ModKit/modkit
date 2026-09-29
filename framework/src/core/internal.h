/* Private contract between the core's translation units; mods include only include/mhfu. */
#ifndef MHFU_CORE_INTERNAL_H
#define MHFU_CORE_INTERNAL_H

#include <stdint.h>
#include <pspkerneltypes.h>
#include "mhfu/events.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Register snapshot a trampoline wrapper spills on its stack; the first 9 words match its frame. */
typedef struct mhfu_anchor_regs {
    uint32_t a0, a1, a2, a3;
    uint32_t v0, v1;
    uint32_t ra, sp, pc;
} mhfu_anchor_regs_t;

/* --- registry (registry.cpp) --- */
int  mhfu_event_register_pri(mhfu_event_id_t id, mhfu_event_cb_t cb, int priority);
int  mhfu_event_count(mhfu_event_id_t id);
void mhfu_event_fire(mhfu_event_id_t id, const void *ctx);
/* trampoline dispatchers; the cave wrappers call them with a0 = the spilled registers */
void mhfu_event_dispatch_quest_beginning(const mhfu_anchor_regs_t *regs);
void mhfu_event_dispatch_quest_entered  (const mhfu_anchor_regs_t *regs);
int  mhfu_event_spawn_poll_thread(SceSize args, void *argp);

/* --- code cave (cave.cpp) --- */
uint32_t *mhfu_cave_alloc(int n_insns);   /* bump allocator; 0 if exhausted */

/* --- self-modifying code (trampoline.cpp; mhfu_hook_flush_caches is in hooks.h) --- */
void mhfu_smc_patch_word(uint32_t addr, uint32_t word); /* ranged invalidate */

/* --- quiet-screen patch queue (install.cpp) --- */
int  mhfu_hook_deferred_thread(SceSize args, void *argp);

/* --- event trampolines (trampoline.cpp) --- */
int  mhfu_event_install_trampolines(void);
void mhfu_event_uninstall_trampolines(void);
int  mhfu_event_install_thread(SceSize args, void *argp);
/* Prefix trampoline on any anchor PC; install while the JIT is cold. Idempotent per address. */
int  mhfu_hook_trampoline(uint32_t anchor_pc, uint32_t dispatcher);

/* --- hook arbitration (hooks.cpp) --- */
void mhfu_hook_init(void);

/* --- mod table (modtable.cpp) --- */
void mhfu_mod_init_all(void);
void mhfu_mod_shutdown_all(void);

/* --- AI events (ai.cpp) --- */
void mhfu_ai_on_monster_spawn(int slot, uint32_t entity, uint8_t type, uint16_t hp);
void mhfu_ai_poll_death(void);

/* --- quest domain (quest.cpp) --- */
/* Installs the buildTargets wrapper if a mod subscribed; call after mhfu_mod_init_all(). */
void mhfu_quest_init(void);

/* --- extra RAM (xram.cpp) --- */
/* Picks the injection scratch region once: the emulator's raw extra RAM or the real PSP's
 * volatile partition. */
void mhfu_xram_init(void);
/* Stages relocate entries into our volatile lock once armed; 1 when done. */
int  mhfu_xram_prelock(void);
/* Quest exit: unlocks our volatile lock and drops the entries staged in it. */
void mhfu_xram_release(void);
/* Volatile lock-hold probe, compiled off: arm at quest depart, tick from the poll. */
void mhfu_xram_recon_arm(void);
void mhfu_xram_recon_tick(uint8_t scr);

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

/* --- bootstrap (bootstrap.cpp) --- */
/* Debug sentinel cells (MHFU_BOOT_SENTINEL) for a host debugger when the log is unavailable. */
void mhfu_sentinel_set(uint32_t offset, uint32_t value);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* MHFU_CORE_INTERNAL_H */
