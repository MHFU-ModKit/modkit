# mhfu-hud

A live HUD for MHFU next to PPSSPP: vitals, the quest map with the monsters on it, each
monster's AI cells and its species row, drawn in a pygame window. It needs a running PPSSPP
with its remote debugger on: Settings > Tools > Developer tools > Allow remote debugger.

## Install

In a clone of modkit, `uv sync`. For a wheel, see the root README's
[Wheels](../../README.md#wheels).

## Example

```bash
uv run hud                  # finds the PPSSPP on this machine and waits for it
uv run hud --read-only      # never writes game memory
uv run hud --shot out/      # one PNG per tab, no window, then exit
```

`--host`, `--port` (default: read from the PPSSPP process), `--poll-hz` (default 3),
`--fullscreen`, `--assets DIR`. Keep the poll rate low: PPSSPP stops the game for every
read, and a high rate stalls the emulator.

## What writes

Only the QUEST_PREP tab writes game memory: the size, species and HP edits staged there are
written to each matching monster once it spawns, by one writer thread. LIVE and AI_MOD only
read. A badge in the status row shows while edits are staged and on; `--read-only` starts no
writer at all.

## Artwork

None is included. The HUD reads a folder (`--assets DIR`, else `$MHFU_HUD_ASSETS`, else
`${XDG_DATA_HOME:-~/.local/share}/mhfu-hud/assets`) and draws placeholders for anything missing:

```
monsters/<slug>.png      monster icons, slug from the name ("tigrex", "giadrome")
items/<slug>.png         item icons ("paintball")
maps/<slug>.png          map images ("snowy_mountains")
backgrounds/village.png  the village background (.jpg works too)
```

A `manifest.json` in `monsters/`, `items/` or `maps/` maps a slug to another file name. Map
calibration you save lands in `${XDG_CONFIG_HOME:-~/.config}/mhfu-hud/calibration.json`.

## Status

Reads vitals, the map, the monsters and their AI cells live, and writes nothing unless you stage
an edit in QUEST_PREP. Only the Snowy Mountains map is calibrated.

## Licence

MIT.
