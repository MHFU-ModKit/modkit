# mhfu-studio

One Qt window for authoring MHFU mods offline, with the commands behind it. Two workspaces:

- **Monster**: a ported monster's clips, labels, parts, hitboxes and moves, kept in its manifest
  in [`ports/`](../../ports), checked against the base monster's data from the game. Its hit
  tables are one set of writes over the base monster's, exported as a Lua module the framework
  loads (`<name>_hit.lua`) or pushed into the running game through PPSSPP's debugger.
- **Map**: edits to a stage's mesh, collision and textures, kept as edit lists in a `map.toml`
  document and pushed into the running game through PPSSPP's debugger; and named points (the
  Point tool, the Points panel), which `mhfu rig goto|walk` takes the hunter to.

## Install

In a clone of modkit, `uv sync` installs it with Qt (PySide6) and the Qt test plugin;
`uv sync --no-group qt` leaves out only the test plugin, for a machine without a display. For a
wheel, see the root README's [Wheels](../../README.md#wheels).

The studio needs the extracted games. It takes each from its variable (`MHFU_DATA`,
`MHP3RD_DATA`), else from the folder chosen under Setup on its start page, else it looks for
`workspace/extracted` and `workspace/extracted_mhp3` in the working directory and up to four
folders above it. Extract there
([`mhp-formats`](../../packages/mhp-formats/README.md#extracting-a-game)) and nothing needs
setting. Sending to the game also needs PPSSPP's memory stick (`MHFU_MEMSTICK`, else PPSSPP's
default).

## Example

```bash
uv run studio                                    # the window
uv run studio open ports/zinogre.toml            # the window on a port
uv run studio port check ports/zinogre.toml      # the same checks, on the command line
uv run studio port hit ports/zinogre.toml --deploy   # zinogre_hit.lua onto the memory stick
uv run studio port push ports/zinogre.toml           # the same writes into the running game
```

| Command | Does |
|---|---|
| `studio open [PATH]` | The window, with PATH open; `--workspace NAME`, `--size WxH` |
| `studio render selftest\|map\|monster` | A stage or a monster to PNG, without a window |
| `studio port scene\|clips\|align\|check\|hit\|push` | A port's contents, clip coverage and labels, timing against the host, checks, its Lua module, and the same writes into the running game (`MHFU_LANE` picks a lane's) |
| `studio map inject\|edit\|push\|exits\|surfaces\|budget\|textures\|verify` | A map document into the running game, one edit list offline or live, and what a stage holds |

`--help` on any of them says more.

## Naming a port's clips

The Clips panel lists every clip of the original by MHP3rd id (`stream * 100 + slot`) with the
anim (executor entry) that plays it. Click a clip or step with the arrow keys and it plays; type
a name, Return, what it shows, Return: the manifest has it and the next clip plays. A name pins
nothing and moves no clip. Place pins one in another anim, swapping with the clip there, and
builds the port again. Play in game holds the clip's anim on the running game's big monster through
`cli_bridge.lua` (`MHFU_LANE`'s PPSSPP, else the one running); Release lets it go. The game
plays the build last injected, so a clip placed since the manifest was saved is refused until it
is saved and injected again (`mhfu-port inject`).

Used in names the moves that play a clip. Order by "Fits after the one on screen" sorts the list
by how well each clip's first pose continues where the playing clip ends (the mean angle between
the two poses, the body's heading left out), to find the next part of a chain: up to 10 degrees is
good, up to 20 fair, past it poor. It rates poses only, so a part the engine blends into can score
poor; the picker's Neighbours order lists clips by source number instead.

## A port's own moves

An own move plays one clip on every body part through the framework's move player, with attacks of
its own. In Moves (View > Panels), New from the clip on screen makes one (Clips has New move too);
its fields are the manifest's: length, carrier, the base monster's attacks kept or not, what follows
it, and how it turns. The Timeline draws its attacks in lanes under the clip: drag on the empty lane
to add one (a click adds one without an end), drag an edge or the span to move it; the playhead
lights the attacks that are out, and their hit group in the view when hit groups are shown. The ring
on the floor of the view (T hides it) is the turn: an arrow for frame 0, one for the facing now, and
a handle for the end, which drags the clip's `turn`, or a fixed steer's angle, in whole degrees
(Shift: 15). Every edit is one undo step.

Play in game saves, writes the port's modules to the memory stick's `mods/lib` as
`mhfu-port inject` does, and asks the running port to play the move by name; it refuses a move
whose clip sits in another anim than in the saved manifest, since the game holds the build
injected from the file. It writes only the modules that changed, and asks for the move from the
moves module just built, so a new or edited move plays once the game has re-run that module.
A move asked while the monster has noticed the hunter but is not yet in combat waits for combat,
so the "!" and the howl play out; Force beside the button plays it at once.

Moves that hand to one another by Then are a sequence: the table groups them under the first,
and the picked move's Sequence strip shows one chip per step. Add step after lists the clips,
best fit after the step's clip first, and makes the one you click a new own move that follows it;
Change clip, Remove step, Earlier, Later and Split here edit the chain, each one undo step; a
pair move is shown but never edited here. Play sequence plays the steps back to back, each
starting where the last stood, facing as it faced, and the Timeline's bar above the transport
shows the steps (click one to pick it). Picking or playing anything else starts clips where
they normally start.

The Behaviour dock (the button under the moves, or View > Panels) draws the port's `[behaviour]`
graph: every move and every block is a node, and right-click adds a block (the palette is
`mhfu_port.behaviour.KINDS`). A wire from a block's `out` feeds the next block's `in`, or plays a
move at its `play`; a move's `while playing` starts a path while it plays, and its `then` hands to
another move (`after`). Any other wire is refused in the status line. `#2` on a block is its path's
rank (higher on the canvas is checked first, but every path that plays nothing runs before all
the others), `⚠` a path the game refuses (the tip says why),
faded a block on no complete path. Delete removes blocks and their links; a move is deleted in
Moves. Every gesture is one undo step.

Effect blocks (violet) change the monster's counters and flags when their path fires. A path may
end at an effect with nothing to play: it applies its effects, keeps its `#n` on that block, and
the scan goes on to the next path. A counter, flag or signal name is a box of the names the graph
already uses; type a new one (lowercase letters, digits, `_`, starting with a letter) or it is put
back. An On signal block fires when Lua raises that name: `port:fire("name")`; `port:var("name")`
reads a counter or flag and `port:var("name", v)` sets it. Hunter side ticks any of front, left,
right, behind.

## Tests

`uv run pytest apps/studio` runs offscreen; the Qt tests skip without the `qt` group, and the
few that need a real GL window run with `MHFU_UI_DISPLAY=1`. `apps/studio/docker/run.sh` runs
the tests, or any command, in a Linux container with Mesa's software GL, the renderer the render
goldens are pinned to:

```bash
apps/studio/docker/run.sh                                  # the studio's tests
MHFU_UI_DISPLAY=1 apps/studio/docker/run.sh                # with Qt on Xvfb: the GL ones too
apps/studio/docker/run.sh uv run studio render selftest -o out/
```

## Status

Both workspaces work. New map geometry has to fit the primitives a vertex group already has
(`studio map budget` says how much). The Qt tests run locally only: CI has no display.

## Licence

GPL-3.0-or-later: it is built on `mhp-formats`, which contains code derived from
[mhff](https://github.com/svanheulen/mhff).
