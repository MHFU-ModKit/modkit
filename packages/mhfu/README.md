# mhfu

What the modkit knows about Monster Hunter Freedom Unite (PSP, EU release ULES01213): the game's
addresses and structs, and the analysis of its code. Everything reads through one memory
interface, so the same code runs on the running game, a RAM dump or the game's own files.

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

game = Extracted.find("extracted/")  # the output of `mhp-formats extract`
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
and constants; the commands below are built on it. They read an extracted game from `--data` or
`MHFU_DATA`.

| Command | What it prints |
|---|---|
| `mhfu dis ADDR [COUNT]`, `xref ADDR`, `switch ADDR` | Disassembly, references, jump tables, in BOOT.BIN or `--overlay` |
| `mhfu reloc OVERLAY DELTA -o OUT` | An overlay moved to another address |
| `mhfu moveset`, `phases`, `chain SPECIES` | A big monster's moves, what ends each, what follows it |
| `mhfu effects`, `attacks`, `abi` | The effects and attacks each move spawns; the engine-to-overlay interface |
| `mhfu hitzones`, `hitboxes SPECIES` | Where a monster can be hit, and where it hits |
| `mhfu census --log framework.log` | How long each move lasted in a real game, from the framework's log |
| `mhfu intel --all` | All of the above per species, as `species/emNN.json` |
| `mhfu stage ids\|maps\|exits\|surfaces` | Stage files, the map table, area exits |
| `mhfu inject FILE_ID FILE` | Places a finished file for the framework's live injection |

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

PPSSPP must open its debugger at startup (`RemoteDebuggerOnStartup = True` in `ppsspp.ini`).
`MHFU_ISO` names the game image, `MHFU_PPSSPP` a PPSSPP binary other than the usual install,
and `MHFU_LAUNCHER=docker` (with `MHFU_CONTAINER`) uses the container from `ppsspp/`. The
automation boots the first save slot; it is tested in German and English, not yet in the other three.

## Licence

MIT.
