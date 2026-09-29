# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`mhfu-port diff`: what differs between two built monster PACs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING

from ..diff import diff

if TYPE_CHECKING:
    from . import Subparsers


def register(sub: Subparsers) -> None:
    p = sub.add_parser("diff", help="compare two monster PACs structurally")
    p.add_argument("old", type=Path)
    p.add_argument("new", type=Path)
    p.add_argument("--json", action="store_true", help="every finding, as JSON")
    p.add_argument("--limit", type=int, default=12, help="examples per kind in the text")
    p.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    report = diff(args.old.read_bytes(), args.new.read_bytes())
    print(json.dumps(report.to_json(), indent=1) if args.json else report.text(args.limit))
    return 0
