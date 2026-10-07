/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* Calling the game's own functions from C. */
#ifndef MHFU_CALL_H
#define MHFU_CALL_H

#include <stdint.h>

/* fn(a0..a6) on a stack aligned to 16 bytes (src/core/call.S); its v0. The game's VFPU code
 * stores quads (sv.q) into its frame, which need 16, and psp-gcc keeps frames at 8: called
 * straight from C, the first such store halts the game. Every call into the game goes through
 * here, with word arguments only (an integer or a pointer). */
#ifdef __cplusplus
extern "C" uint32_t mhfu_call(uint32_t fn, uint32_t a0 = 0, uint32_t a1 = 0, uint32_t a2 = 0,
                              uint32_t a3 = 0, uint32_t a4 = 0, uint32_t a5 = 0,
                              uint32_t a6 = 0);
#else
uint32_t mhfu_call(uint32_t fn, uint32_t a0, uint32_t a1, uint32_t a2, uint32_t a3, uint32_t a4,
                   uint32_t a5, uint32_t a6);
#endif

#endif /* MHFU_CALL_H */
