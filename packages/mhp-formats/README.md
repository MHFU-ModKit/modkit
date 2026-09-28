# mhp-formats

The file formats of Monster Hunter Freedom Unite and Monster Hunter Portable 3rd (PSP). Every
format is a dataclass that reads with `from_bytes` and writes back with `to_bytes`, byte for byte
on every file the games ship.

```python
from mhp_formats import Pac, Pmo, Tmh, detect

pac = Pac.from_bytes(data)  # a big monster: skeleton, model, textures, ...
model = Pmo.from_bytes(pac.entries[1])
positions = model.positions(0)  # group 0, scaled to model units
rgba = Tmh.from_bytes(pac.entries[2]).images[0].decode()
assert pac.to_bytes() == data
detect(pac.entries[0])  # -> Skeleton
```

| Module | Format |
|---|---|
| `pac` | The container both games pack everything into |
| `pmo` | Models, MHFU's 1.0 and MHP3rd's 102: meshes, materials, bone palettes, the geometry, and edits laid out through the one GE emitter |
| `tmh` | Texture banks: every pixel and palette format the games use, DXT1 included |
| `skeleton` | Skeletons of both games |
| `anim`, `fu.anim`, `p3rd.anim` | Animation packs; only the clip encoding differs between the games |
| `fu.stage` | MHFU stages: terrain, textures, environment and the collision grid |
| `databin`, `iso` | DATA.BIN and getting a game's files out of its ISO |
| `detect` | Which format a blob is |
| `psp` | What the PSP itself defines: GE display lists, vertex types, colours, swizzle, strips |

Where the games differ, the difference lives in `mhp_formats.fu` (MHFU) or `mhp_formats.p3rd`
(MHP3rd).

## Extracting a game

```bash
uv run mhp-formats extract game.iso extracted/   # in this repository; elsewhere, the [iso] extra
```

`extracted/data_files/file_NNNNN.bin` is the game's engine file NNNNN + 1. Only MHFU EU
(ULES-01213) and MHP3rd (ULJM-05800) are recognised.

## Licence

GPL-3.0-or-later. The texture, vertex, display-list and model codecs are derived in part from
[mhff](https://github.com/svanheulen/mhff) by Seth VanHeulen; DATA.BIN decryption uses his
[mhef](https://github.com/svanheulen/mhef), through [a fork](https://github.com/MHFU-ModKit/mhef)
that runs on Python 3.
