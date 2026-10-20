# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`mhfu script`: a big monster's AI script, disassembled from its overlay.

    mhfu script 75                  # every routine of map type 0's header
    mhfu script 75 --variant 5      # another map type's
    mhfu script 75 --routine ADDR   # one routine, flow-followed from its address
    mhfu script 75 --stats          # counts by kind and opcode, enter pairs, conditions
    mhfu script 75 --json           # the same as data
    mhfu script 75 --row 76         # a sibling species the overlay serves
    mhfu script 75 --source -o F    # the species row as source text, to a file
    mhfu script 75 --check F        # F edited: parse, resolve and vet it, a line per finding
    mhfu script 75 --build F -o OUT # the same, then the image to OUT: --base ADDR, --map MAP
    mhfu script 75 --doc F          # a brain file's edits to the row: findings, id, worst run
    mhfu script 75 --doc F --source # the program they build, as source text
    mhfu script --all               # one coverage line per big-monster overlay

The coverage line counts routines, instructions, opcodes the game has no case for and routines
that do not end cleanly; the exit status is 1 if any is not zero, or for --check and --build if a
finding is an error, which also stops --build.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from ..em import script
from ..em.script import brainfile, build
from ..em.script.check import Finding
from ..em.script.parse import ParseError
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
    p.add_argument(
        "--source",
        action="store_true",
        help="the row as source text (a game listing: keep it out of repos)",
    )
    p.add_argument("--check", type=Path, metavar="FILE", help="vet the edited source FILE")
    p.add_argument("--build", type=Path, metavar="FILE", help="assemble the edited source to -o")
    p.add_argument("--doc", type=Path, metavar="FILE", help="vet the edits of the brain file FILE")
    p.add_argument("--base", type=_address, metavar="ADDR", help="--build: place it at ADDR")
    p.add_argument("--map", type=Path, metavar="MAP", help="--build: write where things landed")
    p.add_argument("-o", "--out", type=Path, metavar="FILE", help="write the output to FILE")
    p.add_argument("--all", action="store_true", help="one coverage line per overlay")
    p.add_argument("--data", type=Path, help="extracted game (default: $MHFU_DATA)")
    p.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    game = Extracted.find(args.data)
    if args.all:
        return coverage(game, args.json)
    if args.doc:
        return from_doc(game, args)
    if args.species is None:
        raise ValueError("name an em overlay (75, em75), or pass --all")
    sc = script.Scripts(game, args.species)
    if args.check or args.build:
        return edited(sc, args)
    if args.source:
        lines = script.source.render(script.lift.lift(sc, args.row))
        emit("\n".join(line.text for line in lines), args.out)
        return 0
    lst = sc.disassemble(args.variant, args.row)
    only: list[int] | None = None
    if args.routine is not None:
        only = [args.routine]
        if args.routine not in lst.routines:
            lst = replace(lst, routines={args.routine: script.decode(sc.read, args.routine)})
    if args.stats:
        stats = lst.stats(only)
        emit(json.dumps(stats.as_dict(), indent=1) if args.json else stats_text(stats), args.out)
    elif args.json:
        emit(json.dumps(lst.to_dict(only), indent=1), args.out)
    else:
        emit(lst.text(only), args.out)
    return 0


def edited(sc: script.Scripts, args: argparse.Namespace) -> int:
    """Parse the edited source against the row it came from, resolve and check it; `--build`
    then writes the image, unless a finding is an error."""
    if args.check and args.build:
        raise ValueError("--check and --build are exclusive: --build checks first")
    if args.build and args.out is None:
        raise ValueError("--build needs -o OUT")
    prog = vet(args.check or args.build, script.lift.lift(sc, args.row))
    if prog is None:
        return 1
    if args.build:
        img = script.asm.assemble(prog, args.base)
        args.out.write_bytes(img.data)
        if args.map is not None:
            args.map.write_text(json.dumps(image_map(img), indent=1) + "\n")
        print(f"{args.out}: {len(img.data)} bytes")
    return 0


def report(findings: Iterable[Finding]) -> bool:
    """A line printed per finding; whether none is an error."""
    ok = True
    for f in findings:
        print(f"{f.level} {f.code} {f.node or f.item or '-'}: {f.text}")
        ok &= f.level != "error"
    return ok


def vet(path: Path, like: script.ir.Program) -> script.ir.Program | None:
    """The edited source at `path` parsed against `like` and resolved, a line printed per
    finding; None when a finding is an error."""
    try:
        prog = script.edit.resolve(script.parse.parse(path.read_text(), like=like))
    except ParseError as e:
        raise ValueError(f"{path}: {e}") from e
    return prog if report(script.check.check(prog)) else None


def from_doc(game: Extracted, args: argparse.Namespace) -> int:
    """Build the row a brain file edits: the findings, then the container id and the worst run,
    or with `--source` the program as text."""
    bf = brainfile.load(args.doc)
    if args.species not in (None, bf.overlay):
        raise ValueError(f"{args.doc} is em{bf.overlay}'s, not em{args.species}'s")
    built = build.compile(bf, game, bf.overlay if args.row is None else args.row)
    if not report(built.findings):
        return 1
    if args.source:
        emit("\n".join(ln.text for ln in script.source.render(built.program)), args.out)
    else:
        worst = built.budget.worst
        run_text = "" if worst is None else f", worst run {worst.count} of {script.budget.LIMIT}"
        print(f"row {built.row} id {built.id:08X}{run_text}")
    return 0


def image_map(img: script.asm.Image) -> dict[str, object]:
    """Where an image put things, for a tool that patches a running game."""
    return {
        "base": img.base,
        "size": len(img.data),
        "relocs": img.relocs,
        "headers": {f"0x{va:08X}": at for va, at in img.headers.items()},
        "origin": {f"0x{pc:08X}": at for pc, at in img.origin.items()},
        "items": {name: list(span) for name, span in img.items.items()},
        "closes": img.closes,
    }


def emit(text: str, out: Path | None) -> None:
    if out is None:
        print(text)
    else:
        out.write_text(text + "\n")


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
