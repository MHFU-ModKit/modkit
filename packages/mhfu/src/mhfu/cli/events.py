# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`mhfu events`: the framework's monster events in the running game (`mhfu.live.monster_events`):
the notice, combat entered and left, flinches, part breaks and the tail cut, one line each.

    mhfu move ride zinogre          # the framework, the bridge and a port; cold boot
    mhfu events                     # what the ring still holds
    mhfu events --follow 60         # and what happens in the next minute
    mhfu events --state             # each big monster now: noticed, in combat, its notice
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from ppsspp_debug import DebuggerError

from ..live import monster_events
from ..live.rig import Rig, big_monsters
from .live import launcher, launcher_args

if TYPE_CHECKING:
    from . import Subparsers


def register(sub: Subparsers) -> None:
    p = sub.add_parser("events", help="a big monster's notice, combat, flinches, breaks, tail cut")
    launcher_args(p)
    p.add_argument("--follow", type=float, metavar="SECONDS", help="then print new ones this long")
    p.add_argument("--state", action="store_true", help="each big monster's state instead")
    p.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    try:
        with Rig.attach(launcher(args)) as rig:
            if args.state:
                for m in big_monsters(rig.s):
                    print(f"0x{m.base:08X} {monster_events.state(rig.s.mem, m.base)}")
                return 0
            _, found = monster_events.read(rig.s.mem)
            for ev in found:
                print(ev)
            if args.follow:
                for ev in monster_events.follow(rig.s, args.follow):
                    print(ev, flush=True)
    except (ConnectionError, DebuggerError) as e:
        print(f"mhfu events: {e}", file=sys.stderr)
        return 1
    return 0
