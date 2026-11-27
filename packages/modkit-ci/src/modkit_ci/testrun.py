# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""One pytest process per test unit."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence

from modkit_ci.affected import Unit, all_units
from modkit_ci.registry import Registry, load


def pytest_argv(
    unit: Unit, *, cov: bool = False, no_qt: bool = False, extra: Sequence[str] = ()
) -> list[str]:
    """The pytest command line for one unit."""
    argv = [sys.executable, "-m", "pytest", unit.tests]
    if cov and unit.packages and not (no_qt and unit.qt):
        argv += [f"--cov={pkg}" for pkg in unit.packages]
        if unit.pyproject:
            argv.append(f"--cov-config={unit.pyproject}")
        argv.append("--cov-report=term-missing")
    return [*argv, *extra]


def run(
    registry: Registry,
    names: Sequence[str],
    *,
    cov: bool = False,
    no_qt: bool = False,
    extra: Sequence[str] = (),
) -> int:
    """Run the named units one after another; 1 if any failed or is unknown."""
    units = {u.name: u for u in all_units(registry, registry.root / "uv.lock")}
    failed: list[str] = []
    for name in names:
        unit = units.get(name)
        if unit is None:
            print(f"test: unknown unit {name}", file=sys.stderr)
            failed.append(name)
        elif not (registry.root / unit.tests).is_dir():
            print(f"test: {name}: no tests directory, skipped")
        else:
            print(f"test: {name}", flush=True)
            argv = pytest_argv(unit, cov=cov, no_qt=no_qt, extra=extra)
            if subprocess.run(argv, cwd=registry.root, check=False).returncode != 0:
                failed.append(name)
    if failed:
        print("test: failed: " + " ".join(failed), file=sys.stderr)
    return 1 if failed else 0


def _partition(rest: list[str]) -> tuple[list[str], str, list[str]]:
    if "--" not in rest:
        return rest, "", []
    i = rest.index("--")
    return rest[:i], "--", rest[i + 1 :]


def _cmd(args: argparse.Namespace) -> int:
    # REMAINDER swallows the options written after the units: take ours back out
    rest: list[str] = args.pytest_args
    head, _, tail = _partition(rest)
    cov, no_qt = args.cov or "--cov" in head, args.no_qt or "--no-qt" in head
    unknown = [a for a in head if a not in ("--cov", "--no-qt")]
    if unknown:
        print(f"test: unexpected {' '.join(unknown)} (pytest options go after --)", file=sys.stderr)
        return 2
    return run(load(), args.units, cov=cov, no_qt=no_qt, extra=tail)


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add the `test` command."""
    p = sub.add_parser("test", help="run test units")
    p.add_argument("units", nargs="+")
    p.add_argument("--cov", action="store_true")
    p.add_argument("--no-qt", action="store_true")
    p.add_argument("pytest_args", nargs=argparse.REMAINDER)
    p.set_defaults(func=_cmd)
