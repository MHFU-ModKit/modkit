# mhfu

What the modkit knows about Monster Hunter Freedom Unite (PSP, EU release ULES01213): the game's
addresses and structs, the analysis of its code, and automation of the running game. Everything
reads through one memory interface, so the same code runs on the running game, a RAM dump or the
game's own files.

## Install

In a clone of modkit, `uv sync`, then `uv run mhfu --help`. For a wheel, see the root README's
[Wheels](../../README.md#wheels). The analysis commands need an extracted game (`mhp-formats
extract`), from `--data` or `MHFU_DATA`.

## Example

```bash
uv run mhfu go-on-quest --rank 1 --quest Giadrome   # cold boot to standing in the quest
uv run mhfu shell                                   # a debug shell on the running game
```

## Addresses

`src/mhfu/addresses.toml` is the one list of game addresses and struct layouts. Python reads it
directly; the framework's C header and Lua table are generated from it, so a new address is added
in this file only.

```python
from mhfu import addresses

addresses.SCREEN_STATE  # <SCREEN_STATE 0x08A8CA48 u8>, usable as an int
addresses.SCREEN_STATE.doc  # what it is
addresses.ENTITY.HP  # <HP +0x2E4 u16>, the field's offset
```

```bash
mhfu addresses c -o addresses.gen.h      # MHFU_SCREEN_STATE, MHFU_ENTITY_HP, ...
mhfu addresses lua -o addresses.gen.lua  # mhfu.addr.SCREEN_STATE, mhfu.addr.ENTITY.HP, ...
```

Product code in this repository never writes an address as a number: a pre-commit check rejects
one. For a value that only looks like an address, such as a MIPS jump opcode, end the line with
a `noaddr` comment.

## Memory and typed views

```python
from mhfu.files import Extracted
from mhfu.memory import Image, Live
from mhfu.structs import Entity

game = Extracted.find("workspace/extracted")  # the output of `mhp-formats extract`
em75 = game.em(75)  # the Tigrex overlay, mapped where the game loads it
em75.u32(em75.text.start)

Entity(Image(dump, base), address).hp  # from a RAM dump
Entity(Live(client), address).hp = 1  # in the running game, via ppsspp_debug.Client
```

A view's fields take their offset and type from `addresses.toml`; one that disagrees fails at
import.

## Overlay and EBOOT analysis

The game's code is plain MIPS in `BOOT.BIN` and in its overlays (`game_task`, `game_sub`, one
`emNN` per big monster, one per stage). `mhfu.mips` decodes it with
[rabbitizer](https://github.com/Decompollaborate/rabbitizer) and finds functions, calls, switches
and constants; the commands below are built on it.

| Command | What it prints |
|---|---|
| `mhfu dis ADDR [COUNT]`, `xref ADDR`, `switch ADDR` | Disassembly, references, jump tables, in BOOT.BIN or `--overlay` |
| `mhfu reloc OVERLAY DELTA -o OUT` | An overlay moved to another address |
| `mhfu moveset`, `phases`, `chain SPECIES` | A big monster's moves, what ends each, what follows it |
| `mhfu effects`, `attacks` | The effects and attacks each move spawns |
| `mhfu abi inventory\|vtables\|interface\|factory\|classes\|callers` | The engine-to-overlay interface: overlays, entity vtables and their slots, em id to overlay, calls through a slot |
| `mhfu hitzones`, `hitboxes SPECIES` | Where a monster can be hit, and where it hits |
| `mhfu census --log framework.log` | How long each move lasted in a real game, from the framework's log |
| `mhfu intel --all` | All of the above per species, as `species/emNN.json` |
| `mhfu stage ids\|maps\|exits\|surfaces` | Stage files, the map table, area exits, surface tables |
| `mhfu stage spots\|spawns` | In the running game: the quest's gathering spots and small-monster spawns |
| `mhfu inject FILE_ID FILE` | Places a finished file for the framework's live injection |
| `mhfu names build\|show\|coverage` | Names for EU code, matched from the MHP2G decomp's JP symbols (needs the JP disc, for research) |

## The running game

`mhfu.live` drives the game in PPSSPP through its debugger (`ppsspp-debug`): boot, walk, talk,
take a quest. Every step waits on what memory says, never on a count of button presses, since
the game's menus drop presses.

```python
from mhfu.live import boot, quests
from mhfu.live.session import Session

with Session.launch(cold=True) as s:  # PPSSPP here, or in the modkit container
    boot.to_village(s, "english")
    quests.take(s, 1, "Giadrome")  # rank 2, the first quest naming the Giadrome
    s.game.player.hp
```

| Command | What it does |
|---|---|
| `mhfu go-on-quest --rank R --quest NAME` | Cold boot to standing in the quest |
| `mhfu start`, `mhfu stop` | The game with its debugger open, or stopped |
| `mhfu shell` | A debug shell on the running game: player, monsters, animations, hit volumes |
| `mhfu rig load\|save\|where\|teleport\|summon\|pin\|speed` | Set up a live experiment: savestates, the player on the floor at x, z, a big monster beside the player, pinned HP (`--cull`: no small monsters in the section), fast-forward |
| `mhfu rig hold\|aim BONE` | A big monster's AI script held until its next reaction; the player put before one of its bones, facing it |
| `mhfu rig points\|goto\|walk` | Named points of a map document; the player put at one (one area change away) or walked to it, exit to exit |
| `mhfu observe trace\|cost` | Which engine functions a big monster's overlay calls in each of its states, and how often, without stopping the game |

Named points live in a map document's `map.toml` (`mhfu.points`; the studio's map editor sets
them). `mhfu rig goto NAME --map DIR` changes area straight to the point's stage and puts the
player on its floor; `mhfu rig walk NAME...` walks there. The walk's route crosses stages by
their files (floor, walls, exits): an exit counts only if a walk reaches it. Besides walking it
takes three things the collision gives: climbable walls (material 9 or 10), ledges (an unmarked
step of 120 to 350 units) and drops off a cliff (one way). Climb points in the document replace
a ledge the collision gets wrong. Both need the extracted game (`--data` or
`MHFU_DATA`); `MHFU_MAP` names the document.

```python
from mhfu.files import Extracted
from mhfu.live import route
from mhfu.live.rig import Rig
from mhfu import points

wall = points.find(points.load("maps/snow"), "tigrex_wall")
with Rig.open(state=6) as rig:
    plan = route.Map.live(rig.s, Extracted.find())
    rig.goto(wall, plan)  # one area change: about two seconds
```

PPSSPP must open its debugger at startup (`RemoteDebuggerOnStartup = True` in `ppsspp.ini`).
`MHFU_ISO` names the game image, `MHFU_PPSSPP` a PPSSPP binary not on `PATH`, and
`MHFU_LAUNCHER=docker` (with `MHFU_CONTAINER`) uses the container from `ppsspp/`.
`--lane N` (`MHFU_LANE`) runs a hidden PPSSPP of its own, so several run side by side
([`ppsspp/README.md`](../../ppsspp/README.md)).

## Status

Addresses and analysis cover MHFU EU only. The automation boots the first save slot and is
tested in German and English; French, Spanish and Italian are untested.

## Licence

MIT.
