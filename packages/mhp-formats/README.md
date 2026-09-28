# mhp-formats

The file formats of Monster Hunter Freedom Unite and Monster Hunter Portable 3rd (PSP). Every
format is a dataclass that reads with `from_bytes` and writes back with `to_bytes`, byte for byte
on every file the games ship.

## Licence

GPL-3.0-or-later. The TMH texture codec and the GE display-list walker are derived from
[mhff](https://github.com/svanheulen/mhff) by Seth VanHeulen; DATA.BIN decryption uses his
[mhef](https://github.com/svanheulen/mhef).
