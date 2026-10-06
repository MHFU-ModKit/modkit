# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`mhfu start` and `mhfu stop`: the game in PPSSPP with its debugger open."""

from __future__ import annotations

import argparse
import dataclasses
import sys
from typing import TYPE_CHECKING

from ..live.session import Launcher, Session

if TYPE_CHECKING:
    from . import Subparsers


def register(sub: Subparsers) -> None:
    p = sub.add_parser("start", help="start the game with its debugger open and print the port")
    launcher_args(p)
    p.add_argument(
        "--state",
        help="savestate to load at launch, as the emulator sees it; skips the cold boot, "
        "so plugins do not load",
    )
    p.add_argument("--cold", action="store_true", help="restart an emulator already running")
    p.add_argument("--timeout", type=float, default=60.0, help="seconds to wait for the game")
    p.set_defaults(run=start)

    p = sub.add_parser("stop", help="stop the emulator")
    launcher_args(p, paths=False)
    p.set_defaults(run=stop)


def launcher_args(p: argparse.ArgumentParser, paths: bool = True) -> None:
    """Where the emulator runs: the options `launcher` reads back."""
    where = p.add_mutually_exclusive_group()
    where.add_argument(
        "--docker",
        dest="docker",
        action="store_true",
        default=None,
        help="PPSSPP in the modkit container (MHFU_LAUNCHER=docker)",
    )
    where.add_argument(
        "--local", dest="docker", action="store_false", help="PPSSPP on this machine (default)"
    )
    p.add_argument("--container", help="container name (MHFU_CONTAINER, default ppsspp)")
    p.add_argument("--lane", type=int, help="a hidden PPSSPP of its own, numbered (MHFU_LANE)")
    if paths:
        p.add_argument("--ppsspp", help="local PPSSPP binary (MHFU_PPSSPP)")
        p.add_argument("--iso", help="game image as the emulator sees it (MHFU_ISO)")


def launcher(args: argparse.Namespace) -> Launcher:
    given = {
        "docker": args.docker,
        "container": args.container,
        "lane": args.lane,
        "ppsspp": getattr(args, "ppsspp", None),
        "iso": getattr(args, "iso", None),
    }
    return dataclasses.replace(
        Launcher.from_env(), **{k: v for k, v in given.items() if v is not None}
    )


def start(args: argparse.Namespace) -> int:
    try:
        session = Session.launch(
            launcher(args),
            state=args.state,
            cold=args.cold,
            timeout=args.timeout,
            stop_on_exit=False,
        )
    except (ConnectionError, RuntimeError, TimeoutError) as e:
        print(f"mhfu start: {e}", file=sys.stderr)
        return 1
    with session:
        print(session.port)
    return 0


def stop(args: argparse.Namespace) -> int:
    print(f"stopped {launcher(args).stop()} emulator process(es)")
    return 0
