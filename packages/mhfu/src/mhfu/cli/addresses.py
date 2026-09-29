"""`mhfu addresses`: the address table as a C header or a Lua module."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .. import addresses

if TYPE_CHECKING:
    from . import Subparsers


def register(sub: Subparsers) -> None:
    p = sub.add_parser("addresses", help="print the address table as a C header or Lua module")
    p.add_argument("lang", choices=sorted(addresses.RENDER))
    p.add_argument("-o", "--output", type=Path, help="write here instead of stdout")
    p.add_argument("--table", type=Path, help="read this TOML instead of the packaged one")
    p.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    table = addresses.load(args.table) if args.table else addresses.table()
    text = addresses.RENDER[args.lang](table)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0
