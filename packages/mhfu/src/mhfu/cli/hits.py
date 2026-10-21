# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`mhfu hits`: a big monster's hits on the hunter, counted per (main, sub) pair in the running
game (`mhfu.live.hits`).

    mhfu hits --lane 3 --seconds 120 --engage --json hits.json

The game must already run, in a quest with the monster in it. The hunter's HP is held (`--hp`),
so a census measures damage dealt, not whether a hunter survives it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ppsspp_debug import DebuggerError

from ..files import Extracted
from ..live import clips, hits, survival
from ..live.rig import Rig
from .live import launcher, launcher_args

if TYPE_CHECKING:
    from . import Subparsers

HP = 1000
"""The hunter's pool by default: a hit of 126 must not cart a 100-HP one mid-window."""


def register(sub: Subparsers) -> None:
    p = sub.add_parser(
        "hits",
        help="a big monster's hits on the hunter per (main, sub) pair, live",
        description="Watch one big monster for --seconds of emulated time and count, per (main, "
        "sub) pair: the entries, the attack spawns (and those within --reach of the hunter), the "
        "hits on the hunter and the damage. Attaches to a game already in a quest. A breakpoint "
        "that logged nothing is named at the end: a zero is a result only if it can fire.",
    )
    launcher_args(p, paths=False)
    p.add_argument("--seconds", type=float, default=60.0, help="emulated seconds to watch")
    p.add_argument(
        "--reach",
        type=float,
        default=hits.REACH,
        help="units from the hunter within which a spawn counts as in reach",
    )
    p.add_argument("--json", type=Path, help="write the census and every event here")
    p.add_argument("--slot", type=int, help="the monster's registry slot (default the first)")
    p.add_argument("--hp", type=int, default=HP, help="hold the hunter's HP here (0: leave it)")
    p.add_argument(
        "--engage",
        action="store_true",
        help="bring the monster to the hunter when it is far, out of the section or idle",
    )
    p.add_argument(
        "--keep-small",
        action="store_true",
        help="leave small monsters alone (else their HP is zeroed, so only the big one hurts)",
    )
    p.add_argument(
        "--data",
        type=Path,
        help="extracted game (default: $MHFU_DATA); any species but em75's needs it",
    )
    p.set_defaults(run=run)


def _game(path: Path | None) -> Extracted | None:
    """The extracted game; None when none is given and none is set (em75 needs none)."""
    try:
        return Extracted.find(path)
    except FileNotFoundError:
        if path is not None:
            raise
        return None


def run(args: argparse.Namespace) -> int:
    try:
        game = _game(args.data)
        with Rig.attach(launcher(args)) as rig:
            s = rig.s
            slot, monster = clips.monster(s, args.slot)
            aim = hits.target(monster.vtable, game)
            print(f"monster slot {slot} at 0x{monster.base:08X}", file=sys.stderr)
            if args.hp:
                s.game.player.max_hp = args.hp
            keeper = hits.Keeper(
                s,
                monster,
                lambda: rig.summon(monster, distance=hits.BRING),
                engage=args.engage,
            )
            with survival.Guard(
                s,
                hp=args.hp or None,
                calm_monsters=False,
                cull_small=not args.keep_small,
                tick=0.2,
                log=lambda line: print(line, file=sys.stderr),
            ):
                found = hits.run(s, monster, aim, args.seconds, reach=args.reach, keep=keeper)
    except (ConnectionError, LookupError, TimeoutError, RuntimeError, DebuggerError) as e:
        print(f"mhfu hits: {e}", file=sys.stderr)
        return 1
    print(hits.report(found))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(found.to_json(), indent=1), encoding="utf-8")
    return int(found.ended != "time")
