# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`mhfu-port brain`: an MHP3rd monster's AI script as text, or the check over every monster."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from mhfu.files import Extracted

from ..brain import p3

if TYPE_CHECKING:
    from . import Subparsers


def register(sub: Subparsers) -> None:
    p = sub.add_parser(
        "brain", help="an MHP3rd monster's AI script as text; --check tests them all"
    )
    p.add_argument(
        "id", type=int, nargs="?", help="MHP3rd monster id (23 Brute Tigrex, 40 Zinogre)"
    )
    p.add_argument("--slot", type=int, default=0, help="overlay slot: 0 for m0, 1 for m1, ...")
    p.add_argument("--routine", help="only this routine, by name (base_0, call_12, death_0)")
    p.add_argument("--raw", action="store_true", help="address and bytes before each line")
    p.add_argument(
        "--check",
        action="store_true",
        help="lift every id and slot (or ID's) and compare each routine with its bytes",
    )
    p.add_argument("--p3rd-data", type=Path, help="extracted MHP3rd (default: $MHP3RD_DATA)")
    p.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    extracted = Extracted.find(args.p3rd_data, env="MHP3RD_DATA")
    if args.check:
        return _check(p3.check(extracted, None if args.id is None else [args.id]))
    if args.id is None:
        raise ValueError("give a monster id, or --check")
    lines = p3.render(p3.lift(extracted, args.id, args.slot), args.routine, args.raw)
    try:
        print("\n".join(lines))
    except BrokenPipeError:
        # the reader quit (`| head`): point stdout at /dev/null so exit does not flush into the pipe
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    return 0


def _check(report: p3.Report) -> int:
    if report.skipped:
        print(f"no script: {' '.join(map(str, report.skipped))}")
    for slot, totals in sorted(report.slots.items()):
        _row(f"m{slot}", totals)
    _row("all", report.total)
    for line in report.quirks:
        print(f"known: {line} has no end")
    for line in report.problems:
        print(f"problem: {line}")
    for line in report.missing:
        print(f"missing: {line} should have no end")
    print("ok" if report.ok else "FAILED")
    return 0 if report.ok else 1


def _row(name: str, t: p3.Totals) -> None:
    print(
        f"{name:4} {t.routines:5} routines {t.instructions:7} instructions"
        f" {t.unknown} unknown {t.uncovered} uncovered {t.mismatched} mismatched"
    )
