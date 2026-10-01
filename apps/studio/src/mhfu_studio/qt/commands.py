# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`studio qt`: the Qt spike's window."""

from __future__ import annotations

import argparse
from pathlib import Path

from mhfu_studio.cli import Groups


def register(groups: Groups) -> None:
    p = groups.top.add_parser("qt", help="open the Qt spike's window (needs the qt extra)")
    p.add_argument("path", nargs="?", type=Path, help="a map document")
    p.set_defaults(run=_run)


def _run(args: argparse.Namespace) -> int:
    try:
        from mhfu_studio.qt.window import main
    except ImportError as e:
        raise ValueError(f"{e}: install the qt extra (uv sync --extra qt)") from None
    return main(path=args.path)
