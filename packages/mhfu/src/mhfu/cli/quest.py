# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`mhfu go-on-quest`: cold boot the game and take a quest, every step waiting on memory.

    mhfu go-on-quest --until village
    mhfu go-on-quest --rank 1 --list
    mhfu go-on-quest --rank 1 --quest Velocidrome
    mhfu go-on-quest --board hall --rank 0 --quest gather --until accepted

It always cold boots, since plugins load on a cold boot only, and leaves the emulator running
where it stopped unless --stop. It fast-forwards until it stops, unless --no-fast.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import TYPE_CHECKING

from ..live import Session, boot, hall, quests
from .live import launcher, launcher_args

if TYPE_CHECKING:
    from . import Subparsers

UNTIL = ("village", "board", "accepted", "quest")


def register(sub: Subparsers) -> None:
    p = sub.add_parser(
        "go-on-quest", help="cold boot the game and take a quest, driven by memory reads"
    )
    p.add_argument(
        "--quest", help="substring of the quest's name, else its objective or monsters, as shown"
    )
    p.add_argument(
        "--rank",
        type=int,
        default=0,
        choices=range(len(quests.RANKS)),
        help="0 = 1 star ... 5 = 6 stars, 6 = urgent",
    )
    p.add_argument("--board", choices=("elder", "hall"), default="elder", help="whose board")
    p.add_argument(
        "--counter",
        choices=sorted(hall.COUNTERS),
        default="counter_low",
        help="the hall counter, with --board hall",
    )
    p.add_argument("--slot", type=int, default=0, help="character slot; only 0 can be chosen")
    p.add_argument("--language", choices=boot.LANGUAGES, default="english")
    p.add_argument("--list", action="store_true", help="print the rank's quests and stop")
    p.add_argument("--until", choices=UNTIL, default="quest", help="stop once this is reached")
    p.add_argument("--no-depart", action="store_true", help="the same as --until accepted")
    p.add_argument("--stop", action="store_true", help="stop the emulator at the end")
    p.add_argument(
        "--no-fast", dest="fast", action="store_false", help="run at the game's own speed"
    )
    launcher_args(p)
    p.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    until = "accepted" if args.no_depart else args.until
    if args.slot:
        print("mhfu go-on-quest: no known cursor picks another slot than 0", file=sys.stderr)
        return 2
    if until in ("accepted", "quest") and not args.list and not args.quest:
        print("mhfu go-on-quest: pass --quest NAME, or --list", file=sys.stderr)
        return 2
    start = time.monotonic()

    def log(message: str) -> None:
        print(f"[{time.monotonic() - start:6.1f}s] {message}", flush=True)

    try:
        with (
            Session.launch(launcher(args), cold=True, stop_on_exit=args.stop) as s,
            boot.fast_forward(s, args.fast),
        ):
            return go(s, args, until, log)
    except (ConnectionError, RuntimeError, TimeoutError, LookupError) as e:
        print(f"mhfu go-on-quest: {e}", file=sys.stderr)
        return 1


def go(s: Session, args: argparse.Namespace, until: str, log: quests.Log) -> int:
    boot.to_village(s, args.language)
    log("village")
    if until == "village":
        return 0
    if args.board == "hall":
        if not hall.in_hall(s) and not hall.enter(s):
            raise RuntimeError("could not get into the gathering hall")
        log("gathering hall")
    rank = quests.RANKS[args.rank]
    if args.list or until == "board":
        quests.open_board(s, args.rank, board=args.board, counter=args.counter)
        log(f"{args.board} board, rank {rank}")
        for card in quests.list_quests(s):
            print(f"  {card.index:2d}  {card.name}")
            print(f"      {card.objective}  [{'/'.join(card.monsters)}]")
        return 0
    taken = quests.take(
        s,
        args.rank,
        args.quest,
        board=args.board,
        counter=args.counter,
        leave=until == "quest",
        log=log,
    )
    if taken.area is not None:
        log(f"in the quest, area index {taken.area}")
    return 0
