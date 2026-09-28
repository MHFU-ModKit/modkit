# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`mhp-formats`, the command line."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from . import iso
from ._base import FormatError


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mhp-formats", description=__doc__)
    commands = parser.add_subparsers(required=True, metavar="COMMAND")
    extract = commands.add_parser(
        "extract",
        help="copy a game's files out of its ISO, DATA.BIN decrypted and split",
        description="Copy the ISO's files into OUT, and DATA.BIN decrypted into OUT/data_files, "
        "one file per engine file id: file_NNNNN is id NNNNN + 1.",
    )
    extract.add_argument("iso", type=Path, help="the game's UMD image")
    extract.add_argument("out", type=Path, help="directory to write into")
    extract.set_defaults(run=_extract)
    args = parser.parse_args(argv)
    try:
        args.run(args)
    except (FormatError, ImportError, OSError) as e:
        parser.exit(1, f"mhp-formats: {e}\n")
    return 0


def _extract(args: argparse.Namespace) -> None:
    game = iso.extract(args.iso, args.out, lambda step: print(step, file=sys.stderr))
    print(f"{game.name} ({game.value}) extracted to {args.out}")
