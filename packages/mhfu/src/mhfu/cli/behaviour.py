"""`mhfu moveset`, `mhfu phases`, `mhfu chain`: what a big monster's overlay does per action,
what ends each action, and what comes after it."""

from __future__ import annotations

import argparse
import collections
from pathlib import Path
from typing import TYPE_CHECKING

from .. import files
from ..em import phases
from ..em.chain import Chain, Edge
from ..em.moveset import Moveset

if TYPE_CHECKING:
    from . import Subparsers


def register(sub: Subparsers) -> None:
    p = sub.add_parser("moveset", help="a big monster's (main, sub) pairs, handlers, animations")
    p.add_argument("species", type=int, help="overlay species (em number)")
    p.add_argument("--main", type=int, help="only this main state")
    p.add_argument("--data", type=Path, help="extracted game (default: $MHFU_DATA)")
    p.set_defaults(run=moveset)

    p = sub.add_parser("phases", help="what ends each of a big monster's actions")
    p.add_argument("species", type=int, help="overlay species (em number)")
    p.add_argument("--main", type=int, help="only this main state")
    p.add_argument("--budget", action="store_true", help="budget-gated actions: who owns it")
    p.add_argument("--data", type=Path, help="extracted game (default: $MHFU_DATA)")
    p.set_defaults(run=phases_)

    p = sub.add_parser("chain", help="which pair a big monster's action hands off to")
    p.add_argument("species", type=int, help="overlay species (em number)")
    p.add_argument("pair", type=int, nargs="*", metavar="MAIN SUB", help="one pair, every edge")
    p.add_argument("--as", dest="as_species", type=int, help="species byte (default: its own)")
    p.add_argument("--brain", action="store_true", help="enter calls outside the handlers")
    p.add_argument("--enter", action="store_true", help="enter(main, id) -> pair, per main")
    p.add_argument("--data", type=Path, help="extracted game (default: $MHFU_DATA)")
    p.set_defaults(run=chain)


def _moveset(args: argparse.Namespace) -> Moveset:
    if args.species not in files.EM_SPECIES:
        raise ValueError(f"no big-monster overlay for species {args.species}")
    return Moveset(files.Extracted.find(args.data).em(args.species))


def moveset(args: argparse.Namespace) -> int:
    ms = _moveset(args)
    if ms.tick is None:
        print(f"{ms.ovl.name}: no switch on the main state")
        return 1
    print(f"{ms.ovl.name}: action tick 0x{ms.tick_entry:08X}")
    for m, main in sorted(ms.mains.items()):
        if args.main is not None and m != args.main:
            continue
        subs = "no sub switch" if main.switch is None else f"{len(main.switch.targets)} subs"
        print(f"main {m}: 0x{main.dispatcher:08X}  {subs}")
        for (pm, sub), pair in sorted(ms.pairs.items()):
            if pm != m:
                continue
            if pair.handler is None:
                print(f"  ({m},{sub:3d})  inline at 0x{pair.case:08X}")
                continue
            anim = ms.animations(pair)
            ids = ",".join(map(str, anim.ids)) + (",computed" if anim.computed else "")
            given = " ".join(f"{k}={v}" for k, v in pair.args.items())
            print(f"  ({m},{sub:3d})  0x{pair.handler:08X}  anim {ids or '-'}  {given}".rstrip())
    return 0


def phases_(args: argparse.Namespace) -> int:
    ms = _moveset(args)
    counts: collections.Counter[str] = collections.Counter()
    for (m, sub), pair in sorted(ms.pairs.items()):
        if pair.handler is None or (args.main is not None and m != args.main):
            continue
        g = phases.gates(ms.code, pair.handler)
        counts[g.ends_on] += 1
        if args.budget:
            if g.ends_on == "budget":
                seeds = phases.budget_seeds(ms.code, pair.handler)
                owner = f"phase 0 seeds {list(seeds)}" if seeds else "a post-hook owns it"
                print(f"  ({m},{sub:3d})  0x{pair.handler:08X}  {owner}")
            continue
        frames = ",".join("?" if f is None else f"{f:g}" for f in g.reached) or "-"
        crosses = ",".join("?" if f is None else f"{f:g}" for f in g.crosses) or "-"
        print(
            f"  ({m},{sub:3d})  0x{pair.handler:08X}  {g.ends_on:11s}  reached {frames}"
            f"  crosses {crosses}  clip {g.clip_done}  budget {g.budget}"
        )
    if not args.budget:
        print("  " + ", ".join(f"{n} {k}" for k, n in counts.most_common()))
    return 0


def _edge(e: Edge) -> str:
    to = " ".join(f"({m},{s})" for m, s in e.to) or "(computed)"
    call = f"{e.kind}({e.main},{e.id},{e.mode})"
    via = " via " + ",".join(f"0x{v:08X}" for v in e.via) if e.via else ""
    guards = f"  [{' & '.join(e.guards)}]" if e.guards else ""
    if e.alts:
        guards += "  (or " + " / ".join(" & ".join(g) for g in e.alts) + ")"
    return f"{call} -> {to}{via}{guards}  @0x{e.site:08X}"


def chain(args: argparse.Namespace) -> int:
    ms = _moveset(args)
    ch = Chain(ms, args.as_species)
    if args.enter:
        for main, rows in sorted(ch.enter.table().items()):
            for n, to in sorted(rows.items()):
                print(f"  enter({main},{n:3d}) -> {' '.join(map(str, to)) or '(computed)'}")
        return 0
    if args.brain:
        for fn, edges in ch.brain.items():
            print(f"0x{fn:08X}: {len(edges)} edge(s)")
            for e in edges:
                print("    " + _edge(e))
        return 0
    if args.pair:
        if len(args.pair) != 2:
            raise ValueError("a pair is MAIN SUB")
        link = ch.pairs.get((args.pair[0], args.pair[1]))
        if link is None:
            print(f"({args.pair[0]},{args.pair[1]}) has no handler")
            return 1
        print(f"({args.pair[0]},{args.pair[1]}) handler 0x{link.handler:08X}")
        for e in link.next:
            print("    " + _edge(e))
        return 0
    handed = sum(1 for link in ch.pairs.values() if link.next)
    print(
        f"{ms.ovl.name}: {len(ch.pairs)} pairs with a handler, {handed} hand off; "
        f"enter-action {'-' if ch.enter.function is None else f'0x{ch.enter.function:08X}'}"
    )
    for (m, sub), link in sorted(ch.pairs.items()):
        if not link.next:
            continue
        succ = sorted({t for e in link.next for t in e.to})
        computed = sum(1 for e in link.next if e.computed)
        extra = f"  +{computed} computed" if computed else ""
        print(f"  ({m},{sub:3d}) -> {' '.join(f'({a},{b})' for a, b in succ) or '-'}{extra}")
    return 0
