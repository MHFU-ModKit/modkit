# PMO

The model format of both games: meshes of vertex groups, each group a PSP GE display list with
the vertex and index buffers it draws, plus materials and a bone palette. Skeletons and
animation are separate PAC entries ([animation.md](animation.md)). Read and written by
`mhp_formats.pmo.Pmo` (MHFU, version `1.0`) and `mhp_formats.p3rd.pmo.Pmo` (MHP3rd, `102`); the
GE encodings are in `mhp_formats.psp`. The display-list walker and vertex decoder derive in part
from [mhff](https://github.com/svanheulen/mhff) by Seth VanHeulen.

| | MHFU `1.0` | MHP3rd `102` |
|---|---|---|
| Mesh record | 0x18 | 0x30, with the mesh's own scale |
| Position scale | header | per mesh |
| Alignment inside the geometry region | 16 | 4 |
| Remap table | always | on few models; offset 0 when absent |
| Geometry region | inline | inline, or in a companion file (the next DATA.BIN file) |

## Header (0x40)

| Offset | Type | Meaning |
|---|---|---|
| 0x00 | `char[4]` | `pmo\0` |
| 0x04 | `char[4]` | Version, `1.0\0` or `102\0` |
| 0x08 | `u32` | Size: header, tables and geometry region. Bytes after it are the tail |
| 0x0C | `f32` | Cull-sphere radius before the entity's scale; MHFU sets the largest scale component, and 0 culls the model as a point |
| 0x10 | `f32[3]` | Scale (MHFU): multiplies the dequantised positions |
| 0x1C | `u16` | Mesh count |
| 0x1E | `u16` | Material count |
| 0x20 | `u32` | Mesh table offset |
| 0x24 | `u32` | Group table offset |
| 0x28 | `u32` | Remap table offset, 0 for none |
| 0x2C | `u32` | Bone palette offset |
| 0x30 | `u32` | Material table offset |
| 0x34 | `u32` | Geometry region offset |
| 0x38 | 8 bytes | Zero |

Offsets count from the start of the PMO. The tables follow the header in the order mesh, group,
remap, bone palette, material, each starting on a 16-byte boundary (zero padding), and the
geometry region follows the materials.

An MHP3rd PMO with a companion file ends at the geometry offset: there the geometry offset
equals the file's length, the size still counts the region, and the region starts at offset 0 of
the companion (`Pmo.needs_geometry`).

## Mesh record

A mesh owns a run of groups and a run of the remap table.

MHFU, 0x18 bytes:

| Offset | Type | Meaning |
|---|---|---|
| 0x00 | `f32[2]` | UV scale |
| 0x08 | `u32` | Lighting flags: bit 0 lighting, 1 fog, 2 alpha blending, 31 enable |
| 0x0C | `u32` | A GE `BLENDMODE` command word, or 0 |
| 0x10 | `u8` | Remap entries this mesh owns |
| 0x11 | `u8` | Zero |
| 0x12 | `u16` | First remap entry |
| 0x14 | `u16` | Group count |
| 0x16 | `u16` | First group |

MHP3rd, 0x30 bytes: `f32[4]` scale (x, y, z, and a fourth value kept as is), `f32[2]` UV scale,
`f32[2]` UV offset, then lighting, blend and the same 8 bytes of counts. The mesh scale replaces
the header's for the mesh's groups.

Meshes are contiguous: each starts its groups and remap entries where the one before ended.

## Group record (0x10)

| Offset | Type | Meaning |
|---|---|---|
| 0x00 | `u8` | Material: an index into the owning mesh's remap run |
| 0x01 | `u8` | Bone-palette entries this group applies |
| 0x02 | `u16` | First of them; the running total over the groups before |
| 0x04 | `u32` | Display list offset |
| 0x08 | `u32` | Vertex buffer offset |
| 0x0C | `u32` | Index buffer offset, 0 when the list draws without indices |

The three buffer offsets count from the geometry region. Two records may point at one block; no
shipped PMO does.

## Materials

The **remap table** is one `u8` per entry, a material table index. A group draws with
`materials[remap[mesh.first_remap + group.material]]` (`Pmo.material`). Without a remap table
the entries run in order.

A **material** is 0x10 bytes:

| Offset | Type | Meaning |
|---|---|---|
| 0x00 | `u32` | Colour, RGBA with red in the low byte. MHFU does not draw a group whose material has alpha 0 |
| 0x04 | `u32` | Ambient colour, same layout |
| 0x08 | `u8` | TMH image index; 0xFF draws untextured |
| 0x09 | 3 bytes | Zero |
| 0x0C | `u32` | Leftover memory on some MHFU models, else 0 |

## A big monster's meshes

The engine draws a big monster mesh by mesh, skips a mesh whose entity mask byte is 0, and lets
the species overlay set each material's alpha before the draw, so a mesh or a material hides as a
whole. A monster with a severable tail keeps the tail's tip in **mesh record 1**, skinned only to
the skeleton's second root chain, the one at `params[1]`
([animation.md](animation.md#skeleton)):

- While the tail is whole, the engine copies a tail joint's pose onto each chain joint every frame
  and draws the tip in place.
- At the cut it masks mesh 1 off the monster and spawns the dropped tail, an object that draws
  the monster's own mesh 1, its chain posed from its binds. A model without a mesh record 1
  crashes the game there.
- The Tigrex (em75) hides mesh 1's material 1, the tip's cut face, and mesh 5's material 2, the
  body's stump cap, until the cut; its meshes 2, 3, 4 and 6 have other alpha rules.

`mhfu-port` builds a port this way (`mhfu_port.mesh.build`): mesh 1 holds the parts that ride only
the chain, its material 0 the tip, 1 the cut face (the part that welds to the body at the tip's
offset and faces along the tail), then the tip's other textures, each its own material. The
chain's `(joint, carrier)` pairs come from `mhfu_port.rig.tip_of`, and on the 35 native rigs with
a chain they are the carriers the engine copies.

## Bone palette and skinning

The palette is an array of `{u8 slot, u8 bone}`: "matrix `slot` now holds skeleton bone
`bone`". Each group applies its run of entries on top of the palette as the groups before it
left it, in group-table order, so a slot keeps its bone until a later group changes it
(`Pmo.palette`).

A vertex's weight `k` blends the bone in slot `k` (`Pmo.influences`). A vertex type without
weights rides the bone in slot 0 rigidly. The GE takes at most 8 weights per vertex. Models with
no skeleton, such as stages, have an empty palette.

## Geometry region

One block per group, in group-table order: the display list, then the vertex buffer, then the
index buffer, each starting on the version's alignment, zero bytes between. The region is padded
with zeros to a multiple of 16, and the header's size counts that padding. The vertex count of a
block is not stored; it is what the draws reach.

### Display list

A list of `u32` commands, opcode in the top byte and a 24-bit argument, ending at `RET` or
`END` (`mhp_formats.psp.ge`). The games' model lists use:

| Op | Name | Argument |
|---|---|---|
| 0x00 | `NOP` | |
| 0x01 | `VADDR` | Vertex buffer address |
| 0x02 | `IADDR` | Index buffer address |
| 0x04 | `PRIM` | Primitive type in bits 16-18, vertex count in bits 0-15 |
| 0x0B | `RET` | Ends the list |
| 0x10 | `BASE` | Bits 16-19 become bits 24-27 of the next addresses |
| 0x12 | `VTYPE` | Vertex type, below |
| 0x13 | `OFFSETADDR` | Address offset = argument << 8 |
| 0x14 | `ORIGIN` | Address offset = this command's own address |
| 0x9B | `FFACE` | Bit 0 = 1 reverses the winding of the triangles that follow |

An address is `(offset + (BASE bits | argument)) & 0x0FFFFFFF`. The lists open with `ORIGIN`, so
a stored `VADDR` or `IADDR` argument is the buffer's distance from that `ORIGIN` command. Most
close with `OFFSETADDR 0`; a `VADDR` or `IADDR` after it is absolute, so a draw appended there
finds no vertices. One list sets one vertex type.

After a `PRIM` the GE moves `IADDR` past the indices it read, or, drawing without indices,
`VADDR` past the vertices. Primitive types: 0 points, 1 lines, 2 line strip, 3 triangles, 4
triangle strip, 5 fan, 6 rectangles; the models draw strips and lists. A strip alternates
winding from one triangle to the next, a list and a fan do not (`psp.ge.triangles`). MHFU draws
with face culling off, so a face shows from both sides whatever its winding.

### Vertex type

The `VTYPE` argument (`mhp_formats.psp.vtype.VertexType`):

| Bits | Field | Values |
|---|---|---|
| 0-1 | Texture coordinates | 0 none, 1 `u8`, 2 `u16`, 3 `f32` |
| 2-4 | Colour | 0 none, 4 BGR565, 5 ABGR5551, 6 ABGR4444, 7 ABGR8888 |
| 5-6 | Normal | 0 none, 1 `s8`, 2 `s16`, 3 `f32` |
| 7-8 | Position | 1 `s8`, 2 `s16`, 3 `f32` |
| 9-10 | Weights | 0 none, 1 `u8`, 2 `u16`, 3 `f32` |
| 11-12 | Index | 0 none, 1 `u8`, 2 `u16`, 3 `u32` |
| 14-16 | Weight count - 1 | |
| 18-20 | Morph count - 1 | |
| 23 | Through mode | Screen-space vertices: raw positions, texel UVs |

Bits 13, 17, 21 and 22 are unused and kept as read.

A vertex lays out weights, texture coordinates, colour, normal, position in that order, each
aligned to its component size, padded to its largest component; with morphing, the targets of a
vertex follow each other. Fixed-point components divide by 128 (8-bit) or 32768 (16-bit):
position and normal are signed, texture coordinates and weights unsigned, so a weight of 1.0 is
128 or 32768. Positions are then multiplied by the scale. Colour format `c` is texture format
`c - 4` ([tmh.md](tmh.md#colour-formats)).

## Editing

- `Pmo.to_bytes` lays the region out afresh and reproduces every shipped PMO.
- `Pmo.to_bytes_inplace(original)` keeps the original's length: a block that still fits stays
  at its offset, one that grew moves into the first hole that holds it, and the groups whose
  block moved are returned. A PMO written into a running game must move none: the engine
  compiles the lists at load but reads the group table while it draws
  ([stage.md](stage.md#editing-a-running-game)).
- `Block.pack`, `Block.clear_prims` and `Block.clear(keep_layout=True)` change what a group
  draws without adding or moving a command, which is the only kind of edit a loaded model takes.
