# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`mhfu clips`: play a big monster's executor entries one at a time and read what each body
part plays (`mhfu.live.clips`).

    mhfu-port inject ports/zinogre.toml           # the port on the lane's stick (MHFU_LANE)
    mhfu clips ride zinogre                       # bridge and rider deployed, cold boot to quest
    mhfu clips play 100
    mhfu-port slots zinogre.bin --manifest ports/zinogre.toml -o slots.csv
    mhfu clips sweep --expect slots.csv --out sweep.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from collections.abc import Iterable
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING

from ppsspp_debug import DebuggerError

from ..live import clips
from ..live.rig import Rig
from .live import launcher, launcher_args

if TYPE_CHECKING:
    from . import Subparsers

FIELDS = (
    "entry",
    "part",
    "taken",
    "stream",
    "slot",
    "held",
    "frames",
    "speed",
    "moved",
    "kicked",
    "waited",
    "pair",
    "tries",
    "verdict",
)


def register(sub: Subparsers) -> None:
    p = sub.add_parser("clips", help="play executor entries and read each body part's clip")
    cmds = p.add_subparsers(dest="clips_command", required=True, metavar="COMMAND")

    c = cmds.add_parser("ride", help="deploy a port's rider and bridge, cold boot into the quest")
    launcher_args(c)
    c.add_argument("name", help="the port, as `mhfu-port inject` placed it")
    c.add_argument("--pac", help="its file in the inject directory (default NAME.bin)")
    c.add_argument("--host", type=int, default=clips.TIGREX, help="species it rides")
    c.add_argument("--replace", type=int, default=clips.GIADROME, help="species it replaces")
    c.add_argument("--quest", default="Giadrome", help="substring of the quest's name")
    c.add_argument("--rank", type=int, default=1, help="0 = 1 star ...")
    c.set_defaults(run=ride)

    c = cmds.add_parser("play", help="hold one entry and print what each body part plays")
    _common(c)
    c.add_argument("entry", type=int)
    c.add_argument("--hold", action="store_true", help="keep the hold (`mhfu clips release`)")
    c.set_defaults(run=play)

    c = cmds.add_parser("sweep", help="play entries in turn and check each part's clip")
    _common(c)
    c.add_argument("entries", nargs="*", help="N or N-M; default --expect's, else the pack's")
    c.add_argument("--expect", type=Path, help="`mhfu-port slots` CSV of the build")
    c.add_argument("--out", type=Path, help="one CSV row per entry and part")
    c.set_defaults(run=sweep)

    c = cmds.add_parser("release", help="end a hold")
    _common(c)
    c.set_defaults(run=release)


def _common(c: argparse.ArgumentParser) -> None:
    launcher_args(c)
    c.add_argument("--slot", type=int, help="the monster's registry slot (default the first)")
    c.add_argument("--kick", type=float, default=2.0, help="seconds before restarting the action")
    c.add_argument("--timeout", type=float, default=6.0, help="seconds to wait for a dispatch")


def entries(words: Iterable[str]) -> list[int]:
    """`N` and `N-M` words."""
    out: list[int] = []
    for w in words:
        lo, _, hi = w.partition("-")
        out.extend(range(int(lo), int(hi or lo) + 1))
    return out


def _fail(args: argparse.Namespace, e: Exception) -> int:
    print(f"mhfu clips {args.clips_command}: {e}", file=sys.stderr)
    return 1


def ride(args: argparse.Namespace) -> int:
    start = time.monotonic()

    def log(message: str) -> None:
        print(f"[{time.monotonic() - start:6.1f}s] {message}", flush=True)

    try:
        rig = clips.ride(
            args.name,
            pac=args.pac,
            host=args.host,
            replace=(args.replace,),
            quest=args.quest,
            rank=args.rank,
            launcher=launcher(args),
            log=log,
        )
        with rig:
            slot, m = clips.monster(rig.s)
            log(f"{m.name} in registry slot {slot} at 0x{m.base:08X}; bridge up")
    except (ConnectionError, DebuggerError, LookupError, RuntimeError, TimeoutError) as e:
        return _fail(args, e)
    return 0


def _rows(p: clips.Played, expect: clips.Expect | None) -> list[list[object]]:
    bad = "; ".join(clips.verdict(p, expect)) or "ok"
    return [
        [
            p.entry,
            q.part,
            int(q.taken),
            q.asked[0],
            q.asked[1],
            " ".join(f"{s}:{i}" for s, i in q.held),
            f"{q.end:g}",
            f"{q.speed:g}",
            int(q.moved),
            int(p.kicked),
            f"{p.waited:.1f}",
            "{},{}".format(*p.pair),
            p.tries,
            bad,
        ]
        for q in p.parts
    ]


def play(args: argparse.Namespace) -> int:
    try:
        with Rig.attach(launcher(args)) as rig:
            s = rig.s
            p = clips.play(
                s, args.entry, slot=args.slot, kick_after=args.kick, timeout=args.timeout
            )
            if not args.hold:
                clips.release(s, args.slot)
    except (ConnectionError, DebuggerError, LookupError, TimeoutError, ValueError) as e:
        return _fail(args, e)
    how = "after restarting the action" if p.kicked else "on its own"
    took = f"dispatched in {p.waited:.1f} s {how}" if p.dispatched else "NOT dispatched"
    print(f"entry {p.entry}: {took}")
    for q in p.parts:
        plays = " / ".join(f"stream {s} slot {i}" for s, i in q.held) or "a clip outside the pack"
        if not q.taken:
            note = "; not dispatched to this part"
        elif q.empty:
            note = f"; stream {q.asked[0]} slot {q.asked[1]} is empty"
        else:
            note = (
                "" if q.resolved else f"; the resolver names stream {q.asked[0]} slot {q.asked[1]}"
            )
        state = "moving" if q.moved else "standing"
        print(f"  part {q.part}: {plays}, {q.end:g} frames at speed {q.speed:g}, {state}{note}")
    return 0 if p.dispatched and all(q.resolved for q in p.parts if q.taken) else 1


def sweep(args: argparse.Namespace) -> int:
    expect = clips.read_expect(args.expect) if args.expect else {}
    good = bad = 0
    try:
        with ExitStack() as stack:
            out = None
            if args.out:
                out = csv.writer(stack.enter_context(args.out.open("w", newline="")))
                out.writerow(FIELDS)
            rig = stack.enter_context(Rig.attach(launcher(args)))
            s = rig.s
            todo = entries(args.entries) or sorted(expect)
            if not todo:
                _, m = clips.monster(s, args.slot)
                todo = clips.Pack.read(s.mem, m.action_table).entries()
            print(f"{len(todo)} entries, about {len(todo) * 3 // 60 + 1} min", flush=True)
            played = clips.sweep(
                s, todo, slot=args.slot, kick_after=args.kick, timeout=args.timeout
            )
            for p in played:
                wrong = clips.verdict(p, expect.get(p.entry))
                good, bad = good + (not wrong), bad + bool(wrong)
                frames = "/".join(f"{q.end:g}" for q in p.parts)
                where = " ".join(f"{s_}:{i}" for q in p.parts for s_, i in q.held[:1])
                tries = f" (try {p.tries})" if p.tries > 1 else ""
                say = "; ".join(wrong) or "ok"
                print(f"{p.entry:4d}  {frames:<14} {where:<16} {say}{tries}", flush=True)
                if out:
                    out.writerows(_rows(p, expect.get(p.entry)))
    except KeyboardInterrupt:
        print("interrupted")
    except (ConnectionError, DebuggerError, LookupError, TimeoutError, ValueError) as e:
        return _fail(args, e)
    print(f"{good}/{good + bad} entries right on every part")
    return 0 if not bad else 1


def release(args: argparse.Namespace) -> int:
    try:
        with Rig.attach(launcher(args)) as rig:
            acked = clips.release(rig.s, args.slot)
    except (ConnectionError, DebuggerError, LookupError) as e:
        return _fail(args, e)
    print("released" if acked else "no ack: the game is paused or cli_bridge.lua is not loaded")
    return 0 if acked else 1
