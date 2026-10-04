# mhfu-blender

A Blender 4.2+ extension for MHFU monster models: import a PAC, edit it, export it as a PAC and
push it into the running game. It reads and writes through `mhp-formats` and `mhfu-port`.

## Build

From a clone of modkit:

```bash
uv run apps/blender/build.py           # apps/blender/dist/mhfu_blender-<version>.zip
uv run apps/blender/build.py test      # build, install into a throwaway profile, run tests/ in Blender
```

The build packs and validates the zip with Blender itself: `--blender PATH` (or `$BLENDER`) picks
it; the default is the one in `/Applications/Blender.app` on macOS and `blender` on `PATH`
elsewhere. Install the zip from Blender's Preferences > Get Extensions > Install from Disk.

The zip carries its dependencies as pure-Python wheels at their `uv.lock` versions, so one zip
serves every OS. numpy is Blender's own; packages with no pure wheel (psutil, rabbitizer) are left
out, so the extension must not import the live-game chain that needs them.

## Example

Import `file_06185.bin` from an extracted MHFU (the Tigrex model), move some vertices in Edit
Mode, and press Push to Game in the MHFU tab: the PAC lands on the memory stick, and the report
gives the line a Lua mod needs to load it into the running game.

## Import

File > Import > MHFU Model (.bin), or the MHFU tab of the 3D view's sidebar, reads an MHFU
monster model PAC (`file_0{em + 6110}`), a built port, or an MHP3rd model PAC. An MHP3rd model
finds its geometry (`file_N+1`) and moveset (`file_N+2`) next to it unless you name them, and its
em id, which picks the record map, from its file name unless you give it.

- **Units.** Everything inside the armature is in game units with Y up. The armature object is
  turned Z up and scaled 0.01 for show; that transform is not part of the model, so edit in Edit
  and Pose Mode and leave the mesh objects' own transforms alone.
- **Bones** are `jNN` (joint NN, plus the MHP3rd name) and all rest pointing up, so a pose bone's
  channels are the game's: rotation is Euler XYZ in radians, location is the offset from the bind
  position. Scale and the unnamed channels ride on the bone's `mhfu_ch_*` custom properties and
  move nothing: the game ignores scale and MHFU crashes on it.
- **Meshes**: one object per PMO group, weighted by the file's own skin; keep each group's vertex
  order.
- **Clips**: one Action per animation slot, named `clip_NN`. Pick one in the MHFU tab, which also
  resets the pose: switching in the Action Editor leaves a channel the new clip does not key
  where the old one left it, while the game plays it at bind.
- **Keys.** Between two keys the game draws a cubic whose slopes are the keys' eases; Blender
  draws the same curve from Bezier keys with Free handles a third of the way to the next key.
  What reaches the game is each key's frame (whole frames), value (rotation in 1/4096 of 90
  degrees, location in 1/16 unit) and the slope of each handle, not its length; a Linear segment
  goes straight, other modes are read by their handles.
- The .blend keeps the source files' bytes, so export does not need them again.

## Export

File > Export > MHFU PAC (.bin), or Export PAC in the MHFU tab, writes the selected monster as a
model PAC. An untouched monster comes out byte for byte.

- **Groups.** A group whose vertex count and faces are unchanged is written in place: positions,
  UVs, the normals you changed, and weights over the joints its palette already holds. The PAC
  keeps its size. Adding or deleting vertices or faces, or weighting a vertex to a joint the
  group has no palette slot for, rebuilds that group (8 joints at most) and the PAC grows. A group whose object is deleted draws nothing; a vertex with no weight is refused.
- **Objects.** Moving a mesh object in Object Mode moves its vertices in the game. Modifiers and
  shape keys are not applied. A PMO vertex has one UV, so a vertex on a UV seam is split into one
  per UV, which rebuilds its group.
- **Skeleton.** The bones' rest heads are the bind pose, so Edit Mode moves ship. Adding, deleting
  or re-parenting a bone is refused; a bone's rest rotation and length are not part of the model.
- **Clips.** Every slot's Action is written back and a slot nobody edited stays byte for byte; a
  key keeps what the Keys item above lists. A bone whose keys are all deleted holds its bind pose
  (an empty track would collapse its mesh).
- **Checks.** `mhfu-port`'s engine rules run on the result: an error the source does not have
  stops the export, the rest are reported.
- **MHP3rd.** An MHP3rd model exports as an MHFU PAC with its geometry, skeleton and textures and
  no clips. An in-game monster from it is a port: build it from a manifest (`mhfu-port build`).

## Push to Game

Push to Game in the MHFU tab exports into the memory stick's inject folder,
`PSP/PLUGINS/mhfu_framework/inject/`, with the source as the `.orig` the framework recognises the
loaded file by; the file id comes from the imported `file_NNNNN` name. A PAC of the source's size
replaces the file in place (`file_NNNNN.bin`), a larger one takes the relocate path
(`file_NNNNN_grown.bin`). A Lua mod loads it; the report gives the call. The memory stick is the
extension's preference, else `$MHFU_MEMSTICK`, else PPSSPP's default folder.

## Tests

Tests that need Blender take the `bpy` fixture and skip anywhere else; `build.py test` runs them
inside Blender with pytest from a cached directory under `.cache/`.

## Status

Blender 4.2 or newer; tested on 4.2, 4.5 and 5.2. The Blender tests run locally only: CI has no
Blender.

## Licence

GPL-3.0-or-later: it is built on `mhp-formats`, which contains code derived from
[mhff](https://github.com/svanheulen/mhff).
