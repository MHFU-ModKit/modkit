# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`mhfu-port travel`: how far each executor entry of a port or a monster PAC moves it."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from mhfu.live import observe
from mhp_formats import fu
from mhp_formats.pac import Pac
from mhp_formats.skeleton import Skeleton

from .. import data, layout, manifest, motion, travel
from ..build import authored, build
from ..model import ANIMATION, SKELETON

if TYPE_CHECKING:
    from . import Subparsers

MOVES = 50.0
"""Units under which an entry counts as standing, without --all."""
TURNS = 20.0
"""Degrees under which it counts as facing where it did."""


def register(sub: Subparsers) -> None:
    p = sub.add_parser("travel", help="how far each executor entry moves the monster in the game")
    p.add_argument("port", type=Path, help="a manifest (built here) or a model PAC")
    p.add_argument("clips", nargs="*", help="entries or clip names (default: those that move)")
    p.add_argument("--all", action="store_true", help="every filled entry, standing ones too")
    p.add_argument("--carry", action="store_true", help="a PAC as `travel.carry` leaves it")
    p.add_argument("--scale", type=float, default=1.0, help="the monster's size (ENTITY+0x220)")
    p.add_argument("--speed", type=float, default=travel.SPEED, help="clip frames per AI frame")
    p.add_argument(
        "--against", type=Path, help="`mhfu observe path --csv` of the game: measured travel"
    )
    data.add_arguments(p)
    p.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    pac, names, authored = _open(args)
    entries = Pac.from_bytes(pac).entries
    skeleton = Skeleton.from_bytes(entries[SKELETON])
    anim = fu.Anim.from_bytes(entries[ANIMATION])
    if args.carry:
        anim = travel.carry(anim, skeleton)
    turns = travel.turns(anim, skeleton, authored)
    if args.against:
        return _against(anim, skeleton, args.against, names)
    by_name = {n: e for e, n in names.items()}
    for c in args.clips:
        if not c.isdigit() and c not in by_name:
            raise ValueError(f"no clip named {c}")
    wanted = [int(c) if c.isdigit() else by_name[c] for c in args.clips] or None
    rows = travel.of(anim, skeleton, wanted)
    if wanted is None and not args.all:
        rows = [t for t in rows if _moves(t, turns.get(t.entry))]
    k = args.scale
    print(
        f"root joint {travel.root(skeleton)}; units x{k:g}, z forward; seconds at {args.speed:g};"
        " degrees: the body's turn left in the clip, the turn carried out of it, the manifest's,"
        " what YAW turns"
    )
    print(
        f"{'entry':>5} {'clip':34} {'frames':>6} loop {'x':>6} {'z':>6} {'dist':>6}"
        f" {'drawn x':>7} {'drawn z':>7} {'body':>5} {'data':>5} {'given':>5} {'yaw':>5}"
        f" {'s':>5}"
    )
    for t in rows:
        loop = "yes" if t.loop else "no"
        turn = turns.get(t.entry)
        given = turn.authored if turn is not None else None
        print(
            f"{t.entry:5d} {names.get(t.entry, ''):34.34} {t.frames:6d} {loop:4}"
            f" {t.carried[0] * k:6.0f} {t.carried[1] * k:6.0f} {t.distance * k:6.0f}"
            f" {t.drawn[0] * k:7.0f} {t.drawn[1] * k:7.0f} {_deg(t.turn):5.0f}"
            f" {_deg(turn.data if turn else 0):5.0f} {'-' if given is None else f'{given:.0f}':>5}"
            f" {_deg(turn.total if turn else 0):5.0f} {t.seconds(args.speed):5.2f}"
        )
    lost = [t.entry for t in rows if MOVES <= math.hypot(*t.drawn) > t.distance]
    if lost:
        print(
            f"{len(lost)} entries keep travel on joint 0, which the engine draws and drops: "
            "rebuild with the travel carried (--carry previews it)"
        )
    return 0


def _deg(yaw: float) -> float:
    return yaw * 360 / travel.TURN


def _moves(t: travel.Travel, turn: travel.Turn | None) -> bool:
    turned = max(abs(_deg(t.turn)), abs(_deg(turn.total)) if turn else 0.0)
    return max(t.distance, abs(t.drawn[0]), abs(t.drawn[1])) >= MOVES or turned >= TURNS


def _against(anim: fu.Anim, skeleton: Skeleton, csv: Path, names: dict[int, str]) -> int:
    """Each clip played in the game against its root path, up to its first wall."""
    filled = set(motion.filled(anim))
    print(f"{'entry':>5} {'clip':34} {'from':>6} {'to':>6} {'game':>6} {'expect':>6} ratio  wall")
    for leg in observe.legs(observe.read_frames(csv)):
        hit = leg.wall
        fs = leg.frames if hit is None else leg.frames[: leg.frames.index(hit)]
        if len(fs) < 2 or leg.entry not in filled:
            continue
        want = sum(_step(anim, skeleton, leg.entry, f) for f in fs[:-1])
        got = observe.Leg(leg.entry, tuple(fs)).path
        ratio = f"{got / want:5.3f}" if want >= MOVES else "    -"
        wall = f"{hit.walls:#010x} class {2 if hit.stuck else 1} at {hit.clip:g}" if hit else "-"
        print(
            f"{leg.entry:5d} {names.get(leg.entry, ''):34.34} {fs[0].clip:6.1f} {fs[-1].clip:6.1f}"
            f" {got:6.0f} {want:6.0f} {ratio}  {wall}"
        )
    return 0


def _step(anim: fu.Anim, skeleton: Skeleton, entry: int, f: observe.Frame) -> float:
    """What the next frame adds to the position: the root's move over the speed's span up to
    the cursor, from 0 while the cursor is under the speed (ROOT_MOTION)."""
    lo = f.clip - f.speed
    a, b = travel.path(anim, skeleton, entry, [lo, f.clip] if lo >= 0 else [0.0, f.speed])
    return f.scale * float(np.hypot(*(b - a)))


def _open(args: argparse.Namespace) -> tuple[bytes, dict[int, str], dict[int, float]]:
    """The PAC, entry -> clip name and entry -> authored turn: a manifest is built, a PAC
    read."""
    if args.port.suffix != ".toml":
        return args.port.read_bytes(), {}, {}
    m = manifest.load(args.port)
    built = build(m, data.from_arguments(args))
    return built.pac, layout.names(m, built.layout), authored(m, built.layout)
