# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The `modkit-ci` command line: every module registers its commands."""

from __future__ import annotations

import argparse
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
    testrun,
)

_MODULES = (registry, affected, testrun, coverage, commits, api, luals, docs, bundle)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command named on the command line; its exit code."""
    parser = argparse.ArgumentParser(prog="modkit-ci", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for module in _MODULES:
        module.register(sub)
    args = parser.parse_args(argv)
    code: int = args.func(args)
    return code
