# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`mhfu move`: a big monster plays an executor entry as a move of its own, through the
framework's move player (`mhfu.live.moves`), and what came of it.

    make -C framework                              # the PRX with the move player
    mhfu move ride zinogre                         # it, the bridge and the port; cold boot
    mhfu move play 46 --attack 6@56                # stamp_right_claw, attack 6 at frame 56
    mhfu move play 46 --attack 6@56 --repeat 10    # a soak
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ppsspp_debug import DebuggerError, Lane

from ..live import clips, moves
from ..live.rig import Rig
from .live import launcher, launcher_args

if TYPE_CHECKING:
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
    c.set_defaults(run=ride)

    c = cmds.add_parser("play", help="play a move and report every part, attack and HP write")
    launcher_args(c)
    c.add_argument("entry", type=int, help="executor entry: the clip")
    c.add_argument("--attack", action="append", default=[], metavar="ID@FRAME", type=_attack)
    c.add_argument("--carrier", type=_ints, default=(0, 2), metavar="MAIN,SUB")
    c.add_argument("--back", type=_ints, metavar="MAIN,SUB[,MODE]", help="entered at the end")
    c.add_argument("--length", type=int, default=0, help="AI frames; default the clip's")
    c.add_argument("--skip", action="store_true", help="no host AI step while the clip plays")
    c.add_argument("--part", type=int, default=0, help="body part whose cursor times attacks")
    c.add_argument("--slot", type=int, help="the monster's registry slot (default the first)")
    c.add_argument("--after", type=float, default=8.0, help="seconds watched after the end")
    c.add_argument("--hp", type=int, help="set the hunter's HP and its caps to this first")
    c.add_argument("--repeat", type=int, default=1, help="moves in a row")
    c.set_defaults(run=play)

    c = cmds.add_parser("stop", help="end the running move")
    launcher_args(c)
    c.add_argument("--slot", type=int)
    c.set_defaults(run=stop)


def _attack(word: str) -> tuple[int, int]:
    """ID@FRAME -> (frame, id)."""
    id_, _, frame = word.partition("@")
    return int(frame), int(id_)


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
    out = [
        f"entry {mv.entry}: {r.reason} after {r.frames} AI frames, then ({r.end_pair[0]},"
        f"{r.end_pair[1]}); {r.skipped} host steps skipped; end at {r.t_end:.2f} s"
    ]
    for p in r.parts:
        held = " / ".join(f"{s}:{i}" for s, i in p.held) or "a clip outside the pack"
        ok = "played to its end" if p.played else "NOT played through"
        out.append(
            f"  part {p.part}: {held} (asked {p.asked[0]}:{p.asked[1]}), "
            f"peak {p.peak:g} of {p.end:g}: {ok}"
        )
    for sp in r.spawns:
        if sp.at is None:
            out.append(f"  attack {sp.id}@{sp.frame}: never spawned")
        else:
            out.append(
                f"  attack {sp.id}@{sp.frame}: AI frame {sp.at}, cursor {sp.cursor:g}, "
                f"node 0x{sp.node:08X}"
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


def play(args: argparse.Namespace) -> int:
    back = tuple(args.back) + (0,) * (3 - len(args.back)) if args.back else None
    mv = moves.Move(
        args.entry,
        tuple(args.attack),
        (args.carrier[0], args.carrier[1]),
        (back[0], back[1], back[2]) if back else None,
        args.length,
        args.skip,
        args.part,
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
                r = moves.play(s, mv, slot=args.slot, after=args.after)
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


def stop(args: argparse.Namespace) -> int:
    try:
        with Rig.attach(launcher(args)) as rig:
            acked = moves.stop(rig.s, args.slot)
    except (ConnectionError, DebuggerError, LookupError) as e:
        return _fail(args, e)
    print("stopped" if acked else "no ack: the game is paused or cli_bridge.lua is not loaded")
    return 0 if acked else 1
