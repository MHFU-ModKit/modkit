# PAC

The container both games pack everything into: a count, an `(offset, size)` table and the
entries. Monster models, stages and the collision inside a stage are PACs. Read and written by
`mhp_formats.pac.Pac`. The engine's own accessor, `GET_SUBRESOURCE` in `mhfu.addresses`, returns
entry `i` through the same table.

## Layout

| Offset | Type | Meaning |
|---|---|---|
| 0x00 | `u32` | Entry count, 1 to 256 |
| 0x04 | `{u32 offset, u32 size}[count]` | Each entry's offset from the start of the PAC and its size |
| | bytes | The entries, in table order |
| | bytes | Tail: whatever follows the last entry up to the end of the file |

- An empty entry is `(0, 0)` in the table.
- Each entry starts at the next multiple of the PAC's alignment after the table or the entry
  before it, and the gap is zero bytes. The alignment is 16, 8 or 4 and is not stored:
  `Pac.from_bytes` takes the first of the three that reproduces the file.
- The tail is not part of any entry. A PAC extracted from DATA.BIN runs on to the end of its
  2048-byte block, and those bytes are disc leftovers, not always zeros (`Pac.tail` keeps them).

## Monster model PACs

### MHFU

`mhfu.files.monster_pac(species)`; every species has one, small monsters included. Alignment 16.

| Entry | Content |
|---|---|
| 0 | Skeleton, magic `0xC0000000` ([animation.md](animation.md)) |
| 1 | PMO `1.0` ([pmo.md](pmo.md)) |
| 2 | TMH, the textures entry 1's materials index ([tmh.md](tmh.md)) |
| 3 | Animation pack ([animation.md](animation.md)) |
| 4-6 | A second, small skeleton, PMO and TMH on some species; three empty entries on the rest |

`mhfu_port.model` reads entries 0 to 3 by these indices; entries 4 to 6 and the tail ride along
unchanged when a port is built.

### MHP3rd

A model PAC holds a skeleton (magic `0x80000000`), a PMO `102` with its TMH, and a second,
low-detail PMO with its TMH, beside entries modkit does not read; its last entry is not an
animation. The full model's geometry region lives in the next file, and the moveset is the file
after that, a standalone animation pack. `mhfu_port.mesh.donor` takes the PMO with the most
meshes.

## Stages

`st<NNN>.pac` and its collision entry are PACs too; their entries are in [stage.md](stage.md).
