# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Changed files -> affected modules and test units (stub)."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from modkit_ci.registry import Registry, load


@dataclass(frozen=True)
class Unit:
    name: str
    path: str
    tests: str
    packages: tuple[str, ...]
    pyproject: str | None
    qt: bool


@dataclass(frozen=True)
class Affected:
    all: bool
    changed: frozenset[str]
    modules: frozenset[str]
    units: tuple[Unit, ...]


def compute(registry: Registry, files: Iterable[str], lock: Path) -> Affected:
    """Owners of `files` plus their dependents; any unowned file means all."""
    raise NotImplementedError


def workspace(registry: Registry, lock: Path) -> dict[str, tuple[str, frozenset[str]]]:
    """uv name -> (editable path, workspace dependencies) from uv.lock."""
    raise NotImplementedError


def _cmd(args: argparse.Namespace) -> int:
    # stub: everything is affected until the real computation lands
    names = sorted(m.name for m in load().modules)
    if args.output == "json":
        print(json.dumps({"all": True, "modules": names, "units": names}))
    elif args.output == "github":
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as out:
            out.write(f"all=true\nmodules={json.dumps(names)}\nunits={json.dumps(names)}\n")
    else:
        print("all\n" + "\n".join(names))
    return 0


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add the `affected` command."""
    p = sub.add_parser("affected", help="modules and units a change touches")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--base")
    group.add_argument("--all", action="store_true")
    p.add_argument("--output", choices=["text", "json", "github"], default="text")
    p.set_defaults(func=_cmd)
