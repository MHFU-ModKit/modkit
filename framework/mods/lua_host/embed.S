/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* The prelude (build/gen/prelude.lua), run at setup: NUL-terminated for luaL_loadstring and
 * without its doc comments. */
    .section .rodata
    .globl mhfu_lua_prelude
mhfu_lua_prelude:
    .incbin "prelude.lua"
    .byte 0
