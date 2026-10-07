# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`mhfu-port build` and `mhfu-port inject`: a manifest's model PAC and its clips and moves
modules, to files or to the game."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

from mhfu import inject

from .. import data, layout, manifest, moves
from ..build import Built, build

if TYPE_CHECKING:
    from . import Subparsers


def register(sub: Subparsers) -> None:
    p = sub.add_parser("build", help="build a port's model PAC and its clips and moves modules")
    p.add_argument("manifest", type=Path)
    p.add_argument("-o", "--out", type=Path, help="default: the manifest's pac name, here")
    data.add_arguments(p)
    p.set_defaults(run=run_build)

    p = sub.add_parser("inject", help="build a port and place it for the relocate inject")
    p.add_argument("manifest", type=Path)
    p.add_argument("--dir", type=Path, help="inject directory (default: the memory stick's)")
    p.add_argument(
        "--lib", type=Path, help="where the Lua modules go (default: the stick's mods/lib)"
    )
    data.add_arguments(p)
    p.set_defaults(run=run_inject)


def run_build(args: argparse.Namespace) -> int:
    m = manifest.load(args.manifest)
    games = data.from_arguments(args)
    built = build(m, games)
    out: Path = args.out or Path(m.port.pac)
    written = _modules(m, built, out.parent, games)
    out.write_bytes(built.pac)
    print(f"wrote {out}, {', '.join(map(str, written))}\n{built.summary.text()}")
    return 0


def run_inject(args: argparse.Namespace) -> int:
    m = manifest.load(args.manifest)
    games = data.from_arguments(args)
    built = build(m, games)
    lib = args.lib or inject.memstick() / inject.MODS_SUBDIR / layout.LIB
    written = _modules(m, built, lib, games)
    host = m.port.host_frame
    path = inject.write_relocate_bytes(
        built.pac, host, args.dir, orig=games.fu.read(host), name=m.port.pac
    )
    print(f"wrote {path}, {', '.join(map(str, written))}\n{built.summary.text()}")
    return 0


def _modules(m: manifest.Manifest, built: Built, where: Path, games: data.Data) -> list[Path]:
    """Write the port's clips and moves modules into `where`, each whole: the game reloads a
    module on change. Raises `ManifestError` for a move the moves module cannot carry, before
    either is written."""
    known = moves.records(games.fu, m.port.host_species)
    texts = {
        layout.module_name(m): layout.lua(m, built.layout),
        moves.module_name(m): moves.lua(m, built.layout, known),
    }
    where.mkdir(parents=True, exist_ok=True)
    out = []
    for name, text in texts.items():
        path = where / name
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
        out.append(path)
    return out
