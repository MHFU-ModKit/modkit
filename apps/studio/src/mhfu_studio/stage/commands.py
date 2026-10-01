# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`studio map`: stage checks that need the collision, offline edits, and the live push."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mhfu_studio.cli import Groups

if TYPE_CHECKING:
    from mhfu.files import Extracted

    from .file import StageFile


def register(groups: Groups) -> None:
    """Adds this area's commands; nothing heavy may be imported at module level."""
    m = groups.map

    def command(name: str, help: str) -> argparse.ArgumentParser:
        c = m.add_parser(name, help=help)
        c.add_argument("--data", type=Path, help="extracted game (default: $MHFU_DATA)")
        return c

    c = command(
        "exits", "where each exit lands on its target's floor (`mhfu stage exits`: the rest)"
    )
    c.add_argument("stage", type=int, nargs="?", help="one stage's exits, each with its floor")
    c.set_defaults(run=exits)

    c = command("surfaces", "triangles per surface class, over every stage or one")
    c.add_argument("stage", type=int, nargs="?")
    c.set_defaults(run=surfaces)

    c = command("budget", "how much geometry each vertex group's primitives can be packed with")
    c.add_argument("stage", type=int)
    c.add_argument("--sub", type=int, choices=(0, 2), help="0 terrain, 2 props (default both)")
    c.set_defaults(run=budget)

    c = command("textures", "a stage's texture bank, and what wears each slot")
    c.add_argument("stage", type=int)
    c.add_argument("--export", type=Path, metavar="DIR", help="write every image as PNG here")
    c.set_defaults(run=textures)

    c = command("verify", "self-checks: collision planes, texture re-encode, in-place PMO layout")
    c.add_argument("stages", type=int, nargs="*", help="default: every stage")
    c.set_defaults(run=verify)

    c = command("edit", "an edit list applied offline: the bytes each half writes")
    c.add_argument("stage", type=int)
    c.add_argument("--ops", type=Path, required=True, help="the edit list (JSON)")
    c.add_argument("--out", type=Path, metavar="DIR", help="write stNNN_subK.bin and the plan")
    c.set_defaults(run=edit)

    c = command("push", "push an edit list into the running game (PPSSPP's debugger)")
    c.add_argument("--stage", type=int, required=True)
    c.add_argument("--ops", type=Path, required=True, help="the edit list (JSON)")
    c.add_argument("--port", type=int, help="debugger port (default: PPSSPP's own)")
    for half in ("mesh", "collision", "textures"):
        c.add_argument(f"--{half}", action="store_true", help=f"push the {half} (default: all)")
    c.add_argument(
        "--catch",
        type=float,
        help="seconds to watch for the area reload and re-apply the mesh on it (default: none"
        " in the village, 120 in a quest area); the game runs slower meanwhile",
    )
    c.add_argument(
        "--hold", type=float, default=0.0, help="seconds to re-apply what an area load undoes"
    )
    c.add_argument("--restore", action="store_true", help="put the file's bytes back instead")
    c.add_argument("--undo", type=Path, metavar="FILE", help="replay a collision undo file")
    c.add_argument("--scratch", type=lambda s: int(s, 0), help="free RAM for added collision")
    c.add_argument("--dry", action="store_true", help="plan it offline; touch no emulator")
    c.set_defaults(run=push)


def _game(args: argparse.Namespace) -> Extracted:
    from mhfu.files import Extracted

    return Extracted.find(args.data)


def exits(args: argparse.Namespace) -> int:
    from mhfu import files

    from .checks import landings

    stages = [args.stage] if args.stage is not None else list(files.STAGES)
    found = landings(_game(args), stages)
    for k in found:
        if args.stage is None and k.lands:
            continue
        x, y, z = k.dest
        floor = f"floor {k.floor:.0f}" if k.floor is not None else "-"
        note = f"   !! {k.fault}" if k.fault else ""
        print(
            f"st{k.stage:03d} -> st{k.target:03d}  land ({x:7.0f} {y:6.0f} {z:7.0f})  {floor}{note}"
        )
    yes = sum(k.lands for k in found)
    where = len({k.stage for k in found})
    print(
        f"{len(found)} exits over {where} stages: {yes} land on the target's floor,"
        f" {len(found) - yes} do not"
    )
    return 0


def surfaces(args: argparse.Namespace) -> int:
    from collections import Counter

    from mhp_formats.fu.stage import SURFACE_BITS

    from . import checks
    from .file import StageFile

    game = _game(args)

    def bits(mask: int | None) -> str:
        if mask is None:
            return "past the table"
        names = [SURFACE_BITS[b].split(":")[0] for b in sorted(SURFACE_BITS) if mask & b]
        unknown = mask & ~sum(SURFACE_BITS)
        return "+".join(names + ([f"0x{unknown:X}?"] if unknown else [])) or "plain"

    if args.stage is None:
        print("mask    triangles  stages  classes")
        for mask, c in sorted(
            checks.census(game).items(), key=lambda kv: (kv[0] is None, kv[0] or 0)
        ):
            name = "-" if mask is None else f"0x{mask:04X}"
            print(f"{name:<7} {c.triangles:10d} {len(c.stages):7d}  {bits(mask)}")
        return 0
    from mhfu import stage as S

    st = StageFile.read(game, args.stage).stage
    table = S.StageOverlay.read(game, args.stage).surface_table() or []
    ids = checks.surface_ids(st)
    print(f"st{args.stage:03d}: triangles per surface id (`mhfu stage surfaces` has the table)")
    for sid in sorted({s for _, s in ids}):
        mask = table[sid] if sid < len(table) else None
        per = ", ".join(f"chunk {c} {n}" for (c, s), n in sorted(ids.items()) if s == sid)
        print(f"  id {sid}  {bits(mask):<24} {per}")
    chunks = st.collision.chunks if st.collision else []
    mats = Counter(t.flags.material for h in chunks for t in h.tris)
    masks = Counter((c, t.flags.exclude) for c, h in enumerate(chunks) for t in h.tris)
    print("  materials:", ", ".join(f"{m}:{n}" for m, n in sorted(mats.items())))
    print("  query masks:", ", ".join(f"c{c} 0x{m:04X}:{n}" for (c, m), n in sorted(masks.items())))
    return 0


def budget(args: argparse.Namespace) -> int:
    from . import mesh
    from .file import MESHES, StageFile

    sf = StageFile.read(_game(args), args.stage)
    for sub in [args.sub] if args.sub is not None else list(MESHES):
        rows = mesh.budget(sf, sub)
        if not rows:
            continue
        print(f"{sf.label} sub {sub}: {len(rows)} vertex groups")
        print("  group  prims  slots  pack  free vertices")
        for g, prims, slots, tris, free in rows:
            print(f"  g{g:<5d} {prims:5d} {slots:6d} {tris:5d} {free:6d}")
        print(
            f"  total  pack {sum(r[3] for r in rows)} triangles, {sum(r[4] for r in rows)} vertices"
        )
    return 0


def textures(args: argparse.Namespace) -> int:
    from mhp_formats.tmh import Tmh

    from . import textures as T
    from .file import TEXTURES, StageFile

    sf = StageFile.read(_game(args), args.stage)
    images = Tmh.from_bytes(sf.entry(TEXTURES)).images
    use = T.usage(sf)
    off, size = sf.table[TEXTURES]
    print(f"{sf.label} bank: {size} bytes at PAC+0x{off:X}, {len(images)} images")
    print("  slot  size      mode  colours  triangles  groups")
    for k, img in enumerate(images):
        u = use.get(k, T.Usage(0, []))
        colours = str(img.clut.entries) if img.clut else "direct"
        worn = ", ".join(f"sub{s}.g{g}" for s, g in u.groups) or "-"
        print(
            f"  {k:<5d} {img.width:3d}x{img.height:<5d} {img.mode:<5d} {colours:<8}"
            f" {u.triangles:9d}  {worn}"
        )
    free = [k for k in range(len(images)) if k not in use]
    print(f"  worn by no group: {free or 'none'}")
    if args.export:
        for path in T.export(sf, args.export):
            print(f"  wrote {path}")
    return 0


def verify(args: argparse.Namespace) -> int:
    from mhfu import files

    from . import checks, textures

    game = _game(args)
    stages = args.stages or list(files.STAGES[1:])
    p = checks.planes(game, stages)
    print(f"collision planes: {p.stages} stages, {len(p.failing)} under {checks.PLANE_FLOOR:.0%}")
    for n, frac in p.failing:
        print(f"  st{n:03d} {frac:.3f}")
    t = textures.verify(game, stages)
    print(
        f"textures: {t.stages} banks, {t.images} images, {t.exact} re-encode byte for byte,"
        f" {t.pixels} pixel for pixel, {len(t.failed)} fail"
    )
    for n, k, why in t.failed[:10]:
        print(f"  st{n:03d} slot {k}: {why}")
    n, bad = checks.inplace(game, stages)
    print(f"meshes: {n} PMOs, {len(bad)} do not lay out over themselves unchanged")
    for name in bad:
        print(f"  {name}")
    return 1 if bad else 0


def _load(args: argparse.Namespace) -> tuple[StageFile, list[dict[str, Any]]]:
    from . import ops as O
    from .file import StageFile

    sf = StageFile.read(_game(args), args.stage)
    ops = O.load(args.ops)
    findings = O.check(ops, base_dir=args.ops.parent, where=sf.label)
    for f in findings:
        print(f)
    if any(f.level == "error" for f in findings):
        raise ValueError(f"{args.ops} has errors; nothing written")
    return sf, ops


def edit(args: argparse.Namespace) -> int:
    import json

    from . import collision, mesh
    from . import ops as O
    from . import textures as T

    sf, ops = _load(args)
    base = args.ops.parent
    out: dict[str, bytes] = {}
    worst = 0
    for sub, e in mesh.edits(sf, ops, base).items():
        print("\n".join(e.log))
        runs = e.runs()
        safe = "resident-safe" if e.safe else "NOT resident-safe"
        print(f"{sf.label} sub {sub}: {sum(n for _, n in runs)} bytes in {len(runs)} runs, {safe}")
        out[f"{sf.label}_sub{sub}.bin"] = e.data
        worst |= not e.safe
        for f in e.findings:
            print(f)
    plan = None
    if any(O.touches_collision(op) for op in ops):
        plan = collision.plan(sf, ops, base)
        print("\n".join(plan.log))
        cells = sum(len(c) for c in plan.cells.values())
        print(
            f"{sf.label} collision: {len(plan.moved)} moved, {len(plan.added)} added,"
            f" {len(plan.deleted)} deleted, {cells} cells relinked"
        )
        for f in plan.findings:
            print(f)
    if any(O.touches_textures(op) for op in ops):
        bank = T.build(sf, ops, base)
        print("\n".join(bank.log))
        out[f"{sf.label}_sub1.bin"] = bank.data
        for f in bank.findings:
            print(f)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        for name, data in out.items():
            (args.out / name).write_bytes(data)
            print(f"wrote {args.out / name}")
        if plan is not None:
            path = args.out / f"{sf.label}_collision.json"
            path.write_text(json.dumps(collision.serialise(plan), indent=1))
            print(f"wrote {path}")
    return int(worst)


def push(args: argparse.Namespace) -> int:
    from mhfu import addresses as a
    from mhfu.memory import Live
    from ppsspp_debug import Client

    from . import live

    sf, ops = _load(args)
    halves = {h: getattr(args, h) for h in ("mesh", "collision", "textures")}
    if not any(halves.values()):
        halves = dict.fromkeys(halves, True)
    p = live.prepare(sf, ops, args.ops.parent, scratch=args.scratch or a.STAGE_SCRATCH, **halves)
    for line in [*p.log, *map(str, p.findings), *live.describe(p)]:
        print(line)
    catch = args.catch
    if catch is None and not (args.undo or args.restore):
        catch = live.default_catch(p, print)
    if args.dry:
        return 0
    with Client.connect(port=args.port) as client:
        mem = Live(client)
        if args.undo:
            print(f"undo: {live.undo(mem, args.undo)} writes put back")
        elif args.restore:
            live.restore(mem, sf)
        else:
            undo = args.ops.parent / ".inject" / f"{sf.label}_collision_undo.json"
            live.run(client, mem, p, catch_for=catch or 0.0, hold_for=args.hold, undo_file=undo)
    return 0
