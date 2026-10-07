# Ports

A manifest, `<name>.toml`, describes one monster ported from MHP3rd: the donor's files
(`[source]`), the MHFU monster it rides (`[port]`), how it is built (`[build]`), and what the
studio and the Lua runtime read: its clips, moves, rules, hurtboxes, parts, hitboxes, attacks and
effects. The schema is `mhfu_port.manifest`; a key left out takes its default. The files are
written whole by `mhfu_port.manifest.save`, so they carry no comments; edit them in the studio
or by hand.

## Build

```bash
uv run mhfu-port build ports/brute_tigrex.toml    # brute_tigrex.bin
uv run mhfu-port inject ports/brute_tigrex.toml   # built and placed on the memory stick
```

`build` writes the model PAC that `[port] pac` names, from the extracted games in `MHFU_DATA`
and `MHP3RD_DATA`, and `<name>_clips.lua` and `<name>_moves.lua` beside it; `inject` puts those
modules in the memory stick's `mods/lib/` (`--lib` elsewhere). The built files are game data:
never commit them.

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

`<name>_clips.lua` maps each clip's name (the manifest's, else `clip_<entry>`) to its entry, and
`mhfu_port.lua`'s `P.define` reads it as the port's clips, so no mod keeps a copy of the layout.
`mhfu-port pose` checks that each entry plays its clip.

## Moves and rules

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

A `[[rule]]` plays a move, a pair or an own one, when its trigger holds: `from` a move (an own
one counts its AI frames) or `from_main`, `min_frames`, the hunter's `dist`, `receding` or
`closing`, `cooldown`, `count`. The framework checks rules every AI frame in C; one fires a
frame, and a pair rule waits while an own move plays.

`build` and `inject` also write `<name>_moves.lua`, the moves and rules as the game runs them:
each own move's executor entry and turn keys come from the same build as the clips module, and
a move it cannot carry (no entry, an attack past its clip or with no host record) fails the
build. `mhfu_port.lua`'s `P.define` takes it when a mod gives no `moves` or `rules`, and
`port:move(name)` plays an own move. `mhfu move ride NAME --brain` boots a port with them.

## Example

`framework/lua/examples/ported_brute.lua` loads the Brute Tigrex in place of a Giadrome;
`ported_zinogre.lua` the Zinogre with its manifest's moves and rules.

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
