# Ports

A manifest, `<name>.toml`, describes one monster ported from MHP3rd: the donor's files
(`[source]`), the MHFU monster it rides (`[port]`), how it is built (`[build]`), and what the
studio and the Lua runtime read: its clips, moves, behaviour, hurtboxes, parts, hitboxes, attacks
and effects. The schema is `mhfu_port.manifest`; a key left out takes its default. The files are
written whole by `mhfu_port.manifest.save`, so they carry no comments; edit them in the studio
or by hand.

## Build

```bash
uv run mhfu-port build ports/brute_tigrex.toml    # brute_tigrex.bin
uv run mhfu-port inject ports/brute_tigrex.toml   # built and placed on the memory stick
```

`build` writes the model PAC that `[port] pac` names, from the extracted games in `MHFU_DATA`
and `MHP3RD_DATA`, and `<name>_clips.lua`, `<name>_turns.lua` and `<name>_moves.lua` beside it;
`inject` puts those modules in the memory stick's `mods/lib/` (`--lib` elsewhere). The built
files are game data: never commit them.

## Clips

Every clip of the donor's moveset goes into the port, each in an executor entry: the action id
(`a1`) that plays it. A donor clip is known by its MHP3rd id, `stream * 100 + slot`, so a
stream-0 id is its slot. `[clips.<name>]` names a clip, and with a `slot` places it too:

```toml
[clips.howl]
source = 2      # the MHP3rd clip; the packer gives it its entry

[clips.dash]
slot = 3        # the executor entry it is pinned in
source = 205    # without it, the clip numbered like the entry
```

The builder packs the rest: a clip whose id is under the host's entry count into the entry of its
own id (entry 100 + s plays slot s of the odd streams), every other clip, by id, into a free
entry, first those the host fills (its own brain asks for them), then the others from 0 up to
122 (the Tigrex has 123 entries). No clip goes to an entry the host plays on some
body parts only (the Tigrex's 24 and 25, head and neck over an idle body). An entry no clip
takes stays empty on a port with its own skeleton, where the host's clips do not fit, and keeps
the host's clip on a port that rides the host's skeleton.

A `slot` pins its clip on top of that packing: the clip the packer had in its entry takes the
entry the pin freed, so placing a clip swaps it with the clip there. A name without one pins
nothing and moves nothing. The studio's Clips panel lists every clip by MHP3rd id and names and
places them.

A `start` cuts the source: the entry holds its frames `start` to `start + frames` only, so one
source fills several entries, each cut pinned. Cuts that meet share the pose there, so a reaction
the engine plays over two entries runs straight through. The cuts of one source free its entry
once; another clip they displace takes a free entry:

```toml
[clips.flinch_tail_cut]       # (4,4): em75 drops the tail when entry 65 ends
slot = 65
source = 111
start = 0
frames = 2

[clips.flinch_tail_cut_drop]  # (4,15), which the drop enters
slot = 83
source = 111
start = 2
frames = 284
```

`<name>_clips.lua` maps each clip's name (the manifest's, else `clip_<entry>`) to its entry, and
`mhfu_port.lua`'s `P.define` reads it as the port's clips, so no mod keeps a copy of the layout.
`mhfu-port pose` checks that each entry plays its clip. `<name>_turns.lua` holds each entry's turn
curve for `mhfu move play --curve`; moves carry their own, so the game never loads it.

## Moves and behaviour

A move with `main` and `sub` paints a host pair: the pair keeps its hitbox, damage and timing,
the clip is the port's. A move without them is the port's own, played by the framework's move
player on its carrier (none given: the host's hub, `mhfu_port.moves.HUBS`, em75's `(0,2)`):

```toml
[moves.stamp]
clip = "stamp_right_claw"
attack = [{ id = 6, frame = 56, end = 80 }]   # an attack record, spawned and ended at clip frames
after = "dash"                                 # played when this one ends; own or a pair

[moves.stamp.steer]
turn = "clip"       # or still, hunter, away, fixed (angle over frames)
walls = true        # a wall ahead ends it
```

`[behaviour]` decides when a move plays, as a graph. A block is `[behaviour.blocks.<id>]`: its
`kind`, the kind's params flat beside it, `at` (canvas x, y), `next` (the blocks it feeds), `play`
(the moves a path that gets here plays) and `label`. A move on the canvas is
`[behaviour.moves.<name>]`: `at` and `during`, the blocks fed while it plays. The kinds and their
params are `mhfu_port.behaviour.KINDS`, which the studio's palette shows: events (`on_noticed`,
`on_flinch`, `on_signal`, ...), the base monster's state (`idle`, `any_time`, `host_state`),
conditions, modifiers and effects.

```toml
[behaviour.blocks.b1]     # the head flinches: play flinch_head in place of the host's flinch
kind = "on_flinch"
at = [260.0, 480.0]
part = 0                  # only the flinch of this part
play = ["flinch_head"]

[behaviour.blocks.b2]     # while the dash has played 10 frames, stamp if the hunter is close
kind = "played_for"
at = [260.0, 360.0]
frames = 10
next = ["b3"]

[behaviour.blocks.b3]
kind = "distance"
at = [480.0, 360.0]
hi = 800.0
play = ["stamp"]

[behaviour.moves.dash]
at = [0.0, 360.0]
during = ["b2"]
```

A path starts at an event or state block nothing feeds, or at a block a move lists in `during`,
follows `next` and plays the moves in each `play` it reaches; fan-out and fan-in make more paths.
Each path is one rule the framework checks every AI frame in C, and one fires a frame; a pair rule
waits while an own move plays. Higher on the canvas is checked first. A path holds one event, one
state and at most one block of a kind, but for conditions and effects (below); a block on no
complete path is loose, which loads but does nothing. A cooldown waits after the path fired; a
limit caps how often.

### Conditions, effects, counters and signals

| Role | Kinds |
|---|---|
| condition | `monster_hp` (share of max HP, from `lo`, below `hi`), `part_broken`, `rage`, `hunter_side` (front, left, right, behind; its left is the monster's own), `chance` (rolled last), `counter_is`, `flag_is` |
| effect | `counter_add`, `counter_set`, `flag_set`: applied each time the path fires |
| event | `on_signal`: a mod raised it |

Condition and effect blocks may repeat on a path: a rule holds 4 conditions and 2 effects
(`monster_hp` with both bounds is two conditions). All conditions hold, or it does not fire.

A path that ends at an effect block with no `next` and no `play` plays nothing: it applies its
effects and counts as fired (cooldown and limit work). The framework runs every no-play path each
AI frame first, whatever its place on the canvas; then the playing paths from the highest, and
the first that holds plays and ends the scan. So canvas order ranks the playing paths only. On
`on_flinch` a no-play path never replaces the host's reaction. Its `force` and `mode` have
nothing to act on, so a no-play path refuses them. `any_time` and `idle` are pair gates: a path
from them waits while an own move plays, and fires in the frames between moves.

Counters and flags are named in the blocks (`[a-z][a-z0-9_]*`) and made on first use: 16 a
monster, signed 16 bits, 0 at first and again for a new monster (a flag is a counter that is 0 or
1). Signals are named the same, 16 a monster. `<name>_moves.lua` carries each rule's `conds`,
`effects` (`{ op, arg, value }`, the board indices resolved), `signal` and `no_play`, and the
top-level `vars` and `signals` tables that number the names by sorted name. In a mod:

```lua
local n = port:var("flinches")      -- the counter's value
port:var("flinches", 0)             -- set it
port:fire("roar")                   -- raise a signal: true if the port has it
```

A signal fires its rules in the AI frame the brain takes it, once. An unknown name is logged once.

A flinch rule that plays a move is armed ahead of the flinch: its conditions are judged in the
AI frame before the host step that flinches, so a counter it tests misses the flinch about to be
counted, in any order. The Zinogre counts flinches on a no-play rule and charges up from idle
once `flinches` is 2, not on the flinch itself.

An event fires in the AI frame it is seen; `part` narrows a flinch or a break to one part
(`[parts]` names them; em75's: 0 head, 1 neck, 2 body, 3 tail, 4/6 the left/right foreleg, 5/7 the
left/right hind leg). `on_flinch` plays an own move in place of the host's flinch: the engine
counts the flinch and applies the damage, then enters the move's carrier instead of `(4,x)`; a
flinch that breaks a part plays the break path's move instead. An own move asked while the
monster's notice runs waits for combat, so the "!" and the roar are not cut; a `force` block plays
it at once (its carrier `[0, 4]` is the roar pair, which the notice enters itself).

A schema 1 file lists `[[rule]]` tables instead; it loads as one chain of blocks a rule, in the
same order, and the next save writes schema 2.

`build` and `inject` also write `<name>_moves.lua`, the moves and the compiled rules as the game
runs them: each own move's executor entry and turn keys come from the same build as the clips module, and
a move it cannot carry (no entry, an attack past its clip or with no host record) or a path that
does not compile fails the build. `mhfu_port.lua`'s `P.define` takes it when a mod gives no `moves` or `rules`, and
`port:move(name)` plays an own move. A module re-written while the game runs (`inject`, the
studio's play in game; each writes only a module that changed) is the port's from the next tick;
its `build` lets `mhfu.live.moves.play_own(..., build=)` wait for that. `mhfu move ride NAME --brain` boots a
port with them; `mhfu move play --own NAME [--force]` plays one of its own moves.

## Example

`framework/lua/examples/ported_brute.lua` loads the Brute Tigrex in place of a Giadrome;
`ported_zinogre.lua` the Zinogre with its manifest's moves and behaviour.

The Zinogre's paths, after the lunge and dash chains (higher is checked first):

1. The notice plays `notice_howl` at once, before anything that idles.
2. Combat start sets the flag `in_combat`, combat end clears it; both play nothing.
3. A break of the head plays `flinch_head`; of the left foreleg `topple_left`; of the right
   `topple_right`; of any part `break_howl`. A topple is a sequence: fall, lie helpless, get up,
   then `stamp` (`after`).
4. A flinch adds 1 to `flinches` and plays nothing.
5. Idle, in combat: with `flinches` at least 2, set it to 0 and play `charge_up`; with the
   hunter 1500 away, play `dash`, then wait 300; below 30% HP, play `notice_howl` once.
6. The signal `roar` plays `notice_howl`.

## Status

| Manifest | Donor | Rides |
|---|---|---|
| `brute_tigrex.toml` | Brute Tigrex (MHP3rd model 5248) | Tigrex, replacing the Giadrome |
| `zinogre.toml` | Zinogre (MHP3rd model 5339) | Tigrex, replacing the Giadrome |

Both build and animate in the game with the Tigrex's AI. Every clip of both plays in the game,
on the frames the build gives it in every body part, the Zinogre's in entries 100 and up
included (`mhfu clips sweep`). Their moves and hitboxes are work in progress in the studio.

## Licence

MIT.
