# mhfu-port

Ports Monster Hunter Portable 3rd big monsters into Monster Hunter Freedom Unite (EU). A port
rides an MHFU monster, the host: it keeps the host's AI and replaces its model PAC with the
donor's own skeleton, skinned model, textures and moveset. The framework injects that PAC into
the running game.

## A port

A manifest in `ports/` names the donor's files, the host and the build settings (see
`ports/README.md`), and is the only input:

```bash
mhfu-port build ports/zinogre.toml          # zinogre.bin, the model PAC
mhfu-port inject ports/zinogre.toml         # the same, placed for the framework to load
```

Both read the extracted games from `--data` / `--p3rd-data`, or `MHFU_DATA` / `MHP3RD_DATA`.

```python
from mhfu_port import build, manifest
from mhfu_port.data import Data

port = build.build(manifest.load("ports/zinogre.toml"), Data.find())
port.pac, port.summary.text()
```

`build` runs as steps (`donor`, `host`, `record_map`, `binding`, `parts`, `skin`, `animation`,
`pac`), each a function a tool can stop at:

| Module | Does |
|---|---|
| `records` | Which donor bone each MHP3rd animation record drives; the records are not positional |
| `rig` | The skeleton the port ships: the donor's, reordered so each animation stream is one run of joints |
| `retarget` | Matches the donor's bones to the host's, to ship the host's skeleton instead |
| `mesh`, `skin` | The donor's geometry, bound to the rig by the donor's own weights, by distance, or by the host's |
| `motion` | The donor's moveset as MHFU's in-game animation, on the host's slots |
| `fk` | The engine's forward kinematics: joint matrices and skinned positions for any clip and frame |

## Checking a build

`mhfu-port diff OLD NEW` compares two builds group by group and clip by clip. `constraints`
holds the engine's rules on a monster PAC; `build` refuses a port that breaks one.

## Licence

GPL-3.0-or-later.
