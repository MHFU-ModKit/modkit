/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* The Lua run at setup, NUL-terminated for luaL_loadstring, both without their doc comments:
 * the prelude (build/gen/prelude.lua) and the address table (build/gen/addresses.lua). */
    .section .rodata
    .globl mhfu_lua_prelude
mhfu_lua_prelude:
    .incbin "prelude.lua"
    .byte 0

    .globl mhfu_lua_addresses
mhfu_lua_addresses:
    .incbin "addresses.lua"
    .byte 0
