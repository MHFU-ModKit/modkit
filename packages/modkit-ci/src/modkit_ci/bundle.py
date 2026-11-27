# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Release asset bundles (stub)."""

from __future__ import annotations

import argparse


def _cmd(args: argparse.Namespace) -> int:
    raise NotImplementedError


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add the `bundle` command."""
    p = sub.add_parser("bundle", help="assemble a module's release assets")
    p.add_argument("module")
    p.add_argument("--out", required=True)
    p.set_defaults(func=_cmd)
