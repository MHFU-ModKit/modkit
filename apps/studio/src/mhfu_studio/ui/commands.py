# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`studio qt`: the Qt window."""

from __future__ import annotations

import argparse
from pathlib import Path

from mhfu_studio.cli import Groups


def register(groups: Groups) -> None:
    """Adds `studio qt`; Qt itself is imported only when it runs."""
    p = groups.top.add_parser("qt", help="open the Qt window")
    p.add_argument("path", nargs="?", type=Path, help="a document; picks its workspace")
    p.set_defaults(run=_run)


def _run(args: argparse.Namespace) -> int:
    try:
        from mhfu_studio.ui.app import main
    except ImportError as e:
        raise ValueError(
            f"the Qt window cannot load ({e}). It needs PySide6 and, on Linux, Qt's system"
            " libraries (libxkbcommon, libEGL, fontconfig)."
        ) from None
    return main(path=args.path)
