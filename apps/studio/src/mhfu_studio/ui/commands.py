# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`studio open`: the window, and what `studio` alone runs."""

from __future__ import annotations

import argparse
from pathlib import Path

from mhfu_studio.cli import Groups
from mhfu_studio.harness.flags import size


def register(groups: Groups) -> None:
    """Adds `studio open`; Qt itself is imported only when it runs."""
    p = groups.top.add_parser("open", help="open the window (the default command)")
    p.add_argument("path", nargs="?", type=Path, help="a document; picks its workspace")
    p.add_argument("--workspace", help="start in this workspace")
    p.add_argument("--size", type=size, metavar="WxH", help="default: the size it was closed at")
    p.set_defaults(run=_run)


def _run(args: argparse.Namespace) -> int:
    try:
        from mhfu_studio.ui.app import main
    except ImportError as e:
        raise ValueError(
            f"the window cannot load ({e}). It needs PySide6 and, on Linux, Qt's system"
            " libraries (libxkbcommon, libEGL, fontconfig)."
        ) from None
    return main(path=args.path, workspace=args.workspace, size=args.size)
