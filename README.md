<p align="center">
  <img src="misc/banner.svg" width="720" alt="MHFU-MODKIT">
</p>

# MHFU ModKit

Libraries, a runtime mod framework and tools for modding Monster Hunter Freedom Unite (PSP, EU
release ULES01213), and for porting monsters to it from Monster Hunter Portable 3rd.

This repository replaces the six former MHFU-ModKit repositories (framework, example-mods,
monster-editor, hud, blender-addon, formats), which are archived.

## Layout

| Path | Contents |
|---|---|
| [`packages/mhp-formats`](packages/mhp-formats) | The games' file formats, and extracting a game from its ISO |
| [`packages/mhfu`](packages/mhfu) | The game: its address map (every language's is generated from it), live structs, automation, code analysis |
| [`packages/mhfu-port`](packages/mhfu-port) | The porter: MHP3rd monsters into MHFU |
| [`packages/ppsspp-debug`](packages/ppsspp-debug) | A client for PPSSPP's debugger |
| [`packages/modkit-testing`](packages/modkit-testing) | The pytest fixtures the packages share |
| [`framework`](framework) | The PRX plugin that runs mods in the game, its Lua host, and example mods in `framework/lua/examples/` |
| [`apps/studio`](apps/studio) | The studio: ported monsters and map edits |
| [`apps/hud`](apps/hud) | A live HUD next to PPSSPP |
| [`apps/blender`](apps/blender) | The Blender extension for monster models |
| [`ports`](ports) | Manifests of the monsters ported from MHP3rd: the Brute Tigrex and the Zinogre |
| [`ppsspp`](ppsspp) | PPSSPP patched for automation, its build, and a headless container to run it in |
| [`docs/formats`](docs/formats/README.md) | The file-format references |

## Getting started

Needs git and [uv](https://docs.astral.sh/uv/). Nothing is prebuilt: every part builds from
this repository.

```bash
git clone https://github.com/MHFU-ModKit/modkit
cd modkit
uv sync                                          # every package and app, with the test tools
uv run mhp-formats extract mhfu.iso workspace/extracted
export MHFU_DATA=$PWD/workspace/extracted/data_files
```

The extraction is the game data the tools read, from your own copy of the game; `workspace/` is
ignored by git, and the studio finds it there without `MHFU_DATA`. An MHP3rd extraction, for
porting, goes to `workspace/extracted_mhp3` with `MHP3RD_DATA` (see
[`mhp-formats`](packages/mhp-formats/README.md#extracting-a-game)). Then:

- the PRX plugin and how to install it: [`framework`](framework/README.md#build)
- the Blender extension: [`apps/blender`](apps/blender/README.md#build)
- PPSSPP with the debugger patches, or in Docker: [`ppsspp`](ppsspp/README.md#build)

Each package's own README has its commands. `uv sync --no-group qt` leaves out the Qt test
plugin, for a machine without a display.

### Wheels

```bash
uv build --all-packages --wheel                  # every package's wheel into dist/
pip install --find-links dist mhfu-studio        # elsewhere; its modkit dependencies come from dist/
```

`uv build --package NAME` builds one. The packages are not on PyPI.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Nothing from the games is committed here: no ISO, no
extracted files, no memory dumps, no disassembly.

## Licence

MIT, except the file-format library and everything that bundles it, which are GPL-3.0-or-later
because they contain code derived from [mhff](https://github.com/svanheulen/mhff), and the
PPSSPP patches, which are GPL-2.0-or-later like PPSSPP, and the PRX linker script, which is
PSPSDK's under BSD-3-Clause. Every file's licence is machine-readable
([REUSE](https://reuse.software/); check with `reuse lint`).

Not affiliated with or endorsed by Capcom. Monster Hunter is a trademark of Capcom Co., Ltd.
