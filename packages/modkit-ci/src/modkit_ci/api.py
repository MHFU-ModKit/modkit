# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The API check orchestrator (stub)."""

from __future__ import annotations

import argparse


def _cmd(args: argparse.Namespace) -> int:
    raise NotImplementedError


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add the `api` command."""
    p = sub.add_parser("api", help="fail on an unmarked break or a doc finding")
    p.add_argument("--base", required=True)
    p.set_defaults(func=_cmd)
