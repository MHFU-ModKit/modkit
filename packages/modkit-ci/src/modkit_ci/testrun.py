# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""One pytest process per test unit (stub)."""

from __future__ import annotations

import argparse


def _cmd(args: argparse.Namespace) -> int:
    raise NotImplementedError


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add the `test` command."""
    p = sub.add_parser("test", help="run test units")
    p.add_argument("units", nargs="+")
    p.add_argument("--cov", action="store_true")
    p.add_argument("--no-qt", action="store_true")
    p.add_argument("pytest_args", nargs=argparse.REMAINDER)
    p.set_defaults(func=_cmd)
