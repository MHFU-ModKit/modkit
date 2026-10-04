# DATA.BIN

`PSP_GAME/USRDIR/DATA.BIN` holds every game file: a table of block numbers, then the files, each
padded to a 2048-byte block. The disc stores it encrypted. Read and written by
`mhp_formats.databin.DataBin` (decrypted) and `decrypt`/`encrypt`, which need the `iso` extra.

## Layout (decrypted)

A block is 2048 bytes. The table occupies the first whole blocks; the files follow back to back.

| Offset | Type | Meaning |
|---|---|---|
| 0x00 | `u32[n + 1]` | Start block of each file, then the end block (the archive's length in blocks). Word 0 is file 0's start, so it is also the table's length in blocks |
| | `{u32 id, u32 size}[]` | Exact byte sizes, for the files that record one |
| | bytes | Fill up to the end of the table's last block; the games leave leftover memory here |

- File `i` is `data[start[i] * 2048 : start[i + 1] * 2048]`: its bytes, then padding to the
  block (shipped padding is not always zero; `to_bytes` writes zeros). A file without a size
  record is known only to the block.
- The start words ascend, and the first word equal to the total block count ends them.
- The size records have no count. They run while the ids ascend and each size lies inside its
  file's last block; the first pair that breaks either rule is fill.
- Writing (`DataBin.to_bytes`): a replacement may have any length. It is padded to whole blocks,
  every later file moves, and `DataBin.replace` updates the file's size record. The last file
  cannot be empty, or the table would lose it.

## File ids

`DataBin.files[i]` is engine file id `i`. `mhp-formats extract` writes engine file `i` as
`data_files/file_{i-1:05d}.<ext>` and skips file 0, so an extracted name is one below the id the
engine asks for (`mhfu.files.ENGINE_SKEW`). The extension comes from the magic: `.tmh`, `.pmf`,
`.wav`, `.vag`, else `.bin`.

Where things are, in extracted ids (`mhfu.files`):

| What | Function | Example |
|---|---|---|
| A species' model PAC | `monster_pac(species)` | Tigrex, species 75: `file_06185` |
| A big monster's AI overlay | `em_overlay(species)` | |
| `st<NNN>.pac`, `stage<NNN>.ovl` | `stage_pac(stage)`, `stage_overlay(stage)` | snowy base camp `st098`: `file_05905` |
| The hub's `st046` variants | `stage_variant_pac(variant)` | |
| In-quest engine, map table, hub overlays | `GAME_TASK`, `GAME_SUB`, `LOBBY_TASK` | |

An MHP3rd monster's model PAC is followed by its geometry companion (+1) and its moveset (+2)
(`mhfu_port.records.GEO`, `ANIM`).

## Encryption

The cipher is [mhef](https://github.com/svanheulen/mhef)'s `DataCipher`. `Game.MHFU` selects
its key set `MHP2G_EU`, `Game.MHP3RD` selects `MHP3_JP`; the key set only decides which files
are stored plain.

- The table is enciphered as one run seeded with block 0. Read the first word alone to learn the
  table's length.
- Each file is one run seeded with its own start block. The files on the key set's exception list
  (by engine file id) are stored plain.

A run, per `u32` word, from the seed's high and low halves `k0`, `k1` (a zero half becomes
`0x2345` or `0x7F8D`):

```text
k0 = k0 * 0x2345 % 0xFFD9
k1 = k1 * 0x7F8D % 0xFFF1
word ^= k0 << 16 | k1
```

Encryption XORs and then substitutes every byte through mhef's encode table; decryption
substitutes through the decode table and then XORs.

## Extracting a game

```bash
uv run mhp-formats extract game.iso extracted/
```

copies the image's files and DATA.BIN's decrypted files into `extracted/data_files/`. The game is
identified by the product code in `UMD_DATA.BIN`; only the two releases above are accepted.
