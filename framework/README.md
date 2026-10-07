# framework

`mhfu_framework.prx`, the plugin that runs mods inside MHFU (EU, ULES01213): a C++ core, the mods
composed into it at build time, and a Lua host that runs mods from the memory stick and reloads
them when they change. `mhfu_boot.prx` loads it on a real PSP.

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

## Example

A mod is a `.lua` file in the memory stick's `mods/` (below). This one logs every big monster as
it spawns:

```lua
mhfu.on_bigmonster_spawn(function(_, species, slot, hp)
  mhfu.log(string.format("[hello] species %d in slot %d, hp %d", species, slot, hp))
end)
```

`lua/examples/` has three to start from: an animation override, a second big monster in a quest,
and the ported Brute Tigrex.

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

A library in `mods/lib/` runs once, when a mod first requires it:
`local port = require("mhfu_port")`. Require at a mod's top level; later, while the Memory Stick
may be busy, `require` refuses to read a new library. A mod that needs a newer API checks
`mhfu.api_version`. Errors from the boot load are written to `framework.log` right after it.

An editor with the Lua language server (VS Code's Lua extension) gets completion and hover docs
from `.luarc.json` at the repo root; `mhfu.addr` needs `make -C framework
build/gen/addresses.gen.lua` once. Outside this repo, add `framework/lua/meta` and
`framework/lua/lib` to `workspace.library`.

## Moves of a port's own

`mhfu.move_play(ent, { entry = 46, attacks = { { 56, 6 } } })` plays executor entry 46 as a
move on a big monster: the move rides a native carrier pair (default `(0,2)`, the Tigrex's
alert hub), its clip goes on every body part, attack id 6 spawns when the clip crosses frame 56,
and when the clip ends the carrier hands back to the monster's own brain. A reaction, a hit
that flinches it for one, ends the move at once. An attack can also end at a frame
(`{ 56, 6, 80 }`), and while the move plays the host's own attacks and effects for the entry
its clip sits in are skipped (`host_attacks = true` keeps them). It runs in C on the AI step
em_vhook wraps (`include/mhfu/move.h`), so it needs a cold boot and a spawned big monster. From
the debugger: `mhfu move ride <port>`, then `mhfu move play 46 --attack 6@56-80`. A move asked
for while the monster's notice runs (it knows the player, not yet in combat) waits for the roar
and the combat entry; the call's `force` starts it at once (`mhfu.move_play(ent, spec, true)`).
`mhfu.monster_state(ent)` says whether it noticed the player, is in combat, runs its notice.

## Monster events

The same AI step finds what a mod may answer, one AI frame after the engine's change at most,
raised on the 5 Hz registry poll (`include/mhfu/monster_events.h`): `mhfu.on_bigmonster_noticed`,
`_combat_entered` and `_combat_left` (the yellow eye beside the player's name), `_flinch` (with
the part), `_part_broken` and `_tail_cut`, each `fn(ev)` with the entity, the AI frame, the pair
and the part. `mhfu.move_react("flinch", ent, { entry = 78 })` plays a move in place of the
monster's flinch: the engine counts the flinch and applies the damage, then enters the move's
carrier instead of `(4,x)`, and the move plays from the next AI step. From the debugger:
`mhfu events --follow 60`.

## Own moves from a manifest

A port's own moves sit in a registry by slot (`mhfu.em_move`). The seam's brain, in C on the
AI step, plays one through the move player when asked (`mhfu.em_play`), when the move before it
ends on its clip, its length or a wall and names it as its `after`, or when a rule says
(`play_move`, and `from_move` to fire while one plays). `mhfu_port.lua` does all of it from the
port's generated `<name>_moves.lua`: `port:move("stamp")` (`ports/README.md` "Moves and
rules").

## Rules on monster events

A brain rule (`mhfu.em_rule`) with `on = "<event>"` fires in C in the AI frame the event is seen,
`part` narrowing a flinch or a break; capacity `mhfu.addr.EM_CFG.RULES_COUNT`. A rule on the
flinch owns the reaction replacement while installed: a flinch of its part, in a frame its gates
hold, enters its own move's carrier instead of `(4,x)`, and the move plays from the next AI
frame. `force = true` plays a rule's move past the notice wait. `ports/README.md` "Moves and
rules" has the manifest's side.

## Tests

`uv run pytest framework` compiles framework sources for the host and runs the Lua against the
declared API; it needs a host `c++` (else those tests skip) and `lupa`, which `uv sync` installs.

## Status

Runs in PPSSPP and on a real PSP under PRO CFW, for MHFU EU only. The Lua API is version 1
(`mhfu.api_version`); `mhfu.clone_combat` is experimental and can crash the game.

## Licence

MIT.
