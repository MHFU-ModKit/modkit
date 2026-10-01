# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The `studio` command. With no command it opens the window; each area adds its own commands."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    Subparsers: TypeAlias = argparse._SubParsersAction[argparse.ArgumentParser]

AREAS = ("shell", "harness", "monster", "map", "stage", "qt")


@dataclass(frozen=True)
class Groups:
    """Where an area adds commands: `top` (studio X), `render`, `port` and `map` (studio map X)."""

    top: Subparsers
    render: Subparsers
    port: Subparsers
    map: Subparsers


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="studio", description="Author MHFU mods offline. With no command, opens the window."
    )
    top = ap.add_subparsers(dest="command", metavar="COMMAND")

    def group(name: str, help: str) -> Subparsers:
        return top.add_parser(name, help=help).add_subparsers(
            dest="action", required=True, metavar="ACTION"
        )

    groups = Groups(
        top=top,
        render=group("render", "draw a port or a stage to PNG, without a window"),
        port=group("port", "check, label, export and deploy a ported monster"),
        map=group("map", "edit stages and push the edits into the running game"),
    )
    for area in AREAS:
        import_module(f"mhfu_studio.{area}.commands").register(groups)
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = parser()
    args = ap.parse_args(argv)
    if args.command is None:
        args = ap.parse_args(["open"])
    try:
        return int(args.run(args) or 0)
    except (FileNotFoundError, ValueError) as e:
        print(f"studio {args.command}: {e}", file=sys.stderr)
        return 1
