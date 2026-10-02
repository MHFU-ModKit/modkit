# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`studio map inject` (a map document into the running game) and `studio render map`."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

from mhfu_studio.cli import Groups
from mhfu_studio.harness import flags

if TYPE_CHECKING:
    from mhfu.files import Extracted

    from .core.scene import MapScene

VIEWS = ("iso", "top", "front", "back", "side", "low", "hunter")
HALVES = ("mesh", "collision", "textures")


def register(groups: Groups) -> None:
    """Adds this area's commands; nothing heavy may be imported at module level."""
    c = groups.map.add_parser(
        "inject", help="push a map document's edits into the running game (PPSSPP's debugger)"
    )
    c.add_argument("document", type=Path, help="the document's folder, or its map.toml")
    c.add_argument("--stage", type=int, help="one stage of the document (default: every one)")
    c.add_argument("--data", type=Path, help="extracted game (default: $MHFU_DATA)")
    c.add_argument("--port", type=int, help="debugger port (default: PPSSPP's own)")
    for half in HALVES:
        c.add_argument(f"--{half}", action="store_true", help=f"push the {half} (default: all)")
    c.add_argument("--restore", action="store_true", help="put the file's bytes back instead")
    c.add_argument(
        "--catch",
        type=float,
        help="seconds to watch for the area reload and re-apply the mesh on it (default: none"
        " in the village, 120 in a quest area); the game runs slower meanwhile",
    )
    c.add_argument(
        "--hold", type=float, default=0.0, help="seconds to re-apply what an area load undoes"
    )
    c.add_argument("--scratch", type=lambda s: int(s, 0), help="free RAM for added collision")
    c.add_argument("--dry", action="store_true", help="plan it offline; touch no emulator")
    c.set_defaults(run=inject)

    r = groups.render.add_parser(
        "map", help="draw a stage, or a map row one tile per section, from fixed cameras"
    )
    where = r.add_mutually_exclusive_group(required=True)
    where.add_argument("--stage", type=int, help="the stage (section) to draw")
    where.add_argument("--row", type=int, help="every section of a map row, one tile each")
    r.add_argument(
        "--view", action="append", choices=VIEWS, help="repeatable (default: iso); --row takes one"
    )
    r.add_argument("--doc", type=Path, help="a map document whose edits the stage shows")
    r.add_argument("--data", type=Path, help="extracted game (default: $MHFU_DATA)")
    r.add_argument("--contact", type=Path, metavar="PNG", help="the tiles as one contact sheet")
    r.add_argument("--collision", action="store_true", help="draw the collision by class")
    r.add_argument("--lattice", action="store_true", help="draw the broadphase lattice")
    r.add_argument("--no-backdrop", action="store_true", help="hide the sky and far terrain")
    r.add_argument(
        "--mode",
        type=int,
        default=0,
        choices=range(5),
        help="0 textured, 1 vertex colour, 2 texture only, 3 by group, 4 flat",
    )
    flags.add_flags(r)
    r.set_defaults(run=render)


def _game(args: argparse.Namespace) -> Extracted:
    from mhfu_studio.shell import places

    return places.extracted(given=args.data)


def inject(args: argparse.Namespace) -> int:
    from mhfu import addresses as a
    from mhfu.memory import Live

    from mhfu_studio.stage import live
    from mhfu_studio.stage import ops as O
    from mhfu_studio.stage.file import StageFile

    from .document import MapDocument

    doc = MapDocument.load(args.document)
    base = doc.directory or Path(".")
    entries = doc.stages
    if args.stage is not None:
        entry = doc.stage(args.stage)
        if entry is None:
            raise ValueError(f"the document has no stage st{args.stage:03d}")
        entries = [entry]
    halves = {h: getattr(args, h) for h in HALVES}
    if not any(halves.values()):
        halves = dict.fromkeys(halves, True)
    game = _game(args)
    pushes = []
    for s in entries:
        print(f"{s.label}: {len(s.ops)} op(s) in {s.ops_file}")
        found = O.check(s.ops, base_dir=base, where=s.label)
        for f in found:
            print(f)
        if any(f.level == "error" for f in found):
            raise ValueError(f"{s.ops_file} has errors; nothing pushed")
        sf = StageFile.read(game, s.number)
        p = live.prepare(sf, s.ops, base, scratch=args.scratch or a.STAGE_SCRATCH, **halves)
        for line in [*p.log, *map(str, p.findings), *live.describe(p)]:
            print(line)
        catch = args.catch
        if catch is None and not args.restore:
            catch = live.default_catch(p, print)
        pushes.append((p, catch or 0.0))
    if args.dry:
        return 0
    with live.connect(args.port) as client:
        mem = Live(client)
        for p, catch in pushes:
            label = p.stage.label
            if len(pushes) > 1 and live.pac_address(mem, p.stage.number) is None:
                print(f"{label}: not resident, skipped (stand in the area and push it alone)")
            elif args.restore:
                live.restore(mem, p.stage)
            else:
                undo = live.undo_path(p.stage.number)
                live.run(client, mem, p, catch_for=catch, hold_for=args.hold, undo_file=undo)
    return 0


def render(args: argparse.Namespace) -> int:
    from .core.atlas import Atlas
    from .core.scene import open_stage
    from .render import headless

    if args.contact is None or args.out is not None or args.golden is not None:
        flags.check_args(args)
    game = _game(args)
    atlas = Atlas(game)
    views = args.view or ["iso"]
    layers = headless.Layers(args.collision, args.lattice, not args.no_backdrop, args.mode)
    if args.stage is not None:
        scene = _edited(open_stage(game, args.stage), args.doc)
        shots = headless.views(
            scene,
            views,
            arrivals=atlas.arrivals(args.stage),
            layers=layers,
            size=args.size,
            samples=args.samples,
            label=lambda v: f"st{scene.stage:03d}_{v}",
        )
    else:
        if len(views) > 1:
            raise ValueError("a row draws one view: give --view once")
        row = atlas.row(args.row)
        scenes = [
            (_edited(open_stage(game, s.stage), args.doc), atlas.arrivals(s.stage))
            for s in row.sections
            if s.present
        ]
        if not scenes:
            raise ValueError(f"row {args.row} has no section")
        shots = headless.tiles(
            scenes, views[0], layers=layers, size=args.size, samples=args.samples
        )
    if args.contact is not None:
        from mhfu_studio.harness.render import contact_sheet
        from mhfu_studio.shell.target import write_png

        images = shots.images
        sheet = contact_sheet(list(images.values()), list(images))
        print(f"wrote {write_png(flags.check_out(args.contact), sheet)}")
        if args.out is None and args.golden is None:
            return 0
    return flags.finish(args, shots)


def _edited(scene: MapScene, doc_dir: Path | None) -> MapScene:
    """The scene with the document's list for its stage replayed, when there is one."""
    if doc_dir is None:
        return scene
    from .core.edit import EditSession
    from .document import MapDocument

    doc = MapDocument.load(doc_dir)
    entry = doc.stage(scene.stage)
    if entry is not None and entry.ops:
        session = EditSession(scene, entry.ops, doc.directory)
        for f in session.findings:
            print(f)
    return scene
