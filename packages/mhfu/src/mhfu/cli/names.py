# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`mhfu names build|show|coverage`: names for EU's code from the MHP2G decomp."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import names, symbols
from ..files import Extracted
from ..names.decomp import Decomp
from .binary import _address

if TYPE_CHECKING:
    from . import Subparsers


def register(sub: Subparsers) -> None:
    p = sub.add_parser("names", help="names for EU's code from the MHP2G decomp")
    cmd = p.add_subparsers(dest="names_command", required=True, metavar="COMMAND")

    b = cmd.add_parser("build", help="match JP's code to EU's and write the names table")
    b.add_argument("--data", type=Path, help="extracted MHFU EU (default: $MHFU_DATA)")
    b.add_argument("--jp", type=Path, help="extracted MHP2G (default: $MHP2G_DATA)")
    b.add_argument("--decomp", type=Path, help="mhp2g-decomp checkout (default: $MHP2G_DECOMP)")
    b.add_argument(
        "-o", "--output", type=Path, help="default: $MHFU_NAMES, else in ~/.cache/modkit"
    )
    b.set_defaults(run=build)

    s = cmd.add_parser("show", help="what an address is called, or the names holding TEXT")
    s.add_argument("what", metavar="ADDR|TEXT")
    s.set_defaults(run=show)

    c = cmd.add_parser("coverage", help="what the table names, by module and of em calls")
    c.add_argument("--data", type=Path, help="extracted MHFU EU (default: $MHFU_DATA)")
    c.add_argument("--em", type=int, default=75, help="the species whose outbound calls count")
    c.set_defaults(run=coverage)


def build(args: argparse.Namespace) -> int:
    table = names.build(
        Extracted.find(args.data),
        Extracted.find(args.jp, env="MHP2G_DATA"),
        Decomp.find(args.decomp),
        lambda step: print(step, file=sys.stderr),
    )
    path = names.write(table, args.output)
    print(f"{len(table['names'])} names, {len(table['functions'])} functions -> {path}")
    return 0


def show(args: argparse.Namespace) -> int:
    table = _table()
    try:
        va = _address(args.what)
    except argparse.ArgumentTypeError:
        found = [
            (key, e)
            for kind in ("names", "data")
            for key, e in table.get(kind, {}).items()
            if args.what in e["name"] or args.what in e["symbol"]
        ]
        for key, e in sorted(found):
            print(f"{key}  {e['name']}  (JP {e['jp']} {e['module']}, {e.get('how', 'data')})")
        return 0 if found else 1
    key = f"0x{va:08X}"
    if own := symbols.own(va):
        print(f"{key}  {own}  (addresses.toml)")
    for kind in ("names", "data", "functions"):
        if e := table.get(kind, {}).get(key):
            print(f"{key}  {kind}: " + json.dumps(e))
            return 0
    return 0 if own else 1


def coverage(args: argparse.Namespace) -> int:
    table, game = _table(), Extracted.find(args.data)
    head = f"{'module':<10} {'JP fns':>7} {'EU fns':>7} {'matched':>8} {'named':>6} {'mapped':>7}"
    print(head)
    for module, c in table["coverage"].items():
        if module in names.MODULES:
            matched = sum(c["matched"].values())
            print(
                f"{module:<10} {c['jp_functions']:>7} {c['eu_functions']:>7} {matched:>8} "
                f"{c['named']:>6} {c['named_mapped']:>7}"
            )
    calls = table["coverage"]["calls"]
    print("call sites of same-code pairs: " + ", ".join(f"{v} {k}" for k, v in calls.items()))
    targets = names.outbound(game.em(args.em))
    sites = sum(targets.values())
    print(f"em{args.em:02d} outbound: {len(targets)} targets, {sites} call sites")
    known = {int(k, 16) for k in table["names"]}
    matched = {int(k, 16) for k in table["functions"]}
    rows: list[tuple[str, Callable[[int], object]]] = [
        ("addresses.toml", symbols.own),
        ("names table", known.__contains__),
        ("either", lambda va: symbols.own(va) or va in known),
        ("matched to JP", matched.__contains__),
    ]
    for label, has in rows:
        hit = [t for t in targets if has(t)]
        share = sum(targets[t] for t in hit) / sites if sites else 0.0
        print(f"  {label:<15} {len(hit):>4} targets {share:>6.1%} of sites")
    return 0


def _table() -> dict[str, Any]:
    path = symbols.names_path()
    if not path.exists():
        raise FileNotFoundError(f"no names table at {path}: run `mhfu names build`")
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data
