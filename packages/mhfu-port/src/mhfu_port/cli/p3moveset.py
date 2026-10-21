# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`p3moveset`: an MHP3rd em overlay's (main, sub) pairs and the clips they play."""

from __future__ import annotations

import argparse
import csv
import sys
from collections.abc import Iterable
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from mhfu.files import Extracted
from mhfu.overlay import Overlay

from ..p3moveset import Moveset, Pair, Pairs

if TYPE_CHECKING:
    from . import Subparsers

COLUMNS = ("main", "sub", "kind", "handler", "clips", "computed", "next", "attacks", "rewrites")


def register(sub: Subparsers) -> None:
    p = sub.add_parser("p3moveset", help="an MHP3rd em overlay's (main, sub) pairs, as CSV")
    p.add_argument(
        "file", type=int, nargs="?", help="extracted file id of the overlay (em058m0: 5246)"
    )
    p.add_argument("--overlay", type=Path, help="an overlay file, instead of a file id")
    p.add_argument("--p3rd-data", type=Path, help="extracted MHP3rd (default: $MHP3RD_DATA)")
    p.add_argument("-o", "--out", type=Path, help="CSV file (default: stdout)")
    p.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    ms = Moveset(_overlay(args))
    if not ms.pairs:
        raise ValueError(f"{ms.ovl.name} has no action tick: is it an MHP3rd em overlay?")
    with args.out.open("w", newline="") if args.out else nullcontext(sys.stdout) as out:
        write(ms, out)
    pairs = ms.pairs.values()
    print(
        f"{len(pairs)} pairs, {sum(bool(p.clips) for p in pairs)} play clips, "
        f"{sum(p.computed for p in pairs)} with an id this walk cannot resolve; "
        f"{len(ms.sites) - len(ms.orphans)} of {len(ms.sites)} play calls reached, "
        f"{len(ms.translator)} pairs the translator rewrites",
        file=sys.stderr,
    )
    return 0


def _overlay(args: argparse.Namespace) -> Overlay:
    if (args.file is None) == (args.overlay is None):
        raise ValueError("give an extracted file id or --overlay FILE")
    if args.overlay is not None:
        return Overlay.from_path(args.overlay)
    return Extracted.find(args.p3rd_data, env="MHP3RD_DATA").overlay(args.file)


def write(ms: Moveset, out: TextIO) -> None:
    """One row per pair: the clips and the pairs entered, space-separated."""
    w = csv.writer(out, lineterminator="\n")
    w.writerow(COLUMNS)
    for p in ms.pairs.values():
        w.writerow(_row(p, ms.translator.get((p.main, p.sub), ())))


def _row(p: Pair, rewrites: Iterable[Pairs]) -> tuple[object, ...]:
    return (
        p.main,
        p.sub,
        p.kind,
        "" if p.handler is None else f"0x{p.handler:08X}",
        " ".join(map(str, p.clips)),
        "yes" if p.computed else "",
        _pairs(p.next),
        " ".join(":".join("?" if v is None else str(v) for v in a) for a in p.attacks),
        _pairs(sorted(rewrites)),
    )


def _pairs(pairs: Iterable[Pairs]) -> str:
    return " ".join(f"{m}:{s}" for m, s in pairs)
