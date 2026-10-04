# File formats

Byte layouts of the game files modkit reads and writes. The code is the reference: every format
below round-trips each file the games ship byte for byte, and each page names the class that
does it.

| Format | Page | Read and written by | What it is |
|---|---|---|---|
| DATA.BIN | [databin.md](databin.md) | `mhp_formats.databin`, `mhp_formats.iso` | The encrypted archive that holds every game file |
| PAC | [pac.md](pac.md) | `mhp_formats.pac.Pac` | The container: monster models, stages, collision |
| PMO | [pmo.md](pmo.md) | `mhp_formats.pmo`, `mhp_formats.p3rd.pmo`, `mhp_formats.psp` | Models: meshes, materials, bone palette, GE display lists |
| TMH | [tmh.md](tmh.md) | `mhp_formats.tmh.Tmh` | Texture banks |
| Skeleton, animation | [animation.md](animation.md) | `mhp_formats.skeleton`, `mhp_formats.anim`, `mhp_formats.fu.anim`, `mhp_formats.p3rd.anim`; `mhfu_port.fk` | Bone trees, bind poses, clip packs, and how the engine plays them |
| Stage | [stage.md](stage.md) | `mhp_formats.fu.stage.Stage`; `mhfu.stage`; `mhfu_studio.stage` | `st<NNN>.pac`: terrain, textures, collision, and the rules for editing a map |

## Conventions

- **Byte order** is little-endian everywhere.
- **Offsets** are hex and count from the start of the structure the table describes. Where a
  format counts from somewhere else (a `HITS` chunk, a PMO's geometry region), the page says so.
- **Types**: `u8`/`u16`/`u32` unsigned, `s8`/`s16`/`s32` signed, `f32` IEEE single, `char[n]`
  bytes. `T[n]` is an array of `n`. Sizes are in bytes.
- **File ids**: `file_NNNNN` is the name `mhp-formats extract` gives a file; the engine asks for
  it as id NNNNN + 1 (`mhfu.files.ENGINE_SKEW`). The functions in `mhfu.files` map species and
  stage numbers to file ids.
- **RAM addresses** are named by their key in `packages/mhfu/src/mhfu/addresses.toml`, which also
  holds the layouts of runtime structures.
- **Games**: MHFU is the EU release ULES-01213, MHP3rd is ULJM-05800. A table that does not name a
  game holds for both.
