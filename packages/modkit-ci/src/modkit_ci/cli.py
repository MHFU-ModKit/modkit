# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The `modkit-ci` command line: every module registers its commands."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from modkit_ci import (
    affected,
    api,
    bundle,
    commits,
    coverage,
    docs,
    luals,
    registry,
    release,
    testrun,
)

_MODULES = (registry, affected, testrun, coverage, commits, api, luals, docs, bundle, release)


def _split(argv: list[str]) -> tuple[list[str], bool, list[str]]:
    if "--" not in argv:
        return argv, False, []
    i = argv.index("--")
    return argv[:i], True, argv[i + 1 :]


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command named on the command line; its exit code."""
    parser = argparse.ArgumentParser(prog="modkit-ci", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for module in _MODULES:
        module.register(sub)
    argv = sys.argv[1:] if argv is None else list(argv)
    # everything after the first `--` is the command's own pass-through (pytest options)
    head, sep, tail = _split(argv)
    args = parser.parse_args(head)
    args.extra = tail if sep else []
    code: int = args.func(args)
    return code
