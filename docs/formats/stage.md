# Stages

`st<NNN>.pac` holds one area of a map: terrain, textures, props, environment and collision. MHFU
only. Read and written by `mhp_formats.fu.stage.Stage`; the stage overlay and the runtime tables
by `mhfu.stage`; edits, offline and into a running game, by `mhfu_studio.stage`.

## Files

A stage number **is** the runtime area index (`AREA_INDEX` in `mhfu.addresses`). Stages run
0 to 266 (`mhfu.files.STAGES`), each with two files:

| File | Extracted id | Content |
|---|---|---|
| `st<NNN>.pac` | `mhfu.files.stage_pac(NNN)` | This page; there is no `st000.pac` |
| `stage<NNN>.ovl` | `mhfu.files.stage_overlay(NNN)` | The stage's code overlay (`mhfu.overlay.Overlay`), holding its parameter object |

The hub loads one of 16 `st046` variants in place of `st046.pac` (`stage_variant_pac`). Twenty
stages are placeholders: a table of empty entries, then disc leftovers.

## Entries

A PAC with alignment 16 ([pac.md](pac.md)):

| Entry | Content |
|---|---|
| 0 | PMO `1.0`: the terrain |
| 1 | TMH: the textures both PMOs' materials index ([tmh.md](tmh.md)) |
| 2 | PMO `1.0`: props, water, sky; empty in a few stages |
| 3 | Environment, below |
| 4 | Parameter block, starting `02 01`; the loader writes pointers into it; not decoded |
| 5 | Collision, below |
| 6, 7 | Present on eight stages; not decoded |

**Environment** (`Environment`):

| Offset | Type | Meaning |
|---|---|---|
| 0x00 | `u16` | 2 |
| 0x02 | `u8[4]` | Fog colour, RGBA |
| 0x06 | `f32[2]` | Fog range, start and end |
| 0x0E | bytes | Directions, colours and floats not yet told apart |

## Visible meshes

Entries 0 and 2 are ordinary PMOs ([pmo.md](pmo.md)) in world space, the frame the collision
and the player position use. Every stage group draws without normals or weights: the vertex
colour is the lighting. Positions and texture coordinates are 16-bit, no two groups share a
block, the bone palette is empty, and the lists draw triangle strips and lists. A PMO holds both
the playable area and a far backdrop.

## Collision

Entry 5 is a PAC with alignment 4 of two `HITS` chunks (`Collision`): chunk 0 holds walls,
ceilings and camera blockers, chunk 1 the walkable floor. A chunk (`Hits`):

| Offset | Type | Meaning |
|---|---|---|
| 0x00 | `char[4]` | `HITS` |
| 0x04 | `u32` | Chunk size, this header included |
| 0x08 | `u32[2]` | Cell size in x and z, in world units |
| 0x10 | `u32[2]` | Grid size `nx`, `nz` |
| 0x18 | `s32[2]` | Grid origin; (0, 0) in every shipped chunk, and the engine ignores it |
| 0x20 | `u32` | Grid offset, always 0x20 |
| 0x24 | `u32` | Triangle array offset |
| 0x28 | `u32[nx * nz]` | The grid: per cell, its list's offset |
| | `u32[]` | The cell lists, in cell order |
| | 56-byte records | The triangles |

- **Offsets inside a chunk count from the chunk's start + 8** (`BASE`), the address the engine
  holds; the grid offset 0x20 is therefore byte 0x28.
- Cell `(ix, iz)` is grid entry `ix * nz + iz`, with `ix = x // cell_x` and `iz = z // cell_z`.
  The engine adds no origin and checks no bounds, so the grid starts at world (0, 0) and must
  cover the map.
- A cell list is ascending `u32` entries, each a triangle index times 56 (the triangle's offset
  in the array), ended by `0xFFFFFFFF`. The longest shipped list holds 124.
- Shipped lists are approximate: they name cells a triangle does not reach, and leave out of
  cells it does reach mostly triangles whose xz projection is a line (walls, which no downward
  query hits). A rebuilt grid may list more than the exact overlap, never less; `build_hits`
  writes the exact overlap (`Hits.grid_check` compares).

**Triangle**, 0x38 bytes (`Tri`):

| Offset | Type | Meaning |
|---|---|---|
| 0x00 | `u8` | Surface id, 0 to 7: an entry of the overlay's surface table |
| 0x01 | `u8` | Material: the footstep and impact effect class |
| 0x02 | `u16` | Exclude mask: a query whose mask shares a bit skips the triangle |
| 0x04 | `f32[3]` x 3 | Vertices, world space |
| 0x28 | `f32[3]` | Unit normal; it decides floor against wall |
| 0x34 | `f32` | Plane: `dot(normal, v) + d = 0` |

The engine reads the first word as these three fields (`TriFlags`). A **climbable wall** is
material 9 or 10 on a near-vertical triangle, `|normal.y| < 0.34`
(`mhfu_studio.map.core.scene.CLIMB_MAX_UP`).

The **surface table** is a `u32` bitmask per surface id, held by the stage overlay
(`STAGE_PARAMS.SURFACES`), not by the PAC (`mhp_formats.fu.stage.SURFACE_BITS`):

| Bit | Effect |
|---|---|
| 0x01 | Sets a flag on the contact record |
| 0x02 | Sets a flag on the actor |
| 0x10 | Makes a point probe answer true |
| 0x20 | Sink: the surface sits above the ground (water, deep volumes) |
| 0x40 | With 0x20, some queries drop the triangle |
| 0x80 | Wade: some actor types stand on the surface instead of the ground |

## Outside the PAC

Runtime layouts live in `packages/mhfu/src/mhfu/addresses.toml`; `mhfu.stage` reads them.

- **Stage overlay parameter object** (`STAGE_PARAMS`): the surface table, spheres
  (`STAGE_SPHERE`), area exits (`STAGE_EXIT`: target stage, trigger cylinder, landing point and
  facing), and two placed-object arrays not yet decoded. `mhfu.stage.StageOverlay` reads it
  offline. The exit array has no spare room: adding an exit means moving the array and
  raising `EXIT_COUNT`.
- **Map table** in `game_sub.ovl` (`MAP_TABLE` of `MAP_ROW`): the stages of each map, entry area
  first.
- **Built at quest start**, on the heap and in no file: gathering spots (`GATHER_SPOT`) and
  small-monster spawn points (`SMALL_SPAWN`).

## Editing a running game

The loader copies the PAC into memory unchanged (`RESOURCE_TABLE` names where) and then rewrites
entries 4 and 5 with pointers. Each part is read at a different time:

| Part | The engine reads it | A write to the resident PAC |
|---|---|---|
| Meshes (0, 2) | once, compiled when the area loads | shows after the next area load |
| Textures (1) | every frame, in place | shows on the next frame |
| Collision (5) | on every query, in place | acts at once |
| Climbable material | when the area loads | must be held across the load |

A **quest area** evicts its PAC when the hunter leaves and reads it from disc again on the way
back, so every write is lost on a transition until re-applied (`studio map push --hold`). The
village (map row 0) never re-reads its PAC. `mhfu_studio.stage.live` writes each part.

**Meshes.** Rewrite vertices, indices and materials, or turn a `PRIM` into a `NOP`; nothing
else (`mhfu_studio.stage.mesh`):

- The engine compiles the display lists at load but reads the group table while it draws. Keep
  the PMO's length and move no block (`Pmo.to_bytes_inplace` reports moved groups; there must be
  none); a group record pointing somewhere new corrupts the frame at once.
- Never add a GE command: a new `PRIM` does not draw. New shapes fit into the primitives a group
  already has (`Block.pack`; `studio map budget` gives each group's room) and wear that group's
  material.
- In a quest area the write has to land while the loader puts the PAC down: a write watchpoint
  on the end of the last edited entry stops it there (`studio map push --catch`). The loader
  copies in 0x40000-byte pieces and stops at the start of the piece holding the watchpoint, so
  an edit inside that piece is overwritten (`mhfu_studio.stage.live.CATCH_CHUNK`). An armed
  watchpoint slows the game; the village needs none.
- Growing a group is a file edit for a repacked game (`mhfu_studio.stage.rebuild.grow`). Its new
  draw goes before the list's closing `OFFSETADDR`.

**Collision.** After the loader's fixup every offset in a chunk is an absolute address: the two
header words, each grid word (chunk start + 8 + value) and each list entry (triangle array +
value). So a moved triangle is rewritten in place, added triangles and changed cell lists can
live anywhere in free memory (`STAGE_SCRATCH`), and one grid word per cell links them in
(`mhfu_studio.stage.collision.plan`). Write collision after the area has loaded, when the fixup
has run. A list longer than 124 entries is untested, and a thin collider can be walked into at
speed: give it depth.

**Textures.** The bank sits between the two PMOs with no slack and every image is in use, so an
import replaces an image at its own size and format ([tmh.md](tmh.md#writing)).

**Climbable walls.** Mark one flat face; triangles facing several ways drag the hunter along the
surface.

## Engine facts

- Face culling is off: both sides of every face draw, so winding never hides geometry.
- The ground query casts downward from the actor and runs only while it moves. A floor added
  above the hunter is never hit.
- A live player position sits 140 units above the floor it stands on
  (`mhfu_studio.map.core.scene.STANDING_HEIGHT`).
