# framework

`mhfu_framework.prx`, the plugin that runs mods inside MHFU (EU, ULES01213): a C++ core, the mods
composed into it at build time, and a Lua host that runs mods from the memory stick.
`mhfu_boot.prx` loads it on a real PSP.

## Build

Needs docker, uv and make.

```bash
make -C framework          # both PRXs, compiled in the pinned pspdev image
make -C framework clean
```

| Output | |
|---|---|
| `build/mhfu_framework.prx` | the plugin |
| `build/mhfu_boot.prx` | the real-PSP loader: a kernel plugin that loads the framework from its own directory once the game shows the title or a menu |
| `build/*.elf` | the same with symbols and relocations |
| `build/gen/addresses.gen.h`, `.lua` | the address map, rendered from `packages/mhfu`; Lua reads it as `mhfu.addr` |
| `build/gen/lua_api.gen.h`, `.inc` | the `mhfu` table's C side, rendered from `lua/meta/mhfu.d.lua` |

`mods.manifest` lists the mods linked into the plugin, one directory under `mods/` per line; `#`
disables one. Every mod directory is compiled either way.

## Memory stick

```
PSP/PLUGINS/mhfu_framework/
    mhfu_framework.prx
    mhfu_boot.prx       real PSP only
    plugin.ini          PPSSPP only
    mods/*.lua          Lua mods, loaded at boot and reloaded when they change
    mods/lib/*.lua      Lua libraries, run by require and reloaded when they change
    framework.log       written at run time
```

`plugin.ini` for PPSSPP (it ignores a file without both sections; the addresses are EU only).
`memory = 64` gives the emulated PSP the extra RAM that model injection, entity clones, relocated
overlays and the debug shell's bridge live in:

```ini
[games]
ULES01213 = true
[options]
type = prx
filename = mhfu_framework.prx
name = MHFU Framework
version = 1
memory = 64
```

On a real PSP under PRO CFW, `seplugins/game.txt` names `mhfu_boot.prx` instead:

```
ms0:/PSP/PLUGINS/mhfu_framework/mhfu_boot.prx 1
```

The plugin loads on a cold boot only; after loading a PPSSPP savestate it no longer runs.

## Lua mods

| `lua/` | |
|---|---|
| `meta/mhfu.d.lua` | the `mhfu` API, every function and constant with its doc; the build registers exactly what it declares |
| `lib/` | `_prelude.lua`, embedded and run before any mod, and `mhfu_port.lua`, the ported-monster runtime |
| `tools/` | `cli_bridge.lua`, the in-game side of `mhfu shell` |
| `examples/` | small mods to start from |

A mod is a `.lua` file in `mods/`. A library in `mods/lib/` runs once, when a mod first requires
it: `local port = require("mhfu_port")`. Require at a mod's top level; later, while the Memory Stick
may be busy, `require` refuses to read a new library. A mod that needs a newer API checks
`mhfu.api_version`. Errors from the boot load are written to `framework.log` right after it.

An editor with the Lua language server (VS Code's Lua extension) gets completion and hover docs
from `.luarc.json` at the repo root; `mhfu.addr` needs `make -C framework
build/gen/addresses.gen.lua` once. Outside this repo, add `framework/lua/meta` and
`framework/lua/lib` to `workspace.library`.
