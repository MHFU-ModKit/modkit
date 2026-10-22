# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`mhfu-port splice`: a manifest's `[brain]` translated into its host's AI script, as findings,
source or a brain file."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

from mhfu import addresses
from mhfu.em.script import brainfile, source
from mhfu.em.script import vars as script_vars
from mhfu.em.script.budget import LIMIT
from mhfu.em.script.build import Built, lift_row
from mhfu.em.script.check import Finding

from .. import data, manifest, moves
from ..brain import p3, splice

if TYPE_CHECKING:
    from . import Subparsers


def register(sub: Subparsers) -> None:
    p = sub.add_parser(
        "splice", help="translate a manifest's [brain] into its host's AI script, and vet it"
    )
    p.add_argument("manifest", type=Path)
    p.add_argument(
        "--source", action="store_true", help="print the new and edited routines as MHFU source"
    )
    p.add_argument(
        "-o",
        "--out",
        type=Path,
        help="write the host row's edits as a brain file (for `mhfu brain push|deploy --doc`), "
        "with each own move's pair named; it holds code translated from the game: keep it in the "
        "workspace, never in a repo",
    )
    data.add_arguments(p)
    p.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    m = manifest.load(args.manifest)
    if m.brain is None:
        raise ValueError(f"{args.manifest} has no [brain] table")
    games = data.from_arguments(args)
    row = m.port.host_species
    host = lift_row(games.fu, row, row)
    survey = script_vars.survey(host, games.fu.em(row))
    donor = p3.lift(games.p3rd, m.brain.donor, m.brain.slot)
    spliced = splice.splice(donor, host, m.brain, survey, moves.slots(m))
    built = Built.of(host, spliced.edits)
    findings = [*spliced.findings, *built.findings]
    b = m.brain
    print(f"splice {m.port.name}: MHP3rd {b.donor} {b.entry} into em{row} {b.splice}")
    print(summary(spliced, built, findings))
    for f in findings:
        where = f" {f.item}" if f.item else ""
        print(f"{f.level:7} {f.code}{where}: {f.text}")
    ok = not any(f.level == "error" for f in findings)
    if args.source:
        print("\n".join(routines(built)))
    if args.out:
        if not ok:
            raise ValueError(f"the splice has errors, {args.out} is not written")
        bf = brainfile.new(row, m.port.name)
        bf.pairs |= {(addresses.OWN_MOVE_MAIN, k): name for name, k in spliced.slots.items()}
        bf.put(spliced.edits)
        print(f"wrote {brainfile.save(bf, args.out)}")
    return 0 if ok else 1


def summary(spliced: splice.Spliced, built: Built, findings: list[Finding]) -> str:
    """What the splice came to: routines and bytes added, the longest run, the pairs used."""
    added = f"{len(spliced.routines)} routines added"
    if built.image and (worst := built.budget.worst):
        grown = len(built.image.data) - (built.lift.end - built.lift.base)
        added += f", {grown:+} bytes, worst run {worst.count} of {LIMIT}"
    lines = [f"  {added}"]
    for (donor, target), uses in sorted(spliced.uses.items(), key=lambda use: use[0]):
        to = (
            f"own {target} (slot {spliced.slots[target]})"
            if isinstance(target, str)
            else f"{target[0]},{target[1]}"
        )
        lines.append(f"  pair {donor[0]},{donor[1]} -> {to}  x{uses}")
    codes = Counter(f.code for f in findings)
    lines.append(
        "  findings: " + (", ".join(f"{c} {n}" for c, n in sorted(codes.items())) or "none")
    )
    return "\n".join(lines)


def routines(built: Built) -> list[str]:
    """The source of the routines the edits changed or added."""
    touched = set(built.applied.touched)
    out: list[str] = []
    keep = False
    for line in source.render(built.program):
        if line.depth == 0:
            keep = line.text.startswith("routine ") and line.text.split()[1] in touched
            if keep and out:
                out.append("")
        if keep:
            out.append(line.text)
    return out
