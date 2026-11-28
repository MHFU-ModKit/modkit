# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The pinned LuaLS: locate it, dump its doc JSON, run its check (stub)."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Final

VERSION: Final = "3.19.1"
SHA256: Final = {"linux-x64": "e9235d2d72ef55bc41cf8c99cda2ed64777682024b4bb81f5dea425060c5cbb8"}


class LualsMissing(RuntimeError):
    """No usable LuaLS binary."""


def binary() -> Path:
    """$LUALS_BIN, else the verified cached download, else PATH."""
    raise NotImplementedError


def doc(root: Path, out: Path) -> list[dict[str, Any]]:
    """LuaLS `--doc` JSON of `root`, written to `out`."""
    raise NotImplementedError


def check(root: Path) -> int:
    """Render the generated addresses, then LuaLS `--check` at Warning; its exit code."""
    raise NotImplementedError


def _cmd(args: argparse.Namespace) -> int:
    raise NotImplementedError


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add the `luals` command."""
    p = sub.add_parser("luals", help="LuaLS helper: path, check, doc")
    p.add_argument("action", nargs="?", choices=["path", "check", "doc"], default="path")
    p.add_argument("root", nargs="?")
    p.add_argument("-o", "--output")
    p.set_defaults(func=_cmd)
