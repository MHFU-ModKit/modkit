# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`verify`, `validate`, `pose`, `stretch`, `floor` and `fidelity`: offline checks of a built
port."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

from mhfu.files import monster_pac

from .. import build, constraints, data, fidelity, manifest, pose, verify
from ..motion import frames

if TYPE_CHECKING:
    from . import Subparsers


def register(sub: Subparsers) -> None:
    p = sub.add_parser("verify", help="the structural audit of a built port")
    p.add_argument("port", type=Path, help="a built model PAC")
    p.add_argument(
        "--manifest", type=Path, help="also check the donor's rig and skin and the host's tip"
    )
    p.add_argument("--host", type=int, metavar="SPECIES", help="check this host's tip")
    data.add_arguments(p)
    p.set_defaults(run=run_verify)

    p = sub.add_parser("validate", help="the engine's rules on any monster PAC")
    p.add_argument("pac", type=Path)
    p.add_argument("--template", type=int, metavar="SPECIES", help="keep this species' bone tree")
    data.add_arguments(p)
    p.set_defaults(run=run_validate)

    p = sub.add_parser("pose", help="the port's clips against the donor's, joint by joint")
    p.add_argument("port", type=Path)
    p.add_argument("--manifest", type=Path, required=True)
    data.add_arguments(p)
    p.set_defaults(run=run_pose)

    p = sub.add_parser("stretch", help="how far the port's clips pull its mesh apart")
    p.add_argument("port", type=Path)
    p.add_argument("--slot", type=int, help="every triangle at one frame of this slot")
    p.add_argument("--frame", type=int, help="with --slot; default: mid-clip")
    p.add_argument("--fork", action="store_true", help="only the tear across the body fork")
    p.add_argument("--top", type=int, default=15, help="rows to print (default: 15)")
    p.set_defaults(run=run_stretch)

    p = sub.add_parser("floor", help="where the rest pose puts the feet")
    p.add_argument("pacs", type=Path, nargs="*", help="built or native model PACs")
    p.add_argument("--host", type=int, metavar="SPECIES", help="measure this host's PAC first")
    data.add_arguments(p)
    p.set_defaults(run=run_floor)

    p = sub.add_parser("fidelity", help="whether the porter keeps the donor's skin")
    p.add_argument("manifest", type=Path)
    data.add_arguments(p)
    p.set_defaults(run=run_fidelity)


def run_verify(args: argparse.Namespace) -> int:
    port = verify.Port(args.port.read_bytes())
    m = manifest.load(args.manifest) if args.manifest else None
    frame = monster_pac(args.host) if args.host is not None else m.port.host_frame if m else None
    games = None if frame is None else data.from_arguments(args)
    host = None if games is None or frame is None else verify.Port(games.fu.read(frame))
    if m is not None and games is not None:
        d = build.donor(m, games)
        checks = verify.audit(port, d.skeleton, build.parts(d, m.build), host)
    else:
        checks = verify.audit(port, host=host)
    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"{'PASS' if c.ok else 'FAIL'}  {c.name:<{width}}  {c.detail}")
    failed = sum(not c.ok for c in checks)
    print(f"\n{len(checks)} checks, {failed} failed")
    return 1 if failed else 0


def run_validate(args: argparse.Namespace) -> int:
    port = verify.Port(args.pac.read_bytes())
    template = None
    if args.template is not None:
        fu = data.from_arguments(args).fu
        template = verify.Port(fu.read(monster_pac(args.template))).skeleton
    results = constraints.validate(port.model, port.skeleton, port.anim, template)
    for r in results:
        print(r)
    errors = sum(r.level == "error" for r in results)
    print(f"{errors} errors, {len(results) - errors} warnings")
    return 1 if errors else 0


def run_pose(args: argparse.Namespace) -> int:
    m = manifest.load(args.manifest)
    games = data.from_arguments(args)
    d = build.donor(m, games)
    placed = build.layout(m, d, build.host(m, games)).entries
    port = verify.Port(args.port.read_bytes())
    clips = {e: d.clips[cid] for e, cid in placed.items()}
    r = pose.compare(port, d.skeleton, clips, build.record_map(d, m.build))
    print(f"{r.matched} of {r.bones} donor bones placed (pad {r.pad}), {r.compared} compared")
    print(
        f"{'entry':>5} {'clip':>4} {'joints':>6} {'median':>8} {'p90':>8} {'worst':>8} {'lift':>7}"
    )
    for s in r.slots:
        print(
            f"{s.slot:>5} {placed[s.slot]:>4} {s.joints:>6} {s.median:>8.2f} {s.p90:>8.2f} "
            f"{s.worst:>8.2f} {s.lift:>7.1f}"
        )
    if r.absent:
        print(f"entries the port leaves empty: {' '.join(map(str, r.absent))}")
    if r.partial:
        shown = ", ".join(f"{s} ({a} of {b} joints)" for s, a, b in r.partial)
        print(f"filled in only some parts, not compared: {shown}")
    verdict = "plays its donor's moveset" if r.ok else "differs from its donor"
    print(
        f"{len(r.slots)} clips, worst {r.worst:.2f} units (tolerance {pose.TOLERANCE}): {verdict}"
    )
    return 0 if r.ok else 1


def run_stretch(args: argparse.Namespace) -> int:
    port = verify.Port(args.port.read_bytes())
    if args.fork:
        t = verify.tear(port)
        print("no edge crosses the body fork" if t is None else f"worst tear {t}")
        print(f"gate: {verify.TEAR_LIMIT:.0f} units")
        return 0
    if args.slot is not None:
        clip = port.clip(args.slot)
        if clip is None:
            raise ValueError(f"slot {args.slot} is empty in this port")
        frame = args.frame if args.frame is not None else frames(clip) // 2
        rows = verify.faces(port, args.slot, frame)
        print(f"slot {args.slot} frame {frame}: {len(rows)} triangles grow")
        print(
            f"grown >50: {sum(s.growth > 50 for s, _ in rows)}, "
            f">150: {sum(s.growth > 150 for s, _ in rows)}"
        )
        for s, ratio in rows[: args.top]:
            print(f"  +{s.growth:7.0f}  {ratio:5.1f}x  group {s.group:3d}  joints {_pair(s)}")
        return 0
    worst = verify.worst(port)
    print(f"{len(worst)} clip samples, worst {worst[0].growth:.0f} units" if worst else "no clips")
    for s in worst[: args.top]:
        print(
            f"  +{s.growth:7.0f}  slot {s.slot:3d} frame {s.frame:3d}  group {s.group:3d}  "
            f"joints {_pair(s)}"
        )
    return 0


def run_floor(args: argparse.Namespace) -> int:
    pacs: list[tuple[str, bytes]] = []
    if args.host is not None:
        fu = data.from_arguments(args).fu
        pacs.append((f"host {args.host}", fu.read(monster_pac(args.host))))
    pacs += [(p.name, p.read_bytes()) for p in args.pacs]
    if not pacs:
        raise ValueError("give PACs to measure, or --host")
    rows = [(name, pose.rest_floor(verify.Port(pac))) for name, pac in pacs]
    width = max(len(name) for name, _ in rows)
    print(f"{'':<{width}}  {'floor':>7}  {'vertex':>6}  joint")
    for name, f in rows:
        print(f"{name:<{width}}  {f.height:>7.1f}  {f.vertex:>6}  {f.joint}")
    if len(rows) > 1:
        (first, a), (last, b) = rows[0], rows[-1]
        print(f"\n{last} stands {b.height - a.height:+.1f} units against {first}")
    return 0


def run_fidelity(args: argparse.Namespace) -> int:
    r = fidelity.of_build(manifest.load(args.manifest), data.from_arguments(args))
    f = r.fidelity
    print(f"skin {r.skin}: {f.vertices} vertices, donor {f.expected}")
    print(f"on other joints than the donor's: {f.wrong_set}")
    print(
        f"weight error: worst {f.max_error:.6f}, mean {f.mean_error:.6f} "
        f"(one u8 step {fidelity.WEIGHT_STEP:.6f})"
    )
    print("keeps the donor's skin" if f.ok else "does not keep the donor's skin")
    return 0 if f.ok else 1


def _pair(s: verify.Stretch) -> str:
    return "-".join(map(str, s.joints))
