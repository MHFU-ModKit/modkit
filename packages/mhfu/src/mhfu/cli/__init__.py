"""The `mhfu` command. Each module in this package adds its subcommands through `register`."""

from __future__ import annotations

import argparse
import pkgutil
import sys
from importlib import import_module
from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    Subparsers: TypeAlias = argparse._SubParsersAction[argparse.ArgumentParser]


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="mhfu", description="Monster Hunter Freedom Unite (EU).")
    sub = ap.add_subparsers(dest="command", required=True, metavar="COMMAND")
    for module in sorted(m.name for m in pkgutil.iter_modules(__path__)):
        import_module(f"{__name__}.{module}").register(sub)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return int(args.run(args) or 0)
    except (FileNotFoundError, ValueError) as e:
        print(f"mhfu {args.command}: {e}", file=sys.stderr)
        return 1
