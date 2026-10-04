# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`mhfu shell`: the interactive debug shell on the running game."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ..live.session import Session
from ..live.shell import Shell

if TYPE_CHECKING:
    from . import Subparsers


def register(sub: Subparsers) -> None:
    p = sub.add_parser("shell", help="interactive debug shell on the running game")
    p.add_argument("--port", type=int, help="debugger port (default: the PPSSPP on this machine)")
    p.add_argument("--host", default="127.0.0.1", help="debugger host")
    p.add_argument("--data", type=Path, help="extracted game, for hitbox (default: $MHFU_DATA)")
    p.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    try:
        session = Session.attach(args.port, args.host)
    except (OSError, RuntimeError, TimeoutError) as e:
        print(f"mhfu shell: {e}", file=sys.stderr)
        return 1
    shell = Shell(session, data=args.data)
    try:
        shell.loop()
    finally:
        shell.session.close()  # `connect` may have replaced the first session
    return 0
