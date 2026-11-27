# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The documentation site build (stub)."""

from __future__ import annotations

import argparse


def _cmd(args: argparse.Namespace) -> int:
    raise NotImplementedError


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add the `docs` command."""
    p = sub.add_parser("docs", help="build the documentation site")
    p.add_argument("action", choices=["build"])
    p.add_argument("--out", default="build/docs")
    p.add_argument("--zip")
    p.set_defaults(func=_cmd)
