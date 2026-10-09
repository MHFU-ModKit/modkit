# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The `studio port` commands (scene, clips, align, check, hit, push), `studio render monster`."""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING

from mhfu_port import data

from mhfu_studio.cli import Groups
from mhfu_studio.harness import flags
from mhfu_studio.shell import places

if TYPE_CHECKING:
    from mhfu.em.intel import SpeciesIntel
    from mhfu_port.data import Data
    from mhfu_port.manifest import Manifest

    from mhfu_studio.monster.inputs import Built
    from mhfu_studio.monster.runtime import Host


VIEWS = ("front", "back", "side", "other_side", "three", "top")
SHADINGS = ("textured", "flat", "vgroup")


def register(groups: Groups) -> None:
    """Adds this area's commands; nothing heavy may be imported at module level."""
    _render(groups)
    p = groups.port.add_parser("scene", help="what is in a monster PAC, a port or its donor")
    p.add_argument("target", type=Path, help="a model PAC, or a port manifest")
    p.add_argument("--pac", type=Path, help="the built PAC (default: build it in memory)")
    p.add_argument("--side", choices=("port", "source"), default="port")
    p.add_argument("--em-id", type=int, help="the donor's em id, for a bare MHP3rd PAC")
    p.add_argument("--clips", action="store_true", help="list every clip")
    p.add_argument("--groups", action="store_true", help="list every mesh group")
    p.add_argument("--slot", type=int, help="pose this clip and report what moved")
    p.add_argument("--frame", type=float, help="frame for --slot (default: mid-clip)")
    p.add_argument("--top", type=int, default=6)
    data.add_arguments(p)
    p.set_defaults(run=run_scene)

    p = groups.port.add_parser("clips", help="slot coverage and label health of a port")
    p.add_argument("manifest", type=Path)
    p.add_argument("--pac", type=Path, help="the built PAC (default: build it in memory)")
    p.add_argument("--slots", action="store_true", help="one line per slot")
    p.add_argument("--import-labels", type=Path, metavar="FILE", help="`N -> text` lines")
    p.add_argument("--labels-from", metavar="BUILD", help="the build those labels were made on")
    p.add_argument("--overwrite", action="store_true", help="replace labels already there")
    p.add_argument("--all-slots", action="store_true", help="also label non-CARRIED slots")
    p.add_argument("--write", action="store_true", help="save the manifest (default: report)")
    data.add_arguments(p)
    p.set_defaults(run=run_clips)

    p = groups.port.add_parser("align", help="the host action's frames against a port's clips")
    p.add_argument("manifest", type=Path)
    p.add_argument("--move", help="one move (default: all)")
    p.add_argument("--pair", metavar="M,S", help="a pair not bound yet")
    p.add_argument("--slot", type=int, help="with --pair: the clip slot to align")
    p.add_argument("--impact", type=float, help="with --pair: the clip's contact frame")
    p.add_argument("--pac", type=Path, help="a built PAC, for the clips' real lengths")
    p.add_argument("--species", type=int, help="read another overlay's pairs (not re-hosting)")
    _intel_arguments(p)
    data.add_arguments(p)
    p.set_defaults(run=run_align)

    p = groups.port.add_parser("check", help="check a port against its build and host intel")
    p.add_argument("manifest", type=Path, nargs="+")
    p.add_argument("--pac", type=Path, help="the built PAC (default: build it in memory)")
    p.add_argument("--no-pac", action="store_true", help="skip the build and its checks")
    p.add_argument("--strict", action="store_true", help="warnings fail too")
    _intel_arguments(p)
    data.add_arguments(p)
    p.set_defaults(run=run_check)

    p = groups.port.add_parser("hit", help="export a port's hit tables as its Lua module")
    p.add_argument("manifest", type=Path)
    p.add_argument("-o", "--out", type=Path, help="default: ./<name>_hit.lua")
    p.add_argument("--print", action="store_true", help="print the module instead")
    p.add_argument("--deploy", action="store_true", help="copy it to the memory stick's mods")
    p.add_argument("--library", type=Path, help="the mhfu_port.lua to sync (default: checkout)")
    p.add_argument("--capacity", type=int, help="host set record count (default: intel)")
    _intel_arguments(p)
    data.add_arguments(p)
    p.set_defaults(run=run_hit)

    p = groups.port.add_parser("push", help="write a port's hit tables into the running game")
    p.add_argument("manifest", type=Path)
    p.add_argument("--dry", action="store_true", help="list the writes, touch nothing")
    _intel_arguments(p)
    data.add_arguments(p)
    p.set_defaults(run=run_push)


def _render(groups: Groups) -> None:
    p = groups.render.add_parser(
        "monster", help="a monster PAC or a port, posed, to PNGs and statistics goldens"
    )
    p.add_argument("target", type=Path, help="a model PAC, or a port manifest")
    p.add_argument("--pac", type=Path, help="the built PAC (default: build it in memory)")
    p.add_argument("--side", choices=("port", "source"), default="port")
    p.add_argument("--em-id", type=int, help="the donor's em id, for a bare MHP3rd PAC")
    p.add_argument("--clip", help="a clip name or slot (default: the viewport's opening pose)")
    p.add_argument("--slot", type=int, help="the clip in this slot")
    p.add_argument("--frames", type=_floats, metavar="F,F,..", help="default: mid-clip")
    p.add_argument("--frame", type=float, help="one frame")
    p.add_argument("--bind", action="store_true", help="the bind pose: it cannot show skinning")
    p.add_argument("--view", type=_views, default=("three",), metavar="V,V,..")
    p.add_argument("--shading", choices=SHADINGS, default="textured")
    p.add_argument("--wireframe", action="store_true")
    p.add_argument("--in-place", action="store_true", help="hold the clip's travel at frame 0")
    p.add_argument("--no-bones", action="store_true", help="hide the joint overlay")
    p.add_argument("--no-grid", action="store_true", help="hide the ground plane")
    p.add_argument("--hilite", type=_ints, default=(), metavar="J,J,..", help="paint joints red")
    p.add_argument("--only", choices=("tagged", "rest"), help="draw only --hilite, or the rest")
    p.add_argument("--severed", action="store_true", help="the stump: the tail tip hidden")
    data.add_arguments(p)
    flags.add_flags(p)
    p.set_defaults(run=run_render)


def _floats(text: str) -> tuple[float, ...]:
    return tuple(float(v) for v in text.split(",") if v.strip())


def _ints(text: str) -> tuple[int, ...]:
    return tuple(int(v) for v in text.split(",") if v.strip())


def _views(text: str) -> tuple[str, ...]:
    got = tuple(v.strip() for v in text.split(",") if v.strip())
    bad = [v for v in got if v not in VIEWS]
    if bad or not got:
        raise argparse.ArgumentTypeError(f"views are {', '.join(VIEWS)}; got {text!r}")
    return got


def run_render(args: argparse.Namespace) -> int:
    from mhfu_studio.monster.core.scene import Scene
    from mhfu_studio.monster.render import shots

    flags.check_args(args)
    if args.clip is not None and args.slot is not None:
        raise ValueError("--clip and --slot both name the clip: give one")
    if args.frames is not None and args.frame is not None:
        raise ValueError("--frames and --frame: give one")
    clip: int | str | None = args.slot if args.slot is not None else args.clip
    if isinstance(clip, str) and clip.isdigit():
        clip = int(clip)
    frames = (args.frame,) if args.frame is not None else args.frames
    if args.target.suffix == ".toml":
        games = None if args.pac and args.side == "port" else _games(args)
        sc = Scene.from_manifest(_manifest(args.target), args.pac, args.side, games)
    else:
        sc = Scene.from_path(args.target, args.em_id)
    o = shots.Options(
        views=args.view,
        clip=clip,
        frames=frames,
        bind=args.bind,
        shading=args.shading,
        wireframe=args.wireframe,
        in_place=args.in_place,
        bones=not args.no_bones,
        grid=not args.no_grid,
        hilite=args.hilite,
        only=args.only,
        severed=args.severed,
    )
    return flags.finish(args, shots.render(sc, o, args.size, args.samples))


def _intel_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--intel", type=Path, metavar="DIR", help="species intel (default: cache)")


def _intel(args: argparse.Namespace, species: int) -> SpeciesIntel | None:
    from mhfu_studio.monster import species as sp

    return sp.find(species, args.intel, args.data)


def _games(args: argparse.Namespace) -> Data:
    """--data and --p3rd-data over the places."""
    return places.games(args.data, args.p3rd_data)


def _manifest(path: Path) -> Manifest:
    from mhfu_port import manifest

    return manifest.load(path)


def _sources(args: argparse.Namespace, m: Manifest) -> dict[int, int]:
    """The manifest's layout, entry -> MHP3rd id; its pins alone without the games."""
    from mhfu_studio.monster.inputs import placed

    try:
        games = _games(args)
    except FileNotFoundError:
        games = None
    return placed(m, games).entries


def _built(args: argparse.Namespace, m: Manifest) -> Built:
    from mhfu_studio.monster.inputs import built

    return built(m, args.pac, None if args.pac else _games(args))


def run_scene(args: argparse.Namespace) -> int:
    import numpy as np

    from mhfu_studio.monster.core.scene import Scene

    if args.target.suffix == ".toml":
        games = None if args.pac and args.side == "port" else _games(args)
        sc = Scene.from_manifest(_manifest(args.target), args.pac, args.side, games)
    else:
        sc = Scene.from_path(args.target, args.em_id)
    print(sc.summary())
    if args.groups:
        print("\n  idx  verts  tris  tex  infl")
        for g in sc.groups:
            tex = "-" if g.texture is None else g.texture
            print(
                f"  {g.index:4d} {g.n_vertices:6d} {g.n_faces:5d} {tex!s:>4} "
                f"{g.skin.joints.shape[1]:5d}"
            )
    if args.clips:
        print("\n  slot  frames  loop  tracks  driven  name")
        for c in sc.clips:
            partial = "" if c.whole_rig else "   [partial]"
            print(
                f"  {c.slot:4d} {c.frames:7d} {c.loop!s:>5} {c.tracks:7d} {len(c.driven):7d}  "
                f"{c.name}{partial}"
            )
    if args.slot is not None:
        c = sc.clip(args.slot)
        at = c.frames / 2 if args.frame is None else args.frame
        p = sc.pose(c, at)
        moved = np.linalg.norm(p.joints - sc.rig.bind_joints, axis=1)
        far = np.argsort(-moved)[: args.top]
        print(f"\nclip {c.name}  frame {at:g}/{c.frames}  ({len(c.driven)} driven joints)")
        print(
            f"  joints displaced from bind: {int((moved > 1e-6).sum())} of {len(moved)}, "
            f"max {moved.max():.1f} u"
        )
        print("  furthest: " + ", ".join(f"j{j} {moved[j]:+.0f}u" for j in far))
        d = np.linalg.norm(p.skin(sc.merged) - sc.merged.positions, axis=1)
        print(f"  vertices displaced: {int((d > 1e-6).sum())} of {len(d)}, max {d.max():.1f} u")
    return 0


def run_clips(args: argparse.Namespace) -> int:
    from mhfu_port import layout, slots

    from mhfu_studio.monster import clips, inputs
    from mhfu_studio.monster.document import PortDocument

    doc = PortDocument.open(args.manifest)
    m = doc.manifest
    games = _games(args)
    b = _built(args, m)
    port = slots.anim_of(b.pac)
    table = clips.clip_table(port)
    host, donor = inputs.host_anim(m, games), inputs.donor_clips(m, games)
    cov = clips.coverage(port, host, donor, layout.of(m, donor, host).entries)
    if args.import_labels:
        labels = slots.read_labels(args.import_labels.read_text(encoding="utf-8"))
        provenance = args.labels_from or clips.UNRECORDED.format(args.import_labels.name)
        only = None if args.all_slots else cov
        done = clips.import_labels(
            doc, labels, table, provenance, only, args.overwrite, cov.sources()
        )
        print(
            f"{args.import_labels}: {len(labels)} label(s), {len(done)} land on a slot this "
            f"manifest does not name yet\n  recorded as labelled against: {provenance}"
        )
        if args.write and done:
            print(f"wrote {doc.save()}")
        elif done:
            import difflib

            from mhfu_port.manifest import dumps

            before = dumps(doc.saved_manifest).splitlines(keepends=True)
            after = dumps(doc.manifest).splitlines(keepends=True)
            name = str(args.manifest)
            print("".join(difflib.unified_diff(before, after, name, f"{name} (proposed)")))
            print("not written; pass --write")
        return 0
    if args.slots:
        for slot in sorted(table):
            c = cov.slots[slot]
            got = cov.sources()
            named = ",".join(n for n, cl in m.clips.items() if clips.at(cl, got) == slot) or "-"
            print(f"a1 {slot:<4d} {c.kind:<8} {named:<14} {c.why()}")
        return 0
    print(clips.report(m.port.name, clips.survey(m, table, cov, b.id)))
    return 0


def run_align(args: argparse.Namespace) -> int:
    from mhfu_studio.monster import align, clips

    m = _manifest(args.manifest)
    intel = _intel(args, args.species if args.species is not None else m.port.host_species)
    ends = None
    if args.pac:
        ends = {s: fp[0] for s, fp in clips.pac_clip_table(args.pac.read_bytes()).items()}
    sources = _sources(args, m)
    if args.pair:
        main, sub = (int(x) for x in args.pair.split(","))
        found = clips.entry(m, args.slot, sources) if args.slot is not None else None
        impact = (
            args.impact if args.impact is not None else found[1].impact_frame if found else None
        )
        a = align.align_pair(
            m,
            main,
            sub,
            intel,
            clip=found[0] if found else None,
            slot=args.slot,
            clip_frames=None if ends is None or args.slot is None else ends.get(args.slot),
            impact=impact,
        )
        print(a.report())
        return 0
    if not m.moves:
        print(f"{args.manifest} declares no moves yet; try --pair M,S --slot N")
        return 0
    for name in [args.move] if args.move else sorted(m.moves):
        c = m.clips.get(m.moves[name].clip or "")
        at = None if c is None else clips.at(c, sources)
        end = None if ends is None or at is None else ends.get(at)
        ids = {cid: e for e, cid in sources.items()}
        print(align.align(m, name, intel, end, ids=ids).report() + "\n")
    return 0


def run_check(args: argparse.Namespace) -> int:
    from mhfu_studio.monster import validate
    from mhfu_studio.shell.findings import worst

    if args.pac and len(args.manifest) > 1:
        raise ValueError("--pac names one built PAC: check one manifest at a time")
    status = 0
    for path in args.manifest:
        m = _manifest(path)
        b = None if args.no_pac else _built(args, m)
        pac, sources = None, {}
        if b is not None:
            pac = b.pac
            sources = b.layout.entries if b.layout is not None else _sources(args, m)
        found = validate.validate(m, pac, _intel(args, m.port.host_species), sources)
        print(f"== {path} ({m.port.name} on em{m.port.host_species:02d})")
        print(validate.report(found) + "\n")
        level = worst(found)
        if level == "error" or (args.strict and level == "warning"):
            status = 1
    return status


def _host(args: argparse.Namespace, m: Manifest) -> Host | None:
    from mhfu_studio.monster import runtime

    host = runtime.host(_intel(args, m.port.host_species))
    if host is not None and getattr(args, "capacity", None) is not None:
        host = dataclasses.replace(host, capacity=args.capacity)
    return host


def run_hit(args: argparse.Namespace) -> int:
    from mhfu_studio.monster import runtime

    m = _manifest(args.manifest)
    host = _host(args, m)
    if args.print:
        print(runtime.lua_hit_module(m, host), end="")
        return 0
    path = args.out or Path(runtime.module_name(m))
    dep = runtime.ship(m, path, host, library_path=args.library) if args.deploy else None
    if dep is None:
        runtime.export(m, path, host)
    print(
        f"wrote {path} ({len(m.hurtboxes)} volume(s), {len(m.hitzones)} state(s), "
        f"{len(runtime.sets_of(m))} attack set(s), {len(m.attacks)} attack record(s), "
        f"id {runtime.content_id(m)})"
    )
    for note in runtime.plan(m, host).notes:
        print(note)
    if dep is not None:
        print(f"deployed {dep.describe()}")
    return 0


def run_push(args: argparse.Namespace) -> int:
    from mhfu_studio.monster import push, runtime

    m = _manifest(args.manifest)
    host = _host(args, m)
    if args.dry:
        for w in runtime.plan(m, host).writes:
            print(f"0x{w.at:08X} {len(w.data):5d} B  {w.what}  ({len(w.guards)} guard(s))")
        return 0
    try:
        print(push.to_game(m, host).describe())
    except push.Refused as e:
        print(f"refused: {e}")
        return 1
    return 0
