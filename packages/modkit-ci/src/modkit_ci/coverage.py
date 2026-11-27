# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Coverage floors that only rise (stub)."""

from __future__ import annotations

import argparse


def _cmd(args: argparse.Namespace) -> int:
    raise NotImplementedError


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add the `coverage` command."""
    p = sub.add_parser("coverage", help="fail when a coverage floor fell")
    p.add_argument("--base", required=True)
    p.set_defaults(func=_cmd)
