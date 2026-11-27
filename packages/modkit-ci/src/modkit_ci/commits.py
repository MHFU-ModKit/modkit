# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Commit message validity, for the hook and for CI (stub)."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from modkit_ci.registry import Registry


def check(
    message: str,
    scopes: Sequence[str],
    *,
    ci: bool,
    files: Sequence[str] = (),
    registry: Registry | None = None,
) -> list[str]:
    """Problems with one commit message; empty when it is valid."""
    raise NotImplementedError


def _cmd(args: argparse.Namespace) -> int:
    raise NotImplementedError


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add the `commit-msg` and `commits` commands."""
    hook = sub.add_parser("commit-msg", help="check a commit message file")
    hook.add_argument("file")
    hook.set_defaults(func=_cmd)
    ci = sub.add_parser("commits", help="check every commit in base..head")
    ci.add_argument("--base", required=True)
    ci.add_argument("--head", default="HEAD")
    ci.set_defaults(func=_cmd)
