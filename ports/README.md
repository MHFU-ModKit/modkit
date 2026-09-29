# Ports

A manifest, `<name>.toml`, describes one ported monster: the donor's files (`[source]`), the
MHFU monster it rides (`[port]`), how it is built (`[build]`), and what the studio and the Lua
runtime read: its clips, moves, rules, hurtboxes, parts, hitboxes, attacks and effects. The
schema is `mhfu_port.manifest`; a key left out takes its default. The files are written whole
by `mhfu_port.manifest.save`, so they carry no comments.

```bash
mhfu-port build ports/<name>.toml
```

builds the model PAC that `[port] pac` names from the extracted games in `$MHFU_DATA` and
`$MHP3RD_DATA`. The built file is game data: never commit it.
