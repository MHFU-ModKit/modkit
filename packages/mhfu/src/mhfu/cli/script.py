# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`mhfu script`: a big monster's AI script, disassembled from its overlay.

    mhfu script 75                  # every routine of map type 0's header
    mhfu script 75 --variant 5      # another map type's
    mhfu script 75 --routine ADDR   # one routine, flow-followed from its address
    mhfu script 75 --stats          # counts by kind and opcode, enter pairs, conditions
    mhfu script 75 --json           # the same as data
    mhfu script 75 --row 76         # a sibling species the overlay serves
    mhfu script --all               # one coverage line per big-monster overlay

The coverage line counts routines, instructions, opcodes the game has no case for and routines
that do not end cleanly; the exit status is 1 if any is not zero.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from ..em import script
from ..files import EM_SPECIES, Extracted
from .binary import _address
from .intel import species

if TYPE_CHECKING:
    from . import Subparsers


def register(sub: Subparsers) -> None:
    p = sub.add_parser("script", help="a big monster's AI script, disassembled")
    p.add_argument("species", nargs="?", type=species, help="em overlay (em75, 75)")
    p.add_argument("--variant", type=int, default=0, help="map type, 0 to 16 (default 0)")
    p.add_argument("--row", type=int, help="species row of a sibling the overlay serves (em75: 76)")
    p.add_argument("--routine", type=_address, metavar="ADDR", help="only this routine")
    p.add_argument("--stats", action="store_true", help="counts instead of the listing")
    p.add_argument("--json", action="store_true", help="data instead of text")
    p.add_argument("--all", action="store_true", help="one coverage line per overlay")
    p.add_argument("--data", type=Path, help="extracted game (default: $MHFU_DATA)")
    p.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    game = Extracted.find(args.data)
    if args.all:
        return coverage(game, args.json)
    if args.species is None:
        raise ValueError("name an em overlay (75, em75), or pass --all")
    sc = script.Scripts(game, args.species)
    lst = sc.disassemble(args.variant, args.row)
    only: list[int] | None = None
    if args.routine is not None:
        only = [args.routine]
        if args.routine not in lst.routines:
            lst = replace(lst, routines={args.routine: script.decode(sc.mem.u8, args.routine)})
    if args.stats:
        stats = lst.stats(only)
        print(json.dumps(stats.as_dict(), indent=1) if args.json else stats_text(stats))
    elif args.json:
        print(json.dumps(lst.to_dict(only), indent=1))
    else:
        print(lst.text(only))
    return 0


def stats_text(s: script.Stats) -> str:
    def counts(rows: dict[str, int]) -> str:
        return ", ".join(f"{k} {n}" for k, n in sorted(rows.items(), key=lambda kv: -kv[1]))

    ops = {script.OPCODES[c].name: n for c, n in s.by_op.items()}
    tests = {script.OPCODES[c].name: n for c, n in s.conditions.items()}
    pairs = " ".join(f"{m},{b}:{n}" for (m, b), n in s.enters.items())
    return "\n".join(
        [
            f"{s.routines} routines, {s.instructions} instructions, {s.unknown} unknown opcodes, "
            f"{s.unterminated} unterminated",
            f"kinds: {counts(dict(s.by_kind))}",
            f"opcodes: {counts(ops)}",
            f"conditions: {counts(tests)}",
            f"enter pairs (main,sub:uses): {pairs}",
        ]
    )


def coverage(game: Extracted, as_json: bool) -> int:
    rows = []
    for em in EM_SPECIES:
        sc = script.Scripts(game, em)
        listings = sc.listings()
        s = script.merge(listings.values())
        rows.append(
            {
                "species": em,
                "headers": len(listings),
                "routines": s.routines,
                "instructions": s.instructions,
                "unknown": s.unknown,
                "unterminated": s.unterminated,
                "unresolved": sum(len(x.unresolved) for x in listings.values()),
            }
        )
    if as_json:
        print(json.dumps(rows, indent=1))
    else:
        for r in rows:
            print(
                f"em{r['species']:02d}  headers {r['headers']}  routines {r['routines']:3d}  "
                f"instructions {r['instructions']:6d}  unknown {r['unknown']}  "
                f"unterminated {r['unterminated']}  unresolved calls {r['unresolved']}"
            )
    return int(any(r["unknown"] or r["unterminated"] for r in rows))
