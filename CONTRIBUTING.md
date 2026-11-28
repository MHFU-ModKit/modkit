# Contributing

## Layout

One [uv](https://docs.astral.sh/uv/) workspace: every directory under `packages/` (libraries) and
`apps/` (the studio, the HUD, the Blender extension) is a member. `framework/` is the PRX in C++
with its Lua host, `ports/` the port manifests, `ppsspp/` the patched emulator, and
`docs/formats/` the file-format references.

## Setup

```bash
uv sync                      # every package, with the test and lint tools
uv sync --no-group qt        # the same without the Qt test plugin, for a machine with no display
uv run pre-commit install    # the checks below on every commit
```

The hooks run ruff (lint and format), `mypy --strict` over every package, shellcheck, and
[REUSE](https://reuse.software/), plus two of this repository's own:

- **Comment style.** Comments say what and why: no emoji, dates, retractions, "Phase N" or
  issue numbers.
- **No raw game addresses.** Addresses come from `packages/mhfu/src/mhfu/addresses.toml`, which
  generates the C header and the Lua table; Python reads it directly. A value that only looks
  like an address ends its line with a `noaddr` comment.

`uv run pre-commit run --all-files` runs them all by hand.

## Tests

`uv run pytest` runs everything this machine can. A test that needs something it lacks skips:

| Needs | Set |
|---|---|
| Extracted MHFU or MHP3rd | `MHFU_DATA`, `MHP3RD_DATA`: each extraction's `data_files` directory (`mhp-formats extract`) |
| A real PPSSPP | `PPSSPP_BINARY` (or `PPSSPP_CONTAINER`) and `PPSSPP_GAME` |
| The framework's host builds | a `c++` on `PATH` |
| The studio's Qt tests | the `qt` group and a Qt that loads; `MHFU_UI_DISPLAY=1` for the GL window ones |

The workflows in `.github/workflows/` are the CI: `lint`, `commits`, `api`, `docs`, `test`, `lua`,
`prx` and `qt`; the test jobs run the modules a change affects (`uv run modkit-ci affected`).
Locally, `uv run modkit-ci test <module> --cov` runs one unit, and `uv run
modkit-ci api --base main` is the API check. Docs: `uv sync --group docs && uv run modkit-ci docs
build --out build/docs`.

## Commits

[Conventional Commits](https://www.conventionalcommits.org/): `type(scope): lowercase summary`.
The scope is a module name from `uv run modkit-ci registry`, or `repo` for everything else (a
breaking change is `!` or a `BREAKING CHANGE:` footer). CI checks every commit with
`uv run modkit-ci commits --base main`; `uv run modkit-ci commit-msg <file>` checks one message.

## Releases

release-please opens one release PR per module from its commits; merging it tags
`<module>-v<version>` and `release.yml` attaches the assets (`modkit-ci bundle`). Nothing else
bumps a version.

## No game data

Nothing extracted, dumped, disassembled or rendered from the games is committed: no ISO, no
extracted files or PACs, no textures or renders, no memory dumps, no disassembly listings or
dumped tables. Format references describe layouts in words and tables. `workspace/` is ignored
by git for your extractions.

## Licences

Every new file carries an SPDX header matching its area:

| Area | Licence |
|---|---|
| `packages/mhp-formats`, `packages/mhfu-port`, `apps/studio`, `apps/blender` | GPL-3.0-or-later |
| `ppsspp/patches` | GPL-2.0-or-later |
| everything else | MIT |

```python
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Your Name
```

The REUSE hook checks it.
