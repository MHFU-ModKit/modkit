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

`mods.manifest` lists the mods linked into the plugin, one directory under `mods/` per line; `#`
disables one. Every mod directory is compiled either way.

## Memory stick

```
PSP/PLUGINS/mhfu_framework/
    mhfu_framework.prx
    mhfu_boot.prx       real PSP only
    plugin.ini          PPSSPP only
    mods/*.lua          Lua mods, loaded at boot and reloaded when they change
    framework.log       written at run time
```

`plugin.ini` for PPSSPP (it ignores a file without both sections; the addresses are EU only):

```ini
[games]
ULES01213 = true
[options]
type = prx
filename = mhfu_framework.prx
name = MHFU Framework
version = 1
```

On a real PSP under PRO CFW, `seplugins/game.txt` names `mhfu_boot.prx` instead:

```
ms0:/PSP/PLUGINS/mhfu_framework/mhfu_boot.prx 1
```

The plugin loads on a cold boot only; after loading a PPSSPP savestate it no longer runs.
