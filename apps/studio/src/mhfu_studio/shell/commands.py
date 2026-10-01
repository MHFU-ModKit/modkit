# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The `studio` commands of this area."""

from __future__ import annotations

import argparse
from pathlib import Path

from mhfu_studio.cli import AREAS, Groups
from mhfu_studio.harness.flags import size


def register(groups: Groups) -> None:
    """Adds this area's commands; nothing heavy may be imported at module level."""
    p = groups.top.add_parser("open", help="open the window (the default command)")
    p.add_argument("path", nargs="?", type=Path, help="a document; picks its workspace")
    p.add_argument("--workspace", help="start in this workspace")
    p.add_argument("--size", type=size, default=(1500, 940), metavar="WxH")
    p.set_defaults(run=_open)


def _open(args: argparse.Namespace) -> int:
    from mhfu_studio.shell.app import Studio
    from mhfu_studio.shell.workspace import discover

    found = discover(AREAS)
    studio = Studio([make() for make in found.values()], size=args.size)
    if args.workspace:
        try:
            studio.switch(args.workspace)
        except KeyError as e:
            raise ValueError(e.args[0]) from None
    if args.path is not None and not studio.open(args.path):
        raise ValueError(studio.message)
    studio.run()
    return 0
