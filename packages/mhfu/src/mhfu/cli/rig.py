# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`mhfu rig`: set up a live experiment in a running game (`mhfu.live.rig`).

    mhfu rig load 6 --lane 1          # savestate slot 6 of lane 1's stick, or a path
    mhfu rig where
    mhfu rig teleport 6400 7800
    mhfu rig summon --distance 500
    mhfu rig pin --seconds 60
    mhfu rig speed fast
    mhfu rig points --map maps/snow                   # the named points of a map document
    mhfu rig goto wall --map maps/snow                # one area change, then onto the floor
    mhfu rig walk camp_gate wall --map maps/snow      # walked, exit to exit

A cold boot into a quest is `mhfu go-on-quest`, which fast-forwards too. A map document is the
studio's (`map.toml` or its folder), by default `MHFU_MAP`.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING

from ppsspp_debug import DebuggerError

from .. import points as P
from ..files import Extracted
from ..live import boot, navigation, route, survival
from ..live.rig import OffFloor, Rig, big_monsters, floor
from ..points import Point
from ..stage import NotLoaded, map_manager
from .live import launcher, launcher_args

if TYPE_CHECKING:
    from . import Subparsers


def register(sub: Subparsers) -> None:
    p = sub.add_parser("rig", help="set up a live experiment: load, teleport, summon, pin HP")
    cmds = p.add_subparsers(dest="rig_command", required=True, metavar="COMMAND")

    def command(name: str, help: str) -> argparse.ArgumentParser:
        c = cmds.add_parser(name, help=help)
        launcher_args(c)
        return c

    c = command("load", "load a savestate: into the running game, else at launch")
    c.add_argument("state", help="slot number on the lane's stick, or a path")
    c.set_defaults(run=load)
    c = command("save", "save a savestate")
    c.add_argument("state", help="slot number on the lane's stick, or a path")
    c.set_defaults(run=save)
    command("where", "the stage, the player and the floor below, the big monsters").set_defaults(
        run=where
    )
    c = command("teleport", "put the player on the floor at x, z of this stage")
    c.add_argument("x", type=float)
    c.add_argument("z", type=float)
    c.add_argument("--near", type=float, help="of several floors there, the one nearest this y")
    c.set_defaults(run=teleport)
    c = command("summon", "move a big monster next to the player and check it is drawn")
    c.add_argument("--distance", type=float, default=600.0)
    c.add_argument("--bearing", type=float, help="degrees; default the way the player faces")
    c.add_argument("--slot", type=int, help="its entity-registry slot; default the first")
    c.set_defaults(run=summon)
    c = command("pin", "hold the player's HP and the quest clock until ctrl-c")
    c.add_argument("--hp", type=int, help="default: the maximum")
    c.add_argument("--seconds", type=float, help="stop after this long")
    c.set_defaults(run=pin)
    c = command("speed", "fast-forward, or the game's own rate")
    c.add_argument("speed", choices=("fast", "normal"))
    c.set_defaults(run=speed)
    c = cmds.add_parser("points", help="the named points of a map document")
    _map_args(c)
    c.set_defaults(run=points)
    c = command("goto", "put the player at a named point, changing area first if need be")
    c.add_argument("point", nargs="?", help="a point's name in the map document")
    c.add_argument("--at", nargs=4, type=float, metavar=("STAGE", "X", "Y", "Z"), help="or here")
    _map_args(c)
    c.set_defaults(run=goto)
    c = command("walk", "walk the player to named points in turn, exit to exit")
    c.add_argument("point", nargs="+", help="points' names in the map document, in order")
    c.add_argument("--no-guard", dest="guard", action="store_false", help="leave HP and the clock")
    c.add_argument(
        "--calm", action="store_true", help="also calm big monsters (their sight stays zero after)"
    )
    _map_args(c)
    c.set_defaults(run=walk)


def _fail(args: argparse.Namespace, e: Exception) -> int:
    print(f"mhfu rig {args.rig_command}: {e}", file=sys.stderr)
    return 1


def load(args: argparse.Namespace) -> int:
    try:
        with Rig.open(launcher(args), state=args.state) as rig:
            print(f"loaded {Rig.state_path(rig.launcher, args.state)}")
    except (ConnectionError, DebuggerError, RuntimeError, TimeoutError) as e:
        return _fail(args, e)
    return 0


def save(args: argparse.Namespace) -> int:
    try:
        with Rig.attach(launcher(args)) as rig:
            print(f"saved {rig.save(args.state)}")
    except (ConnectionError, DebuggerError) as e:
        return _fail(args, e)
    return 0


def where(args: argparse.Namespace) -> int:
    try:
        with Rig.attach(launcher(args)) as rig:
            s = rig.s
            game, player = s.game, s.game.player
            stage = map_manager(s.mem).stage
            print(f"stage st{stage:03d}, area {game.area_index}, screen {game.screen_state}")
            if not player.loaded:
                print("no player: loading or on a menu")
                return 0
            x, y, z = player.position
            try:
                under = floor(s).height(x, z, near=y)
            except NotLoaded as e:
                under = None
                print(f"floor: {e}")
            ground = f"{under:.1f}" if under is not None else "none"
            print(
                f"player ({x:.0f}, {y:.1f}, {z:.0f}) facing {math.degrees(player.facing):.0f}, "
                f"HP {player.hp}/{player.max_hp}, floor {ground}"
            )
            for m in big_monsters(s):
                mx, my, mz = m.position
                d = math.hypot(mx - x, mz - z)
                print(
                    f"{m.name} 0x{m.base:08X} ({mx:.0f}, {my:.0f}, {mz:.0f}), {d:.0f} away, "
                    f"section {m.section}, {'drawn' if m.drawn else 'not drawn'}"
                )
    except ConnectionError as e:
        return _fail(args, e)
    return 0


def teleport(args: argparse.Namespace) -> int:
    try:
        with Rig.attach(launcher(args)) as rig:
            x, y, z = rig.teleport(args.x, args.z, args.near)
    except (ConnectionError, OffFloor, NotLoaded, RuntimeError) as e:
        return _fail(args, e)
    print(f"player -> ({x:.0f}, {y:.1f}, {z:.0f})")
    return 0


def summon(args: argparse.Namespace) -> int:
    try:
        with Rig.attach(launcher(args)) as rig:
            monster = None
            if args.slot is not None:
                found = {m.base: m for m in big_monsters(rig.s)}
                base = rig.s.game.registry[args.slot]
                if base not in found:
                    return _fail(args, LookupError(f"slot {args.slot} holds no big monster"))
                monster = found[base]
            got = rig.summon(monster, distance=args.distance, bearing=args.bearing)
            name = got.monster.name
    except (ConnectionError, LookupError, OffFloor, NotLoaded, RuntimeError) as e:
        return _fail(args, e)
    x, y, z = got.at
    drawn = "drawn" if got.drawn else "NOT drawn"
    print(f"{name} -> ({x:.0f}, {y:.0f}, {z:.0f}), {got.distance:.0f} away, {drawn}")
    return 0 if got.drawn else 1


def pin(args: argparse.Namespace) -> int:
    try:
        with Rig.attach(launcher(args)) as rig, rig.pin_hp(args.hp) as guard:
            print(f"HP pinned at {guard.hp}; ctrl-c stops", flush=True)
            end = time.monotonic() + (args.seconds if args.seconds is not None else math.inf)
            try:
                while time.monotonic() < end:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                pass
    except (ConnectionError, RuntimeError) as e:
        return _fail(args, e)
    print(guard.report())
    return 0


def speed(args: argparse.Namespace) -> int:
    try:
        with Rig.attach(launcher(args)).s as s:  # not the rig, which would undo a fast-forward
            if not boot.set_fast(s, args.speed == "fast"):
                return _fail(args, RuntimeError("this PPSSPP cannot; the modkit's build can"))
    except ConnectionError as e:
        return _fail(args, e)
    return 0


def _map_args(c: argparse.ArgumentParser) -> None:
    c.add_argument("--map", type=Path, help="map document, map.toml or its folder (MHFU_MAP)")
    c.add_argument("--data", type=Path, help="extracted game (default: $MHFU_DATA)")


def _points(args: argparse.Namespace) -> list[Point]:
    where = args.map or os.environ.get("MHFU_MAP")
    if where is None:
        raise FileNotFoundError("no map document: pass --map or set MHFU_MAP")
    return P.load(where)


def points(args: argparse.Namespace) -> int:
    try:
        found = _points(args)
    except (OSError, P.PointError) as e:
        return _fail(args, e)
    for p in found:
        x, y, z = p.at
        kind = f"climb {p.heading:.0f}" if p.kind == "climb" else p.kind
        note = f"  {p.note}" if p.note else ""
        print(f"{p.name:<20} st{p.stage:03d} ({x:.0f}, {y:.0f}, {z:.0f}) {kind}{note}")
    return 0


def _plan(rig: Rig, args: argparse.Namespace) -> route.Map:
    return route.Map.live(rig.s, Extracted.find(args.data))


def goto(args: argparse.Namespace) -> int:
    try:
        if args.at is not None:
            stage, x, y, z = args.at
            point = Point("--at", int(stage), (x, y, z))
        elif args.point is not None:
            point = P.find(_points(args), args.point)
        else:
            return _fail(args, ValueError("name a point or give --at"))
        with Rig.attach(launcher(args)) as rig:
            x, y, z = rig.goto(point, _plan(rig, args), log=print)
    except (OSError, KeyError, LookupError, P.PointError, RuntimeError, DebuggerError) as e:
        return _fail(args, e)
    print(f"player -> ({x:.0f}, {y:.1f}, {z:.0f})")
    return 0


def walk(args: argparse.Namespace) -> int:
    start = time.monotonic()

    def log(line: str) -> None:
        print(f"[{time.monotonic() - start:5.1f}s] {line}", flush=True)

    try:
        known = _points(args)
        targets = [P.find(known, name) for name in args.point]
        climbs = [p for p in known if p.kind == "climb"]
        with Rig.attach(launcher(args)) as rig, ExitStack() as guard:
            if args.guard or args.calm:
                guard.enter_context(survival.Guard(rig.s, calm_monsters=args.calm))
            plan = _plan(rig, args)
            for point in targets:
                log(f"to {point.name}")
                result = rig.walk(point, plan, climbs, log=log)
                if not result.reached:
                    log(f"{point.name}: {result.reason}, {result.remaining:.0f} short")
                    return 1
            p = navigation.pose(rig.s)
            x, y, z = p.x, p.y, p.z
    except (OSError, KeyError, LookupError, P.PointError, RuntimeError, DebuggerError) as e:
        return _fail(args, e)
    log(f"at ({x:.0f}, {y:.0f}, {z:.0f})")
    return 0
