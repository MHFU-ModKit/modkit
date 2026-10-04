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

The zip carries its dependencies as pure-Python wheels at their `uv.lock` versions, so one zip
serves every OS. numpy is Blender's own; packages with no pure wheel (psutil, rabbitizer) are left
out, so the extension must not import the live-game chain that needs them.

Tests that need Blender take the `bpy` fixture and skip anywhere else; `build.py test` runs them
inside Blender with pytest from a cached directory under `.cache/`, never inside Blender itself.

Licensed GPL-3.0-or-later: it is built on `mhp-formats`, which contains code derived from
[mhff](https://github.com/svanheulen/mhff).
