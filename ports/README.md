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
and `MHP3RD_DATA`. The built file is game data: never commit it.

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
