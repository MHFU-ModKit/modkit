# mhfu-port

Ports Monster Hunter Portable 3rd big monsters into Monster Hunter Freedom Unite (EU). A port
rides an MHFU monster, the host: it keeps the host's AI and replaces its model PAC with the
donor's own skeleton, skinned model, textures and moveset. The framework injects that PAC into
the running game.

## Install

In a clone of modkit, `uv sync`, then `uv run mhfu-port --help`. For a wheel, see the root
README's [Wheels](../../README.md#wheels). Building reads both extracted games
(`mhp-formats extract`) from `--data` / `--p3rd-data`, or `MHFU_DATA` / `MHP3RD_DATA`.

## Example

A manifest in `ports/` names the donor's files, the host and the build settings (see
[`ports/`](../../ports)), and is the only input:

```bash
uv run mhfu-port build ports/zinogre.toml    # zinogre.bin, the model PAC, and zinogre_clips.lua
uv run mhfu-port inject ports/zinogre.toml   # the same, placed for the framework to load
```

```python
from mhfu_port import build, manifest
from mhfu_port.data import Data

port = build.build(manifest.load("ports/zinogre.toml"), Data.find())
port.pac, port.summary.text()
```

`build` runs as steps (`donor`, `host`, `record_map`, `binding`, `parts`, `skin`, `layout`,
`animation`, `pac`), each a function a tool can stop at:

| Module | Does |
|---|---|
| `records` | Which donor bone each MHP3rd animation record drives; the records are not positional |
| `rig` | The skeleton the port ships: the donor's, reordered so each animation stream is one run of joints |
| `retarget` | Matches the donor's bones to the host's, to ship the host's skeleton instead |
| `mesh`, `skin` | The donor's geometry, bound to the rig by the donor's own weights, by distance, or by the host's |
| `layout` | Which executor entry each donor clip goes to: the manifest's, else the packer's; and the Lua module that carries it to the game |
| `motion` | The donor's moveset, every stream, as MHFU's in-game animation in those entries; `put` writes an edited clip back into an entry |
| `fk` | The engine's forward kinematics: joint matrices and skinned positions for any clip and frame |
| `model` | A model PAC of either game read into skinned groups, textures and clips; imports nothing of `mhfu` |

## Checking a build

| Command | What it prints |
|---|---|
| `mhfu-port diff OLD NEW` | Two builds compared group by group and clip by clip |
| `mhfu-port verify PAC --manifest M` | A structural audit against the donor: stream partition, driven palette joints, tears across the body's fork, every vertex's weights |
| `mhfu-port validate PAC` | The engine's rules (`constraints`) on any monster PAC; `build` refuses a port that breaks one |
| `mhfu-port pose PAC --manifest M` | The port's clips against the donor's, joint by joint |
| `mhfu-port stretch PAC [--fork]` | How far each clip pulls the mesh apart, and the tear across the fork |
| `mhfu-port floor PAC... --host 75` | Where the idle pose puts the feet by the animation alone, against the host (not where the game puts a port) |
| `mhfu-port fidelity M` | The port's skin weights against the donor's own |
| `mhfu-port slots`, `labels` | Which host move plays which ported clip; hand labels checked against a build |

## Status

Builds donors whose animation-record offset is pinned in `records.OFFSET`: the Brute Tigrex and
the Zinogre, both on the Tigrex. A port's animations come across; its AI is the host's, and the
studio edits its hit volumes and attacks.

## Licence

GPL-3.0-or-later: it is built on `mhp-formats`, which contains code derived from
[mhff](https://github.com/svanheulen/mhff).
