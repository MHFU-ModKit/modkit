# mhfu-studio

One Qt window for authoring MHFU mods offline, with the commands behind it. Two workspaces:

- **Monster**: a ported monster's clips, labels, parts, hitboxes and moves, kept in its manifest
  in [`ports/`](../../ports), checked against the base monster's data from the game, and
  exported as a Lua module the framework loads (`<name>_hit.lua`).
- **Map**: edits to a stage's mesh, collision and textures, kept as edit lists in a `map.toml`
  document and pushed into the running game through PPSSPP's debugger; and named points (the
  Point tool, the Points panel), which `mhfu rig goto|walk` takes the hunter to.

## Install

In a clone of modkit, `uv sync` installs it with Qt (PySide6) and the Qt test plugin;
`uv sync --no-group qt` leaves out only the test plugin, for a machine without a display. For a
wheel, see the root README's [Wheels](../../README.md#wheels).

The studio needs the extracted games. It takes each from its variable (`MHFU_DATA`,
`MHP3RD_DATA`), else from the folder chosen under Setup on its start page, else it looks for
`workspace/extracted` and `workspace/extracted_mhp3` in the working directory and up to four
folders above it. Extract there
([`mhp-formats`](../../packages/mhp-formats/README.md#extracting-a-game)) and nothing needs
setting. Sending to the game also needs PPSSPP's memory stick (`MHFU_MEMSTICK`, else PPSSPP's
default).

## Example

```bash
uv run studio                                    # the window
uv run studio open ports/zinogre.toml            # the window on a port
uv run studio port check ports/zinogre.toml      # the same checks, on the command line
uv run studio port hit ports/zinogre.toml --deploy   # zinogre_hit.lua onto the memory stick
```

| Command | Does |
|---|---|
| `studio open [PATH]` | The window, with PATH open; `--workspace NAME`, `--size WxH` |
| `studio render selftest\|map\|monster` | A stage or a monster to PNG, without a window |
| `studio port scene\|clips\|align\|check\|hit` | A port's contents, clip coverage and labels, timing against the host, checks, and its Lua module |
| `studio map inject\|edit\|push\|exits\|surfaces\|budget\|textures\|verify` | A map document into the running game, one edit list offline or live, and what a stage holds |

`--help` on any of them says more.

## Tests

`uv run pytest apps/studio` runs offscreen; the Qt tests skip without the `qt` group, and the
few that need a real GL window run with `MHFU_UI_DISPLAY=1`. `apps/studio/docker/run.sh` runs
the tests, or any command, in a Linux container with Mesa's software GL, the renderer the render
goldens are pinned to:

```bash
apps/studio/docker/run.sh                                  # the studio's tests
MHFU_UI_DISPLAY=1 apps/studio/docker/run.sh                # with Qt on Xvfb: the GL ones too
apps/studio/docker/run.sh uv run studio render selftest -o out/
```

## Status

Both workspaces work. New map geometry has to fit the primitives a vertex group already has
(`studio map budget` says how much). The Qt tests run locally only: CI has no display.

## Licence

GPL-3.0-or-later: it is built on `mhp-formats`, which contains code derived from
[mhff](https://github.com/svanheulen/mhff).
