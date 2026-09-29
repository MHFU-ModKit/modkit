/* The Lua run at setup, NUL-terminated for luaL_loadstring: the prelude
 * (lua/_prelude.lua) and the address table without its doc comments (build/gen/addresses.lua). */
    .section .rodata
    .globl mhfu_lua_prelude
mhfu_lua_prelude:
    .incbin "_prelude.lua"
    .byte 0

    .globl mhfu_lua_addresses
mhfu_lua_addresses:
    .incbin "addresses.lua"
    .byte 0
