# mhfu-blender

A Blender 4.2+ extension for MHFU monster models: import a PAC, edit it, export it as a PAC and
push it into the running game. It reads and writes through `mhp-formats` and `mhfu-port`.

```bash
uv run apps/blender/build.py           # apps/blender/dist/mhfu_blender-<version>.zip
uv run apps/blender/build.py test      # build, install into a throwaway profile, run tests/ in Blender
```

`--blender PATH` (or `$BLENDER`) picks the Blender; the default is
`/Applications/Blender.app` on macOS and `blender` on `PATH` elsewhere. Install the zip from
Blender's Preferences > Get Extensions > Install from Disk.

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

The zip carries its dependencies as pure-Python wheels at their `uv.lock` versions, so one zip
serves every OS. numpy is Blender's own; packages with no pure wheel (psutil, rabbitizer) are left
out, so the extension must not import the live-game chain that needs them.

Tests that need Blender take the `bpy` fixture and skip anywhere else; `build.py test` runs them
inside Blender with pytest from a cached directory under `.cache/`, never inside Blender itself.

Licensed GPL-3.0-or-later: it is built on `mhp-formats`, which contains code derived from
[mhff](https://github.com/svanheulen/mhff).
