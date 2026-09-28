# mhp-formats

The file formats of Monster Hunter Freedom Unite and Monster Hunter Portable 3rd (PSP). Every
format is a dataclass that reads with `from_bytes` and writes back with `to_bytes`, byte for byte
on every file the games ship.

```python
from mhp_formats import Pac, Skeleton, Tmh

pac = Pac.from_bytes(path.read_bytes())
skeleton = Skeleton.from_bytes(pac.entries[0])
rgba = Tmh.from_bytes(pac.entries[2]).images[0].decode()
assert pac.to_bytes() == path.read_bytes()
```

| Module | Format |
|---|---|
| `pac` | The container both games pack everything into |
| `tmh` | Texture banks: every pixel and palette format the games use, DXT1 included |
| `skeleton` | Skeletons of both games |
| `anim`, `fu.anim`, `p3rd.anim` | Animation packs; only the clip encoding differs between the games |
| `fu.stage` | MHFU stages: terrain, textures, environment and the collision grid |
| `databin`, `iso` | DATA.BIN and getting a game's files out of its ISO |
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

GPL-3.0-or-later. The TMH texture codec and the GE display-list walker are derived from
[mhff](https://github.com/svanheulen/mhff) by Seth VanHeulen; DATA.BIN decryption uses his
[mhef](https://github.com/svanheulen/mhef), through [a fork](https://github.com/MHFU-ModKit/mhef)
that runs on Python 3.
