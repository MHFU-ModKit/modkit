/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* Live model injection with no disk edits: an edited big-monster PAC on the memory stick
 * replaces the game's raw PAC before the overlay restructures it, so the engine builds ours.
 *
 * The overwrite runs on the game thread, from detours on MHFU_GET_SUBRESOURCE and
 * MHFU_MODEL_SETUP. It writes only a buffer that byte-matches the original file
 * (<path>.orig), so a look-alike monster is never touched and a repeat is a no-op. The PAC
 * a quest's monster loads is not always file_0{em_id + 6110}: match the on-screen entity's
 * buffer to find it. The engine's file id is the extracted id + 1. */
#ifndef MHFU_INJECT_H
#define MHFU_INJECT_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Watch the PAC at path (copied) for engine file id file_id; <path>.orig must be the same
 * size. 0, or <0 on a full table or a bad argument. */
int mhfu_inject_register(uint32_t file_id, const char *path);

/* A PAC larger than the engine's raw buffer: get_subresource is pointed at grown_path
 * instead, and orig_path (the unedited PAC) recognises the raw buffer. The emulator stages
 * the grown PAC now; a real PSP stages it into volatile RAM when the model first loads. */
int mhfu_inject_register_relocate(uint32_t file_id, const char *grown_path,
                                  const char *orig_path);

/* Worker tick: re-read an edited PAC whose size or mtime changed. The overwrite itself
 * happens on the game thread. */
void mhfu_inject_tick(void);

/* Re-read file_id now, ignoring mtime (a relocate entry is staged again). Returns its
 * overwrite count so far; 0 if file_id is unknown or the read fails. */
uint32_t mhfu_inject_now(uint32_t file_id);

/* file_id's overwrite count so far, 0 if unknown. */
uint32_t mhfu_inject_locate(uint32_t file_id);

#ifdef __cplusplus
}
#endif

#endif /* MHFU_INJECT_H */
