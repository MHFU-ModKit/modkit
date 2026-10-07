# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`mhfu move`: a big monster plays an executor entry as a move of its own, through the
framework's move player (`mhfu.live.moves`), and what came of it.

    make -C framework                              # the PRX with the move player
    mhfu move ride zinogre                         # it, the bridge and the port; cold boot
    mhfu move play 46 --attack 6@56                # stamp_right_claw, attack 6 at frame 56
    mhfu move play 46 --attack 6@56-90             # ... ended at frame 90
    mhfu move play 46 --attack 6@56 --repeat 10    # a soak
    mhfu move play 20 --curve zinogre --walls      # the dash, its turn on YAW, ended by a wall
    mhfu move play 9 --turn fixed --total 90 --frames 50
    mhfu move ride zinogre --brain                 # with its manifest's moves and rules
    mhfu move play --own stamp                     # the riding port's own move, by name
    mhfu move play --own howl --force              # ... at once, even during the notice
    mhfu move watch --repeat 5                     # the next moves its rules or brain play
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ppsspp_debug import DebuggerError, Lane

from .. import inject
from ..live import clips, moves
from ..live.rig import Rig
from .live import launcher, launcher_args

if TYPE_CHECKING:
    from ..live.session import Session
    from . import Subparsers

LOG = "PLUGINS/mhfu_framework/framework.log"


def register(sub: Subparsers) -> None:
    p = sub.add_parser("move", help="play an executor entry as a big monster's own move")
    cmds = p.add_subparsers(dest="move_command", required=True, metavar="COMMAND")

    c = cmds.add_parser("ride", help="deploy the PRX, the bridge and a port; cold boot to quest")
    launcher_args(c)
    c.add_argument("name", help="the port, as `mhfu-port inject` placed it")
    c.add_argument("--prx", type=Path, default=clips.PRX, help="the framework (default: built)")
    c.add_argument("--quest", default="Giadrome", help="substring of the quest's name")
    c.add_argument("--rank", type=int, default=1, help="0 = 1 star ...")
    c.add_argument(
        "--brain", action="store_true", help="keep the port's manifest moves, claims and rules"
    )
    c.set_defaults(run=ride)

    c = cmds.add_parser("play", help="play a move and report every part, attack and HP write")
    launcher_args(c)
    c.add_argument("entry", type=int, nargs="?", help="executor entry: the clip")
    c.add_argument("--own", metavar="NAME", help="the riding port's own move instead of an entry")
    c.add_argument(
        "--force", action="store_true", help="play while the monster's notice runs, not after"
    )
    c.add_argument("--attack", action="append", default=[], metavar="ID@FRAME[-END]", type=_attack)
    c.add_argument("--carrier", type=_ints, default=(0, 2), metavar="MAIN,SUB")
    c.add_argument("--back", type=_ints, metavar="MAIN,SUB[,MODE]", help="entered at the end")
    c.add_argument("--length", type=int, default=0, help="AI frames; default the clip's")
    c.add_argument("--skip", action="store_true", help="no host AI step while the clip plays")
    c.add_argument("--part", type=int, default=0, help="body part whose cursor times attacks")
    c.add_argument(
        "--host-attacks", action="store_true", help="keep the host entry's own attacks and effects"
    )
    c.add_argument("--slot", type=int, help="the monster's registry slot (default the first)")
    c.add_argument("--after", type=float, default=8.0, help="seconds watched after the end")
    c.add_argument("--hp", type=int, help="set the hunter's HP and its caps to this first")
    c.add_argument("--repeat", type=int, default=1, help="moves in a row")
    c.add_argument(
        "--curve", metavar="PORT", help="YAW follows the entry's turn from PORT's clips module"
    )
    c.add_argument("--turn", choices=moves.TURNS, default="still", help="a turn on top of it")
    c.add_argument("--rate", type=int, default=64, help="hunter, away: YAW units an AI frame")
    c.add_argument("--total", type=float, default=0.0, help="fixed: degrees, the way YAW grows")
    c.add_argument("--frames", type=int, default=1, help="fixed: AI frames to spread it over")
    c.add_argument("--walls", action="store_true", help="a wall ahead ends the move")
    c.add_argument("--dir", type=float, default=0.0, help="degrees of the travel against YAW")
    c.add_argument("--stuck", type=_ints, default=(0, 6, 1), metavar="MAIN,SUB,MODE")
    c.set_defaults(run=play)

    c = cmds.add_parser("watch", help="report the next moves something else plays")
    launcher_args(c)
    c.add_argument("--slot", type=int, help="the monster's registry slot (default the first)")
    c.add_argument("--after", type=float, default=3.0, help="seconds watched after each end")
    c.add_argument("--timeout", type=float, default=60.0, help="seconds to wait for each start")
    c.add_argument("--repeat", type=int, default=1, help="moves to watch")
    c.set_defaults(run=watch)

    c = cmds.add_parser("stop", help="end the running move")
    launcher_args(c)
    c.add_argument("--slot", type=int)
    c.set_defaults(run=stop)


def _attack(word: str) -> moves.Attack:
    """ID@FRAME or ID@FRAME-END."""
    id_, _, frames = word.partition("@")
    frame, _, end = frames.partition("-")
    return moves.Attack(int(id_), int(frame), int(end) if end else None)


def _ints(word: str) -> tuple[int, ...]:
    return tuple(int(w) for w in word.split(","))


def _fail(args: argparse.Namespace, e: Exception) -> int:
    print(f"mhfu move {args.move_command}: {e}", file=sys.stderr)
    return 1


def ride(args: argparse.Namespace) -> int:
    start = time.monotonic()

    def log(message: str) -> None:
        print(f"[{time.monotonic() - start:6.1f}s] {message}", flush=True)

    try:
        rig = clips.ride(
            args.name,
            quest=args.quest,
            rank=args.rank,
            launcher=launcher(args),
            log=log,
            prx=args.prx,
            brain=args.brain,
        )
        with rig:
            slot, m = clips.monster(rig.s)
            log(f"{m.name} in registry slot {slot} at 0x{m.base:08X}; bridge up")
    except (ConnectionError, DebuggerError, LookupError, RuntimeError, TimeoutError) as e:
        return _fail(args, e)
    return 0


def _log_size(args: argparse.Namespace) -> int | None:
    lane = launcher(args).lane
    path = Lane(lane).stick / LOG if lane is not None else None
    return path.stat().st_size if path and path.is_file() else None


def report(r: moves.Played) -> list[str]:
    """What a played move says, one line per finding."""
    mv = r.move
    turned = ((r.yaw - r.yaw0 + 0x8000) & 0xFFFF) - 0x8000
    out = [
        f"entry {mv.entry}: {r.reason} after {r.frames} AI frames, then ({r.end_pair[0]},"
        f"{r.end_pair[1]}); {r.skipped} host steps skipped; end at {r.t_end:.2f} s",
        f"  YAW 0x{r.yaw0:04X} -> 0x{r.yaw:04X}, turned {turned * 360 / moves.FULL_TURN:.1f}"
        f" degrees (curve {len(r.steer.curve)} keys, {r.steer.turn})",
    ]
    for p in r.parts:
        held = " / ".join(f"{s}:{i}" for s, i in p.held) or "a clip outside the pack"
        ok = "played to its end" if p.played else "NOT played through"
        out.append(
            f"  part {p.part}: {held} (asked {p.asked[0]}:{p.asked[1]}), "
            f"peak {p.peak:g} of {p.end:g}: {ok}"
        )
    for sp in r.spawns:
        name = f"{sp.id}@{sp.frame}" + (f"-{sp.end}" if sp.end else "")
        if sp.at is None:
            out.append(f"  attack {name}: never spawned")
            continue
        ended = ""
        if sp.ended is not None:
            how = {0: "it had ended itself", 0xFF: "no longer ours"}.get(sp.ended_state, "ended")
            ended = f"; at AI frame {sp.ended}: {how}"
        out.append(
            f"  attack {name}: AI frame {sp.at}, cursor {sp.cursor:g}, node 0x{sp.node:08X}{ended}"
        )
    for w, drop in r.damage():
        late = [
            f"AI frame {w.frame}, {w.frame - sp.at:+d} from the spawn"
            for sp in r.spawns
            if sp.at is not None
        ]
        out.append(
            f"  hunter -{drop} HP at {w.t:.2f} s, phase {w.phase:g}, pair {w.pair}, "
            f"pc 0x{w.pc:08X} ra 0x{w.ra:08X}" + (f"; {late[0]}" if late else "")
        )
    after = [f"{t:.2f}s {m},{s}" for t, (m, s) in r.pairs]
    out.append("  pairs: " + "  ".join(after))
    later = [(w.t - r.t_end, d) for w, d in r.damage() if w.t > r.t_end]
    if later:
        out.append(f"  first hit after the end: {later[0][0]:.2f} s, -{later[0][1]} HP")
    return out


def _start(
    args: argparse.Namespace, s: Session, mv: moves.Move, steer: moves.Steer
) -> moves.Played:
    if not args.own:
        return moves.play(s, mv, slot=args.slot, after=args.after, steer=steer)

    def ask() -> None:
        if not moves.play_own(s, args.own, args.slot, args.force):
            raise LookupError(f"no port rides the monster, or it has no own move {args.own!r}")

    return moves.watch(s, ask, slot=args.slot, after=args.after)


def play(args: argparse.Namespace) -> int:
    if (args.entry is None) == (args.own is None):
        return _fail(args, ValueError("give an entry or --own NAME"))
    back = tuple(args.back) + (0,) * (3 - len(args.back)) if args.back else None
    mv = moves.Move(
        args.entry or 0,
        tuple(args.attack),
        (args.carrier[0], args.carrier[1]),
        (back[0], back[1], back[2]) if back else None,
        args.length,
        args.skip,
        args.part,
        host_attacks=args.host_attacks,
        force=args.force,
    )
    curve: tuple[int, ...] = ()
    if args.curve:
        lane = launcher(args).lane
        stick = Lane(lane).stick if lane is not None else None
        module = inject.memstick(stick) / inject.MODS_SUBDIR / inject.LIB
        module /= inject.port_module(args.curve, "turns")
        curve = moves.turns_of(module.read_text(encoding="utf-8")).get(args.entry, ())
    steer = moves.Steer(
        curve,
        args.turn,
        args.rate,
        args.total,
        args.frames,
        args.walls,
        args.dir,
        (args.stuck[0], args.stuck[1], args.stuck[2]),
    )
    ends: dict[str, int] = {}
    size = _log_size(args)
    try:
        with Rig.attach(launcher(args)) as rig:
            s = rig.s
            for n in range(1, args.repeat + 1):
                if args.hp:
                    p = s.game.player
                    p.hp_cap, p.max_hp, p.hp = args.hp, args.hp, args.hp
                r = _start(args, s, mv, steer)
                ends[r.reason] = ends.get(r.reason, 0) + 1
                if args.repeat > 1:
                    print(f"--- move {n}/{args.repeat}")
                print("\n".join(report(r)), flush=True)
    except (ConnectionError, DebuggerError, LookupError, RuntimeError, TimeoutError) as e:
        return _fail(args, e)
    grew = _log_size(args)
    if args.repeat > 1:
        print("ends: " + ", ".join(f"{k} x{v}" for k, v in sorted(ends.items())))
    if size is not None and grew is not None:
        print(f"framework.log {size} -> {grew} bytes")
    return 0


def watch(args: argparse.Namespace) -> int:
    try:
        with Rig.attach(launcher(args)) as rig:
            for n in range(1, args.repeat + 1):
                r = moves.watch(
                    rig.s, lambda: None, slot=args.slot, after=args.after, timeout=args.timeout
                )
                if args.repeat > 1:
                    print(f"--- move {n}/{args.repeat}")
                print("\n".join(report(r)), flush=True)
    except (ConnectionError, DebuggerError, LookupError, RuntimeError, TimeoutError) as e:
        return _fail(args, e)
    return 0


def stop(args: argparse.Namespace) -> int:
    try:
        with Rig.attach(launcher(args)) as rig:
            acked = moves.stop(rig.s, args.slot)
    except (ConnectionError, DebuggerError, LookupError) as e:
        return _fail(args, e)
    print("stopped" if acked else "no ack: the game is paused or cli_bridge.lua is not loaded")
    return 0 if acked else 1
