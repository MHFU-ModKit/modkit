"""`mhfu stage`: stage file ids, the map table, exits and surface tables offline; the gathering
spots and small-monster spawns of a running quest."""

from __future__ import annotations

import argparse
import math
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

from ppsspp_debug import Client

from .. import addresses as a
from .. import files
from .. import stage as S
from ..memory import Live, Memory
from ..structs import Entity

if TYPE_CHECKING:
    from . import Subparsers

TURN = 0x10000


def register(sub: Subparsers) -> None:
    p = sub.add_parser("stage", help="stages: file ids, maps, exits, surfaces; live spots, spawns")
    cmds = p.add_subparsers(dest="stage_command", required=True, metavar="COMMAND")

    def offline(name: str, help: str) -> argparse.ArgumentParser:
        c = cmds.add_parser(name, help=help)
        c.add_argument("--data", type=Path, help="extracted game (default: $MHFU_DATA)")
        return c

    offline("ids", help="every stage's overlay and PAC file ids").set_defaults(run=ids)
    offline("maps", help="the map table: the stages of each map").set_defaults(run=maps)
    c = offline("exits", help="area transitions: where each exit leads and lands")
    c.add_argument("stage", type=int, nargs="?")
    c.add_argument("--check", action="store_true", help="flag exits that lead nowhere sensible")
    c.set_defaults(run=exits)
    c = offline("surfaces", help="a stage overlay's surface table, or a census of all of them")
    c.add_argument("stage", type=int, nargs="?")
    c.set_defaults(run=surfaces)

    for name, fn, help in (
        ("spots", spots, "live: resident files and the gathering-spot table"),
        ("spawns", spawns, "live: the small-monster spawn table"),
    ):
        c = cmds.add_parser(name, help=help)
        c.add_argument("--port", type=int, help="debugger port (default: PPSSPP's own)")
        c.add_argument("--data", type=Path, help="extracted game, to name the areas")
        c.set_defaults(run=fn)


def ids(args: argparse.Namespace) -> int:
    game = files.Extracted.find(args.data)
    rows = defaultdict(list)
    for r, stages in enumerate(S.read_map_table(game)):
        for n in stages:
            rows[n].append(r)
    print("stage     overlay      name           pac         map rows")
    for n in files.STAGES:
        name = game.overlay(files.stage_overlay(n)).name
        pac = f"file_{files.stage_pac(n):05d}" if n else "-"
        where = " ".join(map(str, rows[n])) or "-"
        print(f"st{n:03d}     file_{files.stage_overlay(n):05d}  {name:<14} {pac:<11} {where}")
    for v in files.STAGE_VARIANTS:
        pac = f"file_{files.stage_variant_pac(v):05d}"
        print(f"{S.variant_name(v)}  -            -              {pac:<11} (lobby variant)")
    print(f"\n{len(files.STAGES)} overlays, {len(files.STAGE_PACS)} PACs")
    return 0


def maps(args: argparse.Namespace) -> int:
    for r, stages in enumerate(S.read_map_table(files.Extracted.find(args.data))):
        if stages:
            areas = " ".join(f"st{n:03d}" for n in stages)
            print(f"row {r:2d} n={len(stages):<2d} entry=st{stages[0]:03d}  areas: {areas}")
    return 0


def exits(args: argparse.Namespace) -> int:
    game = files.Extracted.find(args.data)
    table = S.read_map_table(game)
    links = {n: S.StageOverlay.read(game, n).exits() for n in files.STAGES}
    edges = {(n, e.target) for n, ex in links.items() for e in ex}
    shown = [args.stage] if args.stage is not None else list(files.STAGES)
    flagged = 0
    for n in shown:
        if not links.get(n):
            continue
        print(f"st{n:03d}  stage{n:03d}.ovl")
        for e in links[n]:
            note = _check(n, e.target, table) if args.check else ""
            flagged += bool(note)
            tx, ty, tz = e.trigger
            dx, dy, dz = e.dest
            print(
                f"   -> st{e.target:03d}{'*' if e.flag else ' '}  trigger ({tx:7.0f} {ty:6.0f}"
                f" {tz:7.0f}) r={e.radius:.0f} h={e.height:.0f}   land ({dx:7.0f} {dy:6.0f}"
                f" {dz:7.0f}) facing {e.yaw * 360 / TURN:.0f} deg{note}"
            )
    mine = sorted(e for e in edges if e[0] in shown)
    one_way = [e for e in mine if (e[1], e[0]) not in edges]
    if args.stage is None:
        print(
            f"\n{sum(len(links[n]) for n in shown)} exits over {len({s for s, _ in mine})} stages"
        )
    print("one-way (no exit back):", ", ".join(f"st{s:03d}->st{t:03d}" for s, t in one_way) or "-")
    if args.check:
        print(f"flagged: {flagged}")
    return 0


def _check(stage: int, target: int, table: list[tuple[int, ...]]) -> str:
    """Why an exit from `stage` to `target` leads nowhere sensible, or ""."""
    if target not in files.STAGES or not target:
        return "   !! no such stage"
    if not any(stage in row and target in row for row in table):
        return "   !! the target shares no map with this stage"
    return ""


def surfaces(args: argparse.Namespace) -> int:
    game = files.Extracted.find(args.data)
    if args.stage is not None:
        so = S.StageOverlay.read(game, args.stage)
        if not so.params:
            print(f"st{args.stage:03d}  {so.name} has no parameter object")
            return 1
        table = so.surface_table() or []
        print(
            f"st{args.stage:03d}  {so.name} parameter object 0x{so.params.base:08X},"
            f" surface table 0x{so.params.surfaces:08X}"
        )
        for sid, mask in enumerate(table):
            print(f"  id {sid}  mask 0x{mask:04X}")
        return 0
    census: dict[int, set[int]] = defaultdict(set)
    for n in files.STAGES:
        for mask in S.StageOverlay.read(game, n).surface_table() or []:
            census[mask].add(n)
    print("mask    stages")
    for mask in sorted(census):
        print(f"0x{mask:04X} {len(census[mask]):7d}")
    return 0


# --- live ---


def _connect(args: argparse.Namespace) -> Client:
    return Client.connect(port=args.port)


def _row_areas(args: argparse.Namespace, row: int) -> tuple[int, ...]:
    """The areas spot groups name: the map row without its base camp."""
    try:
        game = files.Extracted.find(args.data)
    except FileNotFoundError:
        return ()
    table = S.read_map_table(game)
    return table[row][1:] if 0 <= row < len(table) else ()


def _where(mem: Memory) -> tuple[int, int, tuple[float, float, float]]:
    """The stage, the map row and the hunter's position, printed."""
    mgr = S.map_manager(mem)
    stage, row = mgr.stage, mgr.row
    pos = Entity(mem, a.PLAYER_ENTITY).position
    x, y, z = pos
    print(f"stage st{stage:03d} (row {row}), hunter ({x:.0f}, {y:.0f}, {z:.0f})")
    return stage, row, pos


def spots(args: argparse.Namespace) -> int:
    with _connect(args) as client:
        mem = Live(client)
        stage, row, (hx, _, hz) = _where(mem)
        res = S.resident_files(mem)
        listing = ", ".join(f"{f}@{s.data:08X}" for f, s in sorted(res.items()))
        print(f"\nresident files ({len(res)}): {listing}")
        pac = res.get(files.stage_pac(stage)) if stage in files.STAGES[1:] else None
        print(f"  this stage's PAC is {f'at 0x{pac.data:08X}' if pac else 'NOT resident'}")
        here = mem.u32(a.GATHER_SPOT_CURRENT)
        table = S.find_spots(S.snapshot(mem))
    if not table:
        print("\nno gathering-spot table found")
        return 1
    areas = _row_areas(args, row)
    print(f"\ngathering spots: {len(table)} slots at 0x{table[0].base:08X}, 4 per area")
    print(f"  addr       id  area   tool uses     {'x':>9} {'y':>8} {'z':>9}  radius")
    for s in table:
        group = s.id // 4
        area = f"st{areas[group]:03d}" if group < len(areas) else f"grp {group}"
        if s.kind == "unused":
            print(f"  {s.base:08X} {s.id:3d}  {area:<6} (unused slot)")
            continue
        x, y, z = s.position
        d = math.hypot(x - hx, z - hz)
        mark = "   <- the hunter is here" if s.base == here else ""
        print(
            f"  {s.base:08X} {s.id:3d}  {area:<6} {s.tool:4d} {s.uses:2d}/{s.uses_m:<2d}  "
            f"{x:9.0f} {y:8.0f} {z:9.0f}  {s.radius:6.0f}  d={d:.0f}{mark}"
        )
    return 0


def spawns(args: argparse.Namespace) -> int:
    species = {
        int(v): v.name.removesuffix("_VTABLE").lower()
        for v in a.table().addresses.values()
        if v.type == "vtable"
    }
    with _connect(args) as client:
        mem = Live(client)
        _where(mem)
        registry = S.Registry(mem).slots
        found = S.find_spawns(S.snapshot(mem))
        if not found:
            print("no spawn table found")
            return 1
        print(f"small-monster spawns: {len(found)} records from 0x{found[0].base:08X}")
        print(f"  idx  addr      seed      {'x':>9} {'y':>8} {'z':>9}    hp  ent  live entity")
        for s in found:
            if not s.here:
                continue
            live = ""
            slot = s.entity + 1
            if s.entity != S.NO_ENTITY and slot < len(registry) and registry[slot]:
                e = Entity(mem, registry[slot])
                kind = species.get(e.vtable, f"vtable {e.vtable:08X}")
                ex, ey, ez = e.position
                live = f"{kind:<10} {e.base:08X} ({ex:.0f} {ey:.0f} {ez:.0f}) hp={e.hp}"
            x, y, z = s.position
            hp = "-" if s.hp == S.NO_HP else s.hp
            ent = "-" if s.entity == S.NO_ENTITY else s.entity
            print(
                f"  {s.index:3d} {s.base:08X}  {s.seed:08X}  {x:9.0f} {y:8.0f} {z:9.0f}"
                f"  {hp:>4}  {ent:>3}  {live}"
            )
    away = sum(1 for s in found if not s.here)
    print(f"\n{away} records belong to other areas")
    return 0
