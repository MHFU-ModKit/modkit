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
and `MHP3RD_DATA`, and `<name>_clips.lua` beside it; `inject` puts that module in the memory
stick's `mods/lib/` (`--lib` elsewhere). The built files are game data: never commit them.

## Clips

Every clip of the donor's moveset goes into the port, each in an executor entry: the action id
(`a1`) that plays it. A donor clip is known by its MHP3rd id, `stream * 100 + slot`, so a
stream-0 id is its slot. `[clips.<name>]` names a clip and places it:

```toml
[clips.dash]
slot = 3        # the executor entry
source = 205    # the MHP3rd clip; without it, the one numbered like the entry
```

The builder packs the rest: a stream-0 clip into the entry of its own id, every other clip, by
id, into a free entry, first those the host fills (its own brain asks for them), then the others
from 0 up to 122 (the Tigrex has 123 entries). No clip goes to an entry the host plays on some
body parts only (the Tigrex's 24 and 25, head and neck over an idle body). An entry no clip
takes stays empty on a port with its own skeleton, where the host's clips do not fit, and keeps
the host's clip on a port that rides the host's skeleton.

A `[clips.<name>]` pins its clip on top of that packing: the clip the packer had in its entry
takes the entry the pin freed. So naming a clip where it is moves nothing, and placing one
elsewhere swaps it with the clip there. The studio's Clips panel lists every clip by MHP3rd id
and names and places them.

`<name>_clips.lua` maps each clip's name (the manifest's, else `clip_<entry>`) to its entry, and
`mhfu_port.lua`'s `P.define` reads it as the port's clips, so no mod keeps a copy of the layout.
`mhfu-port pose` checks that each entry plays its clip.

## Example

`framework/lua/examples/ported_brute.lua` loads the Brute Tigrex in place of a Giadrome.

## Status

| Manifest | Donor | Rides |
|---|---|---|
| `brute_tigrex.toml` | Brute Tigrex (MHP3rd model 5248) | Tigrex, replacing the Giadrome |
| `zinogre.toml` | Zinogre (MHP3rd model 5339) | Tigrex, replacing the Giadrome |

Both build and animate in the game with the Tigrex's AI; their moves and hitboxes are work in
progress in the studio.

## Licence

MIT.
