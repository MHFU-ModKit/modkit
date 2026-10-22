# mhfu-studio

One Qt window for authoring MHFU mods offline, with the commands behind it. Two workspaces:

- **Monster**: a ported monster's clips, labels, parts, hitboxes and moves, kept in its manifest
  in [`ports/`](../../ports), checked against the base monster's data from the game. Its hit
  tables are one set of writes over the base monster's, exported as a Lua module the framework
  loads (`<name>_hit.lua`) or pushed into the running game through PPSSPP's debugger; a port
  with a `[brain]` also ships the spare attack records its own moves play, derived from both games
  each time and never saved. Its Brain tab reads the base monster's AI script.
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
| `studio port scene\|clips\|align\|check\|hit\|push` | A port's contents, clip coverage and labels, timing against the host (the handler's hit checks and the attacks its entry's animation events spawn), checks, its Lua module, and the same writes into the running game (`MHFU_LANE` picks a lane's) |
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

The Behaviour tab edits the port's `[behaviour]` as rules, grouped by what starts them: an event, a
state between moves, or a move playing. The game checks rules in rank order, `#1` first, rules that
play nothing before all others. Rank is the stored canvas order, each block's `(y, x)`, top first:
`+ Rule` adds a rule last, dragging a rule's frame trades places with its own trigger's rules only,
and in All rules › Canvas dragging a node can change it. In Canvas a block's `out` wires to the next
block's `in` or to a move's `play`; a move's `while playing` starts a rule, and `then` sets the
move's `after`. The palette is `mhfu_port.behaviour.KINDS`. Watch the game, in the header, reads
the running game once a second while the tab is shown and marks each frame and trigger with how
often the game fired its rules (`7×`; a frame lights up when its count rises): the game must run
this port's rules as Play in game sends them, and the header says when it does not. On a
Signal trigger, Fire raises that signal in the running game, once Watch the game has found it; on
Enraged or Calmed it asks the game to enrage or calm the monster. The rules' counts then show
whether their conditions held. It is no edit and has no undo step. The tab
keeps its own docks; Findings stays open across tabs.

Effect blocks (violet) change the monster's counters and flags when their path fires. A path may
end at an effect with nothing to play: it applies its effects, keeps its `#n` on that block, and
the scan goes on to the next path. A counter, flag or signal name is a box of the names the graph
already uses; type a new one (lowercase letters, digits, `_`, starting with a letter) or it is put
back. An On signal block fires when Lua raises that name: `port:fire("name")`; `port:var("name")`
reads a counter or flag and `port:var("name", v)` sets it. Hunter side ticks any of front, left,
right, behind.

Enrage and Calm down are effect blocks too (`port:enrage()` and `port:calm()` do the same from
Lua). The Natural rage box in the Behaviour tab turns the monster's own anger off, so only those
rage it. In the Parts dock, a severable part has Severable below HP %: its cut works only once the
monster's HP is under that percent, 0 at any HP.

## The Brain tab

The Brain tab (View > Panels) shows the AI script that picks a big monster's next move, as code
and read only. It reads the species of the port's base monster, else of the monster PAC that is
open; the species box picks any other of the 42 species the overlays carry a script for. The
lines are `mhfu script EM --source`'s, in a tree: a routine opens to everything in it, an
underlined call, goto or walk follows on a double-click or Return, and Back and Forward (Alt+Left,
Alt+Right) retrace the steps. The header's lists (base routines, interrupts, calls) are beside it.
Find takes text, or an address (`0x...`). A species with different scripts for different map types
has a box to pick one. An `enter` line says what it plays: in the open port for its base monster,
from the species' own intel for another. Copy takes the selected lines, or with one selected the
routine it is in; More > Show addresses adds a column with each line's address in the game's
memory. A script is decoded on a worker of its own and kept for the session.

Follow the game, in the header, reads the running game once a second while the tab is shown and
marks the first monster of the species it finds: NEXT is the line its script resumes at, PLAYING
the `enter` that ran last (the line says "plays 3 3 instead" when the monster plays another move
than that `enter` asked for), PARKED where each of its other contexts waits. A monster that runs a
script pushed with `mhfu brain push` is followed too, mapped back onto the native lines, and the
tab switches to the version it runs. Following a link, find, Back and Forward, or picking another
species or version ends Follow. The tab edits nothing and adds nothing to Findings; edits go
through the source text (`mhfu script`, `mhfu brain`).

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
