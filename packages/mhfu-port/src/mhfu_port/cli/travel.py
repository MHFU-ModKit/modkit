# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`mhfu-port travel`: how far each executor entry of a port or a monster PAC moves it."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

from mhp_formats import fu
from mhp_formats.pac import Pac
from mhp_formats.skeleton import Skeleton

from .. import data, layout, manifest, travel
from ..build import build
from ..model import ANIMATION, SKELETON

if TYPE_CHECKING:
    from . import Subparsers

MOVES = 50.0
"""Units under which an entry counts as standing, without --all."""


def register(sub: Subparsers) -> None:
    p = sub.add_parser("travel", help="how far each executor entry moves the monster in the game")
    p.add_argument("port", type=Path, help="a manifest (built here) or a model PAC")
    p.add_argument("clips", nargs="*", help="entries or clip names (default: those that move)")
    p.add_argument("--all", action="store_true", help="every filled entry, standing ones too")
    p.add_argument("--carry", action="store_true", help="as `travel.carry` leaves the build")
    p.add_argument("--scale", type=float, default=1.0, help="the monster's size (ENTITY+0x220)")
    p.add_argument("--speed", type=float, default=travel.SPEED, help="clip frames per AI frame")
    data.add_arguments(p)
    p.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    pac, names = _open(args)
    entries = Pac.from_bytes(pac).entries
    skeleton = Skeleton.from_bytes(entries[SKELETON])
    anim = fu.Anim.from_bytes(entries[ANIMATION])
    if args.carry:
        anim = travel.carry(anim, skeleton)
    by_name = {n: e for e, n in names.items()}
    for c in args.clips:
        if not c.isdigit() and c not in by_name:
            raise ValueError(f"no clip named {c}")
    wanted = [int(c) if c.isdigit() else by_name[c] for c in args.clips] or None
    rows = travel.of(anim, skeleton, wanted)
    if wanted is None and not args.all:
        rows = [t for t in rows if max(t.distance, abs(t.drawn[0]), abs(t.drawn[1])) >= MOVES]
    k = args.scale
    print(f"root joint {travel.root(skeleton)}; units x{k:g}, z forward; seconds at {args.speed:g}")
    print(
        f"{'entry':>5} {'clip':34} {'frames':>6} loop {'x':>6} {'z':>6} {'dist':>6}"
        f" {'drawn x':>7} {'drawn z':>7} {'s':>5}"
    )
    for t in rows:
        loop = "yes" if t.loop else "no"
        print(
            f"{t.entry:5d} {names.get(t.entry, ''):34.34} {t.frames:6d} {loop:4}"
            f" {t.carried[0] * k:6.0f} {t.carried[1] * k:6.0f} {t.distance * k:6.0f}"
            f" {t.drawn[0] * k:7.0f} {t.drawn[1] * k:7.0f} {t.seconds(args.speed):5.2f}"
        )
    lost = [t.entry for t in rows if abs(t.drawn[1]) >= MOVES or abs(t.drawn[0]) >= MOVES]
    if lost:
        print(
            f"{len(lost)} entries keep travel on joint 0, which the engine draws and drops: "
            "rebuild with the travel carried (--carry previews it)"
        )
    return 0


def _open(args: argparse.Namespace) -> tuple[bytes, dict[int, str]]:
    """The PAC and entry -> clip name: a manifest is built, a PAC read."""
    if args.port.suffix != ".toml":
        return args.port.read_bytes(), {}
    m = manifest.load(args.port)
    built = build(m, data.from_arguments(args))
    return built.pac, layout.names(m, built.layout)
