# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""One pytest process per test unit."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from modkit_ci.affected import Unit, all_units
from modkit_ci.coverage import floor
from modkit_ci.registry import Registry, load


def pytest_argv(
    unit: Unit,
    *,
    cov: bool = False,
    no_qt: bool = False,
    extra: Sequence[str] = (),
    root: Path = Path(),
) -> list[str]:
    """The pytest command line for one unit; the floor is passed so no addopts can lower it."""
    argv = [sys.executable, "-m", "pytest", unit.tests]
    if cov and unit.packages and not (no_qt and unit.qt):
        argv += [f"--cov={pkg}" for pkg in unit.packages]
        if unit.pyproject:
            argv.append(f"--cov-config={unit.pyproject}")
        argv.append("--cov-report=term-missing")
        bar = floor((root / unit.pyproject).read_text(encoding="utf-8")) if unit.pyproject else None
        if bar is not None:
            argv.append(f"--cov-fail-under={bar:g}")
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
            argv = pytest_argv(unit, cov=cov, no_qt=no_qt, extra=extra, root=registry.root)
            if subprocess.run(argv, cwd=registry.root, check=False).returncode != 0:
                failed.append(name)
    if failed:
        print("test: failed: " + " ".join(failed), file=sys.stderr)
    return 1 if failed else 0


def _cmd(args: argparse.Namespace) -> int:
    return run(load(), args.units, cov=args.cov, no_qt=args.no_qt, extra=args.extra)


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add the `test` command."""
    p = sub.add_parser("test", help="run test units")
    p.add_argument("units", nargs="+")
    p.add_argument("--cov", action="store_true")
    p.add_argument("--no-qt", action="store_true")
    p.set_defaults(func=_cmd)
