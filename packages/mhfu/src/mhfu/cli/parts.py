"""`mhfu hitzones`, `mhfu hitboxes`: where a big monster is hit and where it hits, from its
overlay and game_task.ovl. `mhfu inject`: hand a finished file to the framework's live
injection."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

from .. import addresses as a
from .. import files, inject
from .. import hitbox as hb
from .. import hitzone as hz
from ..memory import Image, Space

if TYPE_CHECKING:
    from . import Subparsers


def register(sub: Subparsers) -> None:
    p = sub.add_parser("hitzones", help="a big monster's hit volumes and hitzone grids")
    p.add_argument("species", type=int, nargs="?", help="species id (default: a summary)")
    p.add_argument("--verify", action="store_true", help="check the shape on every overlay")
    p.add_argument("--data", type=Path, help="extracted game (default: $MHFU_DATA)")
    p.set_defaults(run=hitzones)

    p = sub.add_parser("hitboxes", help="a big monster's attack records and attack volumes")
    p.add_argument("species", type=int, nargs="?", help="overlay species (default: a summary)")
    p.add_argument("--verify", action="store_true", help="check the tables on every overlay")
    p.add_argument("--data", type=Path, help="extracted game (default: $MHFU_DATA)")
    p.set_defaults(run=hitboxes)

    p = sub.add_parser("inject", help="place a finished file for the framework's live injection")
    p.add_argument("file_id", type=int, help="extracted file id it replaces")
    p.add_argument("pac", type=Path, help="the finished file")
    p.add_argument("--relocate", action="store_true", help="it is larger than the original")
    p.add_argument("--dir", type=Path, help="inject directory (default: the memory stick's)")
    p.add_argument("--orig", type=Path, help="the pristine file (default: from the game)")
    p.add_argument("--data", type=Path, help="extracted game (default: $MHFU_DATA)")
    p.set_defaults(run=place)


def _roundtrip(v: hz.HitVolume) -> bool:
    """Every byte of the record is a known field: rewriting them all rebuilds it."""
    copy = hz.HitVolume(Image(bytes(hz.STRIDE), 0), 0)
    for name in ("bone", "shape", "row", "part", "flags", "radius", "offset", "far"):
        setattr(copy, name, getattr(v, name))
    return copy.raw == v.raw


def hitzones(args: argparse.Namespace) -> int:
    game = files.Extracted.find(args.data)
    if args.verify:
        return _verify_hitzones(game)
    if args.species is None:
        task = game.overlay(files.GAME_TASK)
        print("em   hurtbox sets  own set          recs  grids")
        for em in files.EM_SPECIES:
            ovl = game.em(em)
            own = hz.own_set(Space([task, ovl]), ovl, em)
            hzs = hz.species_hitzones(task, em)
            where = f"0x{own.va:08X}  {len(own.volumes):4d}" if own else "-" + " " * 15
            hurt = sum(s.kind == hz.HURTBOX for s in hz.find_sets(ovl))
            print(f"em{em:<3d} {hurt:4d}  {where}  {len(hzs.states) if hzs else 0}")
        return 0
    em = args.species if args.species in files.EM_SPECIES else hz.owner(game, args.species)
    ovl = game.em(em)
    mem = hz.space(game, em)
    owners = hz.species_sets(mem, ovl)
    own = hz.own_set(mem, ovl, args.species)
    print(f"species {args.species} in {ovl.name}; walks {f'0x{own.va:08X}' if own else '-'}")
    for s in hz.find_sets(ovl):
        if s.kind == hz.UNKNOWN:
            continue
        caps = sum(v.capsule for v in s.volumes)
        who = f"  <- species {owners[s.va]}" if s.va in owners else ""
        print(
            f"  0x{s.va:08X}  {s.kind:<8} {len(s.volumes):3d} recs ({caps} capsule)"
            f"  bones {len(s.bones):2d}  parts {s.parts}  rows {s.rows}{who}"
        )
    hzs = hz.species_hitzones(mem, args.species)
    if not hzs:
        print("no hitzone grids")
        return 0
    print(f"\n{len(hzs.states)} grid(s) at 0x{hzs.states_va:08X}")
    print("  row  " + " ".join(f"{c:>7}" for c in hz.COLUMNS))
    for i, grid in enumerate(hzs.states):
        print(f"  -- state {i}  (0x{grid.base:08X})")
        for r, values in enumerate(grid.rows):
            print(f"  {r:3d}  " + " ".join(f"{v:7d}" for v in values))
    return 0


def _verify_hitzones(game: files.Extracted) -> int:
    bad = 0
    task = game.overlay(files.GAME_TASK)
    for em in files.EM_SPECIES:
        ovl = game.em(em)
        sets = hz.find_sets(ovl)
        hurt = [s for s in sets if s.kind == hz.HURTBOX]
        shape = all(
            v.row <= hz.MAX_ROW and v.part & hz.PART_MASK == v.part for s in hurt for v in s.volumes
        )
        rebuilt = all(_roundtrip(v) for s in sets for v in s.volumes)
        own = hz.own_set(Space([task, ovl]), ovl, em)
        ok = bool(hurt) and shape and rebuilt and own is not None
        bad += not ok
        found = own is not None and own.va in {s.va for s in sets}
        print(
            f"  em{em:<3d} {len(hurt)} hurtbox set(s)  own "
            f"{f'0x{own.va:08X} {len(own.volumes):3d} recs' if own else 'NONE'}"
            f"{'' if found else ' (not found by shape)'}  {'ok' if ok else 'FAILED'}"
        )
    grids = hz.all_hitzones(task)
    blocks = [g for h in grids.values() for g in h.states]
    pads = all(not any(g.pad) for g in blocks)
    bad += not pads
    print(f"  {len(grids)} species, {len(blocks)} grids, pads {'zero' if pads else 'NOT ZERO'}")
    print("all checks passed" if not bad else f"{bad} problem(s)")
    return 1 if bad else 0


def hitboxes(args: argparse.Namespace) -> int:
    game = files.Extracted.find(args.data)
    if args.verify:
        return _verify_hitboxes(game)
    if args.species is None:
        print("em   tables  attacks  sets  volumes")
        for em in files.EM_SPECIES:
            ts = hb.tables(game.em(em))
            sets = [v for t in ts for v in t.volumes]
            print(
                f"em{em:<3d} {len(ts):5d}  {sum(len(t.attacks) for t in ts):7d}  {len(sets):4d}"
                f"  {sum(len(v.volumes) for v in sets):7d}"
            )
        return 0
    ovl = game.em(args.species)
    ts = hb.tables(ovl)
    if not ts:
        print(f"{ovl.name}: no attack table, the overlay never calls the setter")
        return 0
    primary = hb.primary_table(ts)
    for t in ts:
        vt = f"0x{t.volume_table:08X}" if t.volume_table else "-"
        print(
            f"handle 0x{t.handle:08X}  records 0x{t.records:08X} x{len(t.attacks)}"
            f"  volumes {vt} x{len(t.volumes)}{'  (primary)' if t is primary else ''}"
        )
        for atk in t.attacks:
            if not any(atk.raw):
                continue
            v = t.volume_for(atk.index)
            shapes = (
                ", ".join(
                    f"bone{x.bone}{'/cap' if x.capsule else ''} r={x.radius:g}" for x in v.volumes
                )
                if v
                else "(no set)"
            )
            print(
                f"  [{atk.index:3d}] power={atk.power:3d} vol={atk.volume_set:2d}"
                f" elem=0x{atk.element:02X} kind={atk.kind} ang={atk.angle:3d}"
                f" tag=0x{atk.tag:02X}  -> {shapes}"
            )
    return 0


def _verify_hitboxes(game: files.Extracted) -> int:
    bad = 0
    for em in files.EM_SPECIES:
        ovl = game.em(em)
        calls = list(hb.calls_to(ovl, ovl.text, a.ATTACK_TABLE_SETTER))
        ts = hb.tables(ovl)
        sets = [v for t in ts for v in t.volumes]
        rebuilt = all(_roundtrip(x) for v in sets for x in v.volumes)
        ok = rebuilt and (not calls or (bool(ts) and all(t.volumes for t in ts)))
        bad += not ok
        state = "ok" if ok else "FAILED"
        if not calls:
            state = "no setter call"
        print(
            f"  em{em:<3d} {len(ts)} table(s) {sum(len(t.attacks) for t in ts):4d} attacks"
            f" {len(sets):3d} sets  {state}"
        )
    print("all checks passed" if not bad else f"{bad} problem(s)")
    return 1 if bad else 0


def place(args: argparse.Namespace) -> int:
    data = args.pac.read_bytes()
    if args.orig:
        orig = args.orig.read_bytes()
    else:
        orig = files.Extracted.find(args.data).read(args.file_id)
    if not args.relocate and len(data) > len(orig):
        raise ValueError(
            f"{args.pac} is larger than file {args.file_id} ({len(data)} > {len(orig)} bytes):"
            " use --relocate"
        )
    write = inject.write_relocate_bytes if args.relocate else inject.write_inject_bytes
    path = write(data, args.file_id, args.dir, orig=orig)
    print(f"{path}  (+ {inject.inject_filename(args.file_id)}{inject.ORIG})")
    return 0
