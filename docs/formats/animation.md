# Skeleton and animation

A monster's skeleton (bone tree and bind pose) and its animation pack (clips of keyframed
channels per bone) are separate entries of its model PAC ([pac.md](pac.md)). Read and written
by `mhp_formats.skeleton.Skeleton`, `mhp_formats.anim.AnimPack` and the clip encodings
`mhp_formats.fu.anim.Anim` (MHFU) and `mhp_formats.p3rd.anim.Anim` (MHP3rd). How the engine
plays them is modelled by `mhfu_port.fk`; the rules an edit must keep are
`mhfu_port.constraints.RULES`, which `mhfu-port validate <pac>` checks.

## Skeleton

| Offset | Type | Meaning |
|---|---|---|
| 0x00 | `u32` | Magic: `0xC0000000` MHFU, `0x80000000` MHP3rd |
| 0x04 | `u32` | Bone count + 1 |
| 0x08 | `u32` | Size, up to the end of the last bone; bytes after it are the tail |
| 0x0C | `u32` | 0, the parameter section's tag |
| 0x10 | `u32` | Parameter count; on a few MHP3rd skeletons it disagrees with the values (`Skeleton.params_count`) |
| 0x14 | `u32` | Parameter section size, 12 + 4 x values |
| 0x18 | `u32[]` | 1 to 3 values; on MHFU's big monsters the second is the animated joint count |

The bone sections follow the parameter section, at 0x18 + 4 x values. One per bone:

| Offset | Type | Meaning |
|---|---|---|
| 0x00 | `u32` | `0x40000000 \| kind`, kind 1, 2 or 3 |
| 0x04 | `u32` | 1 |
| 0x08 | `u32` | Section size: 0x10C MHFU, 0x5C MHP3rd |
| 0x0C | `s32` | Index: the bone's own position |
| 0x10 | `s32` | Parent, -1 for a root |
| 0x14 | `s32` | First child, -1 for none |
| 0x18 | `s32` | Next sibling, -1 for none |
| 0x1C | `f32[4]` | Bind scale, w = 1 |
| 0x2C | `f32[4]` | Bind rotation (Euler), w = 1; zero on both games' monsters |
| 0x3C | `f32[4]` | Bind position, the offset from the parent, w = 1 |
| 0x4C | `s32` | On kind-2 bones an earlier bone's index, else -1 |
| 0x50 | `u32` | MHFU: the animation part that drives the bone (below). MHP3rd: 0 |
| 0x54 | | MHFU: zeros to 0x10C. MHP3rd: an 8-byte name, a C string then leftovers |

On the monsters the bind pose is pure translation: a joint's bind position in model space is
the sum of the offsets up its chain (`mhfu_port.fk.bind_world`).

## Animation pack

Both games share the container. It holds streams; a stream is a table of slots, each slot a clip
or empty.

| Offset | Type | Meaning |
|---|---|---|
| 0x00 | `{u32 slots, u32 table}[N]` | Per stream, its slot count and its slot table's offset. Stream 0's table starts right after the header, at 8 x (N + 1) |
| 8N | `u32` | 0 |
| 8N + 4 | `u32` | End of the slot tables, where the first clip starts |

The slot tables follow back to back: `u32` per slot, a clip's offset from the start of the pack or
`0xFFFFFFFF` for an empty slot. The clips follow back to back in the order the slots first use
them; slots may share one clip. Bytes after the last clip are the tail.

MHFU packs have 2, 4, 6 or 10 streams.

## Clips

A clip holds one track per bone it drives, a track one channel per animated component, a channel
its keyframes. Every size includes its own header.

**MHFU** (`fu.anim`), sections of `u32 tag, u32 count, u32 size`:

| Level | Header | Count |
|---|---|---|
| Clip | `0x80000002`, count, size, `u32 loop`, `f32 loop_start` (0x14) | tracks |
| Track | `0x80000000 \| mask` (the channel bits it holds), count, size | channels |
| Channel | `0x80120000 \| bit`, count, size = 12 + 8 x keys | keyframes |

**MHP3rd** (`p3rd.anim`), the same nesting with compact headers and no tags:

| Level | Header |
|---|---|
| Clip | `u32 tracks`, `u32 size`, `u32 loop`, `f32 loop_start` (0x10) |
| Track | `u16 channels`, `u16 size` |
| Channel | `u16 bit`, `u16 keys`, `u32 size` = 8 + 8 x keys |

**Keyframe**, 8 bytes in both: `s16 value`, `s16 frame`, `s16 ease_in`, `s16 ease_out`.

### Channels

| Bit | Channel | One raw unit |
|---|---|---|
| 0x008, 0x010, 0x020 | rotation X, Y, Z | pi / 8192 radians (4096 = 90 degrees) |
| 0x040, 0x080, 0x100 | location X, Y, Z | 1 / 16 |
| 0x200, 0x400, 0x800 | scale X, Y, Z | 1 / 256 |

Bits 0x001, 0x002 and 0x004 also occur in shipped clips, with values near 16; their meaning is
not known. Ease values use the channel's unit, per frame.

## How the engine plays a clip

- **Length**: a clip runs to its last keyframe; there is no separate duration. A nonzero `loop`
  loops it.
- **Interpolation**: between keys `(t0, x0)` and `(t1, x1)` a cubic Hermite, the left key's
  `ease_out` and the right key's `ease_in` the slopes. With `s = (t - t0) / (t1 - t0)` and
  `d = t - t0`:
  `x = x0 (1 - 3s^2 + 2s^3) + x1 (3s^2 - 2s^3) + ease_out0 d (1 - s)^2 + ease_in1 d (s^2 - s)`.
  Before the first key and after the last a channel holds.
- **Pose**: a joint's rotation is its rotation channels as Euler angles composed Rz Ry Rx; its
  translation is its location channels, an axis without a channel keeping the bind offset. A
  joint's world matrix is its parent's times its own. Scale channels are not played.

### MHFU: parts and streams

MHFU splits a rig into parts by `Bone.stream`, and part `k`'s clips sit in streams `2k` and
`2k + 1`, one track per joint of the part in joint order. The engine plays an executor entry
(the action id `a1`): entry `e` of part `k` is slot `e % 100` of stream `2k + e // 100`, so
entries 0-99 play the even streams and 100 and up the odd ones (`mhfu_port.fk.entry_slot`); the
odd half is read from the resolver's arithmetic and not yet seen playing in the game. Many
natives fill the odd streams of all three parts alike; the Tigrex leaves them empty and has 123
entries. An entry plays every part's clip together; an entry may exist in only some parts,
such as a head-and-neck clip over an idle body. `mhfu_port.fk.rig_clip` joins an entry's parts
into one clip over the whole rig, and `mhfu_port.motion.put` writes one back.

- The animated joints are the first `params[1]` bones. Each part is a contiguous run of joint
  indices: part 0 the body, each later part one subtree hanging off one joint (the head, the
  tail).
- The joints past the animated count carry one more part id and are not animated; on a monster
  with a cuttable tail they are a second root chain, the severed tail.
- Within a part, track `i` drives the part's `i`-th joint.

### MHP3rd: clip sets and records

MHP3rd's streams are separate clip sets over the whole rig, and a monster's AI plays clips from
all of them; a clip's id is `stream * 100 + slot` (`mhfu_port.motion.clip_id`). Its tracks are
not positional: record `i` drives the `i`-th bone counted from a per-monster offset,
stepping over the bones the moveset never drives. The offset follows the fork rule: every record
with location channels lands at or above the body fork, the lowest joint with more than one
child. `mhfu_port.records` holds the map (`record_to_bone`, `body_fork`, `loc_below_fork`).

## Engine rules (MHFU)

What a pack and skeleton must keep for the game to load and play them. The full list, with
fixes, is `mhfu_port.constraints.RULES`.

- **No scale channels.** The engine looks each channel up in a table indexed by its bit and
  crashes on the scale bits. Rotation, location and the three unnamed bits play.
- **No empty track on an animated joint.** A joint without channels keeps a zeroed matrix and
  its geometry collapses to the origin. Hold a joint still with zero-rotation keys spanning the
  clip (`mhfu_port.motion.rest`).
- **The parts must match the skeleton.** Each stream's clips carry one track per joint of its
  part, the parts are contiguous runs, and every later part is one subtree.
- **Every parent comes before its children** in bone order.
- **The species' overlay and AI assume its bone count and hierarchy.**
  `mhfu-port validate --template SPECIES` checks that a skeleton keeps them.
- Keyframes sit at frame 0 or later, in frame order, and no channel appears twice in a track.
