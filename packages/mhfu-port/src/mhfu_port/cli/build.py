# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`mhfu-port build` and `mhfu-port inject`: a manifest's model PAC and its clip layout module,
to files or to the game."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

from mhfu import inject

from .. import data, layout, manifest
from ..build import Built, build

if TYPE_CHECKING:
    from . import Subparsers


def register(sub: Subparsers) -> None:
    p = sub.add_parser("build", help="build a port's model PAC and its clip layout module")
    p.add_argument("manifest", type=Path)
    p.add_argument("-o", "--out", type=Path, help="default: the manifest's pac name, here")
    data.add_arguments(p)
    p.set_defaults(run=run_build)

    p = sub.add_parser("inject", help="build a port and place it for the relocate inject")
    p.add_argument("manifest", type=Path)
    p.add_argument("--dir", type=Path, help="inject directory (default: the memory stick's)")
    p.add_argument(
        "--lib", type=Path, help="where the clip layout module goes (default: the stick's mods/lib)"
    )
    data.add_arguments(p)
    p.set_defaults(run=run_inject)


def run_build(args: argparse.Namespace) -> int:
    m = manifest.load(args.manifest)
    built = build(m, data.from_arguments(args))
    out: Path = args.out or Path(m.port.pac)
    out.write_bytes(built.pac)
    module = _module(m, built, out.parent)
    print(f"wrote {out} and {module}\n{built.summary.text()}")
    return 0


def run_inject(args: argparse.Namespace) -> int:
    m = manifest.load(args.manifest)
    games = data.from_arguments(args)
    built = build(m, games)
    lib = args.lib or inject.memstick() / inject.MODS_SUBDIR / layout.LIB
    module = _module(m, built, lib)
    host = m.port.host_frame
    path = inject.write_relocate_bytes(
        built.pac, host, args.dir, orig=games.fu.read(host), name=m.port.pac
    )
    print(f"wrote {path} and {module}\n{built.summary.text()}")
    return 0


def _module(m: manifest.Manifest, built: Built, where: Path) -> Path:
    """Write the port's clip layout module into `where`, whole: the game reloads it on change."""
    where.mkdir(parents=True, exist_ok=True)
    path = where / layout.module_name(m)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(layout.lua(m, built.layout), encoding="utf-8")
    tmp.replace(path)
    return path
