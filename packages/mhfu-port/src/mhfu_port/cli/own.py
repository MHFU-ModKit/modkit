# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`mhfu-port own`: the own moves a manifest's `[brain.pairs] ... = "own"` derives from the donor,
and what the derivation could not carry over."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from mhfu.em.script.check import Finding

from .. import data, manifest, moves, sequence
from ..brain import own
from . import finding_line

if TYPE_CHECKING:
    from . import Subparsers


def register(sub: Subparsers) -> None:
    p = sub.add_parser(
        "own", help="list the own moves a manifest's [brain.pairs] derive from the donor"
    )
    p.add_argument("manifest", type=Path)
    data.add_arguments(p)
    p.set_defaults(run=run)


def say(f: Finding) -> None:
    """A finding on stderr, for the commands that build with derived moves."""
    print(finding_line(f), file=sys.stderr)


def run(args: argparse.Namespace) -> int:
    m = manifest.load(args.manifest)
    if m.brain is None or not own.pairs(m.brain):
        raise ValueError(f"{args.manifest} has no [brain.pairs] entry that is {manifest.OWN!r}")
    games = data.from_arguments(args)
    out, findings = own.expand(m, games)
    slots = moves.slots(out)
    total = sum(mv.own for mv in out.moves.values())
    print(
        f"own {m.port.name}: MHP3rd {m.brain.donor}, {len(own.pairs(m.brain))} pairs, "
        f"{total} own moves of {manifest.OWN_MOVES}"
    )
    listed: dict[str, str] = {}  # each move listed so far, with the head that listed it
    for pair in own.pairs(m.brain):
        head = manifest.own_name(pair)
        print(f"{pair[0]},{pair[1]}", end="")
        if head in m.moves:
            print(f"  {head}: the manifest's own move")
            continue
        steps = sequence.chain(out, head)
        print(f"  {head}" + (f" + {len(steps) - 1} more" if len(steps) > 1 else ""))
        joined = False
        for name in steps:
            earlier = None if joined else listed.get(name)
            joined = joined or earlier is not None
            print(describe(out, name, slots.get(name), earlier))
            listed.setdefault(name, head)
    errors = [f for f in findings if f.level == "error"]
    for f in findings:
        print(finding_line(f))
    return 1 if errors else 0


def describe(m: manifest.Manifest, name: str, slot: int | None, shared: str | None = None) -> str:
    """One derived move: its slot, clip, frames, length, steer and motion by code, the pair it comes
    from, then its attacks. `shared` is the head of an earlier chain that already holds it."""
    mv = m.moves[name]
    clip = m.clips[mv.clip] if mv.clip is not None else None
    head = f"  {name} (slot {slot})  clip {clip.id if clip else mv.anim} {mv.clip}"
    if mv.start or mv.stop:
        head += f"  frames {mv.start}-{mv.stop or 'end'}"
    if mv.length is not None:
        head += f"  {mv.length} AI frames"
    s = mv.steer
    if s.turn == "hunter":
        window = "" if s.window is None else f" in frames {s.window[0]}-{s.window[1]}"
        head += f"  turns to the target at {s.rate or 'the host charge'}{window}"
    if s.turn == "fixed":
        head += f"  turns {s.angle:g} degrees" + (f" in frames {s.window}" if s.window else "")
    if mv.travel:
        x, z = mv.travel[-1]
        head += f"  travels {x:g}, {z:g} over {len(mv.travel)} samples"
    if mv.lunge is not None:
        head += f"  lunges {mv.lunge.base:g} to {mv.lunge.base + mv.lunge.range:g}"
    if clip is not None and clip.lift is not None:
        head += f"  rises {max(clip.lift, key=abs):+g}"
    if mv.label:
        head += f"  ({mv.label})"
    if shared is not None:
        head += f"  shared with {shared}"
    lines = [head]
    spare = {a.id for a in m.attacks}
    for a in mv.attacks:
        kind = " (spare)" if a.id in spare else ""
        lines.append(f"      {a.label} -> host record {a.id}{kind}, frames {a.frame}-{a.end}")
    return "\n".join(lines)
