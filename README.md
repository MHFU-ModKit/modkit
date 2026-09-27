<p align="center">
  <img src="misc/banner.svg" width="720" alt="MHFU-MODKIT">
</p>

# MHFU ModKit

Libraries, a runtime mod framework and tools for modding Monster Hunter Freedom Unite (PSP, EU
release ULES01213), and for porting monsters to it from Monster Hunter Portable 3rd.

> **Under construction.** This repository replaces the six
> [MHFU-ModKit](https://github.com/MHFU-ModKit) repositories, which stay the current release
> until it is done.

## Layout

| Path | Contents |
|---|---|
| `packages/` | Python libraries: the debugger client, the file formats, the game, the porter |
| `framework/` | The PRX plugin and its Lua host |
| `apps/` | The studio (monster and map editor), the HUD, the Blender extension |
| `examples/` | Example mods |
| `data/` | Single-source data, such as the address map the Python, C and Lua constants are generated from |
| `docs/` | Guides and format references |

## Development

Needs [uv](https://docs.astral.sh/uv/).

```bash
uv sync                          # every package, plus pytest and pre-commit
uv run pre-commit install        # ruff, licence headers, comment style on each commit
uv run pytest
```

Tests that read game files take them from `MHFU_DATA` and `MHP3RD_DATA`, each a directory of
extracted `DATA.BIN` files, and skip when the variable is unset. Nothing from the games is
committed here: no ISO, no extracted files, no memory dumps.

## Licence

MIT, except the file-format library and everything that bundles it, which are GPL-3.0-or-later
because they contain code derived from [mhff](https://github.com/svanheulen/mhff). Every file's
licence is machine-readable ([REUSE](https://reuse.software/); check with `reuse lint`).

Not affiliated with or endorsed by Capcom. Monster Hunter is a trademark of Capcom Co., Ltd.
