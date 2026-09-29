"""`mhfu effects`, `attacks`, `abi`, `census` and `intel`: what a big-monster overlay does, read
from its code, and how the engine enters it."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

from .. import hitbox
from ..em import abi, attacks, effects, intel
from ..em import census as cs
from ..files import EM_SPECIES, Extracted
from ..mips import Code
from ..overlay import Overlay

if TYPE_CHECKING:
    from . import Subparsers


def species(text: str) -> int:
    """`75`, `em75` or `em75.ovl`."""
    try:
        n = int(text.removesuffix(".ovl").removeprefix("em"))
    except ValueError:
        n = -1
    if n not in EM_SPECIES:
        raise argparse.ArgumentTypeError(f"{text}: not one of the em overlays {EM_SPECIES}")
    return n


def register(sub: Subparsers) -> None:
    data = argparse.ArgumentParser(add_help=False)
    data.add_argument("--data", type=Path, help="extracted game (default: $MHFU_DATA)")
    many = argparse.ArgumentParser(add_help=False, parents=[data])
    many.add_argument("species", nargs="*", type=species, help="em overlays (default: all)")

    p = sub.add_parser("effects", parents=[many], help="effect ids, bones and frames per handler")
    p.add_argument("--census", action="store_true", help="which species use which effect id")
    p.set_defaults(run=run_effects)

    p = sub.add_parser("attacks", parents=[many], help="the attack ids each handler spawns")
    p.add_argument("--census", action="store_true", help="one line per species: spawner, fit")
    p.set_defaults(run=run_attacks)

    p = sub.add_parser("abi", help="the engine-to-overlay interface: entity vtables")
    cmds = p.add_subparsers(dest="abi", required=True, metavar="WHAT")
    q = cmds.add_parser("inventory", parents=[data], help="every MWo3 overlay")
    q.add_argument("--em", action="store_true", help="only the em overlays")
    cmds.add_parser("vtables", parents=[data], help="the entity vtable of each species")
    cmds.add_parser("interface", parents=[data], help="slots every species overrides, or some")
    cmds.add_parser("factory", parents=[data], help="emId -> species overlay")
    q = cmds.add_parser("classes", parents=[data], help="vtables an overlay installs itself")
    q.add_argument("species", type=species)
    q = cmds.add_parser("callers", parents=[data], help="game_task's calls through a slot")
    q.add_argument("slot", type=int)
    q.add_argument("--limit", type=int, default=3, help="sites shown in context")
    p.set_defaults(run=run_abi)

    p = sub.add_parser("census", help="measured dwell per behaviour pair, from framework.log")
    p.add_argument("--log", type=Path, required=True)
    p.add_argument("--since", type=int, default=0, help="byte offset: the log spans many boots")
    p.add_argument("--min-samples", type=int, default=5)
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--state", action="append", default=[], help="also show MAIN,SUB")
    p.set_defaults(run=run_census)

    p = sub.add_parser("intel", parents=[many], help="one JSON per species: the join of it all")
    p.add_argument("--all", action="store_true", help="every em overlay (also the default)")
    p.add_argument("--out", type=Path, default=Path("species"), help="default: ./species")
    p.add_argument("--stdout", action="store_true", help="print the JSON instead")
    p.add_argument("--log", type=Path, help="framework.log to attach a census from")
    p.add_argument("--since", type=int, default=0, help="byte offset into the log")
    p.add_argument(
        "--census-species",
        type=species,
        help="the species the log watched; needed with more than one overlay",
    )
    p.add_argument("-q", "--quiet", action="store_true", help="no per-species summary")
    p.set_defaults(run=run_intel)


def _overlays(args: argparse.Namespace) -> list[tuple[int, Overlay, Code]]:
    game = Extracted.find(args.data)
    out = []
    for s in args.species or EM_SPECIES:
        ovl = game.em(s)
        out.append((s, ovl, Code(ovl, ovl.text)))
    return out


def _arg(value: int | None) -> str:
    return "?" if value is None else str(value)


def run_effects(args: argparse.Namespace) -> int:
    overlays = _overlays(args)
    found = {s: effects.spawns(code) for s, _, code in overlays}
    if args.census:
        print("per species")
        for s, sites in found.items():
            ids = sorted({x.id for x in sites if x.id is not None})
            print(f"em{s:02d} {len(ids):3d} id(s): " + " ".join(map(str, ids)))
        print("\nper effect id (shared ids are the portable vocabulary)")
        for eid, users in effects.census(found).items():
            use = " ".join(f"em{s:02d}:{n}" for s, n in sorted(users.items()))
            print(f"{eid:4d} {len(users):2d} species  {use}")
        return 0
    for s, _, code in overlays:
        sites = found[s]
        named = [x for x in sites if x.id is not None]
        wrappers = ", ".join(f"0x{w:08X}" for w in effects.wrappers(code)) or "none"
        print(
            f"em{s:02d}  {len(sites)} spawn site(s), {len(named)} with a literal id, "
            f"{len(sites) - len(named)} computed; wrappers {wrappers}"
        )
        by_fn: dict[int, list[effects.Spawn]] = defaultdict(list)
        for x in named:
            by_fn[x.fn].append(x)
        for fn, rows in sorted(by_fn.items()):
            seen = sorted({(r.id, _arg(r.bone), _arg(r.frame)) for r in rows})
            recipes = " ".join(f"{i}@b{b}" + ("" if f == "?" else f"@f{f}") for i, b, f in seen)
            print(f"  fn 0x{fn:08X} {len(rows):3d}  {recipes}")
        print()
    return 0


def run_attacks(args: argparse.Namespace) -> int:
    for s, ovl, code in _overlays(args):
        table = hitbox.primary_table(hitbox.tables(ovl))
        sp = attacks.spawner(code)
        records = "-" if table is None else str(len(table.attacks))
        if args.census:
            ids = [] if sp is None else sp.ids
            print(
                f"em{s:02d} {'-' if sp is None else f'0x{sp.fn:08X}':10s} "
                f"{0 if sp is None else len(sp.sites):3d} site(s) "
                f"{0 if sp is None else sp.literal:3d} literal  "
                f"ids {ids[0] if ids else '-'}..{ids[-1] if ids else '-'}  "
                f"records {records:>3s}  {attacks.fit(sp, table)}"
            )
            continue
        if sp is None:
            print(f"em{s:02d}  no attack spawner\n")
            continue
        print(
            f"em{s:02d}  spawner 0x{sp.fn:08X}: {len(sp.sites)} site(s), {sp.literal} "
            f"literal; table {records} records -> {attacks.fit(sp, table)}"
        )
        for x in attacks.extras(code, sp):
            print(f"  extra 0x{x.fn:08X}: {len(x.sites)} site(s), ids {x.ids or '-'}")
        for fn, ids in attacks.by_handler(code).items():
            print(f"  fn 0x{fn:08X}  " + "; ".join(_attack(table, i) for i in ids))
        print()
    return 0


def _attack(table: hitbox.AttackTable | None, i: int) -> str:
    if table is None or i >= len(table.attacks):
        return f"id {i}"
    rec, vol = table.attacks[i], table.volume_for(i)
    spheres = (
        ""
        if vol is None
        else ": "
        + ", ".join(f"b{v.bone} r{v.radius:g}" for v in vol.volumes[:4])
        + (", ..." if len(vol.volumes) > 4 else "")
    )
    return f"id {i} (power {rec.power}, set {rec.volume_set}{spheres})"


def run_abi(args: argparse.Namespace) -> int:
    game = Extracted.find(args.data)
    if args.abi == "inventory":
        return _inventory(game, args.em)
    engine = abi.Engine.load(game)
    if args.abi == "vtables":
        print(f"{'vtable':>10s} {'species':>7s} {'ptrs':>4s} {'score':>6s} {'2nd':>6s}   overrides")
        for s, o in engine.owners.items():
            over = [k for k, p in enumerate(o.vtable.slots) if engine.in_em(p)]
            print(
                f"0x{o.vtable.va:08X} {f'em{s:02d}':>7s} {o.pointers:4d} {o.score:6.0%} "
                f"{o.second:6.0%}   {','.join(map(str, over))}"
            )
    elif args.abi == "interface":
        slots = engine.interface()
        for kind in ("mandatory", "optional"):
            rows = [x for x in slots if x.kind == kind]
            print(f"{kind}: {len(rows)} slot(s)")
            for x in rows:
                base = ", ".join(f"0x{v:08X}" for v in x.inherited) or "-"
                who = ",".join(f"em{s:02d}" for s in x.overrides)
                tail = f"em75 0x{x.impl[75]:08X}" if kind == "mandatory" else f"{base}  {who}"
                print(
                    f"  {x.index:3d} vt+0x{abi.offset(x.index):03X} "
                    f"{len(x.overrides):2d}/{len(x.impl)}  {tail}"
                )
        print(f"base: {sum(x.kind == 'base' for x in slots)} slot(s) no species overrides")
    elif args.abi == "factory":
        by: dict[int, list[int]] = defaultdict(list)
        for em_id, s in engine.factory().items():
            by[s].append(em_id)
        print(f"{sum(map(len, by.values()))} emIds route to {len(by)} species overlays")
        for s, ids in sorted(by.items()):
            print(f"  em{s:02d} <- emId " + ", ".join(f"0x{e:02X}" for e in ids))
    elif args.abi == "classes":
        found = engine.classes(args.species)
        print(f"em{args.species:02d} installs {len(found)} vtable(s)")
        for va, sites in found.items():
            vt = engine.vtable(va)
            own = sum(engine.in_em(p) for p in vt.slots)
            print(
                f"  0x{va:08X} {len(vt.slots):3d} slot(s) {own:3d} in the overlay  "
                + ", ".join(f"0x{x:08X}" for x in sites[:3])
            )
    else:
        sites = engine.dispatches(args.slot)
        print(f"slot {args.slot} (vt+0x{abi.offset(args.slot):02X}): {len(sites)} call(s)")
        code = engine.task_code
        for site in sites[: args.limit]:
            print(f"  fn 0x{code.function(site).start:08X}")
            for ins in code.span(range(site - 12, site + 8)):
                print(f"    0x{ins.vram:08X}  {ins.disassemble()}")
    return 0


def _inventory(game: Extracted, em_only: bool) -> int:
    print(
        f"{'file':16s} {'id':>4s} {'load':>10s} {'text':>8s} {'data':>7s} {'bss':>7s} "
        f"{'ctors':>5s}  name"
    )
    for path in sorted((game.root / "data_files").glob("file_*.bin")):
        with path.open("rb") as f:
            head = f.read(0x40)
        if not Overlay.sniff(head):
            continue
        ovl = Overlay(head, path.name)
        if em_only and ovl.species is None:
            continue
        print(
            f"{path.name:16s} {ovl.id:4d} 0x{ovl.load:08X} {len(ovl.text):8d} "
            f"{len(ovl.initialised):7d} {len(ovl.bss):7d} {len(ovl.ctors) // 4:5d}  {ovl.name}"
        )
    return 0


def run_census(args: argparse.Namespace) -> int:
    c, why = cs.load(args.log, args.since)
    if c is None:
        raise FileNotFoundError(why)
    print(
        f"{'pair':8s} {'dwell':>6s} {'n':>4s} {'move':>6s}  verdict  "
        "(2 Hz ticks: dwell in ticks, move per tick x2 = units per second)"
    )
    rows = sorted(
        ((sum(v) / len(v), k) for k, v in c.dwell.items() if len(v) >= args.min_samples),
        reverse=True,
    )
    for _, pair in rows[: args.top]:
        print(_census_row(c, pair))
    for spec in args.state:
        main, sub = (int(x) for x in spec.split(","))
        print(_census_row(c, (main, sub)))
    return 0


def _census_row(c: cs.Census, pair: cs.Pair) -> str:
    m = cs.measured(c, pair)
    move = "?" if m["move_per_tick"] is None else f"{m['move_per_tick']:.0f}"
    dwell = f"{m['dwell_ticks']:.1f}" if m["entered"] else "-"
    return (
        f"{str(pair):8s} {dwell:>6s} {m['entered']:4d} {move:>6s}  "
        f"{cs.verdict(c, pair)}  a1 {m['a1']}"
    )


def run_intel(args: argparse.Namespace) -> int:
    todo = list(EM_SPECIES) if args.all or not args.species else args.species
    measured, reason = None, "no census log given (--log)"
    if args.log is not None:
        c, reason = cs.load(args.log, args.since)
        measured = None if c is None else intel.Measured(c, args.log, args.since)
    target = args.census_species
    if measured is not None and target is None:
        if len(todo) == 1:
            target = todo[0]
        else:
            reason = (
                f"a census was found in {args.log} but not attached: --census-species was not given"
            )
            measured = None
            print(f"mhfu intel: {reason}", file=sys.stderr)
    game = intel.Game(Extracted.find(args.data))
    mine = [measured if s == target else None for s in todo]
    why = [
        f"the census in {args.log} was taken from species {target}, not {s}"
        if measured is not None and m is None
        else reason
        for s, m in zip(todo, mine, strict=True)
    ]
    _ = game.bias  # read once, before each worker gets a copy of the game
    if len(todo) == 1:
        docs = [intel.build(game, todo[0], mine[0], why[0])]
    else:
        with ProcessPoolExecutor(min(len(todo), os.cpu_count() or 1)) as pool:
            docs = list(pool.map(intel.build, [game] * len(todo), todo, mine, why))
    for s, doc in zip(todo, docs, strict=True):
        text = json.dumps(doc, indent=1)
        if args.stdout:
            print(text)
            continue
        args.out.mkdir(parents=True, exist_ok=True)
        dest = args.out / f"em{s:02d}.json"
        dest.write_text(text + "\n", encoding="utf-8")
        if not args.quiet:
            print(intel.summarise(doc))
            print(f"  -> {dest} ({len(text) / 1024:.1f} KB)")
    return 0
