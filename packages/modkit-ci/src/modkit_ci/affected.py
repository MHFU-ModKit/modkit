# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Changed files -> affected modules and test units."""

from __future__ import annotations

import argparse
import json
import os
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from modkit_ci import git
from modkit_ci.registry import Module, Registry, load


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


def _lock_packages(lock: Path) -> tuple[set[str], list[dict[str, Any]]]:
    data = tomllib.loads(lock.read_text(encoding="utf-8"))
    members = set(data.get("manifest", {}).get("members", []))
    return members, [p for p in data.get("package", []) if p.get("name") in members]


def workspace(registry: Registry, lock: Path) -> dict[str, tuple[str, frozenset[str]]]:
    """uv name -> (editable path, workspace dependencies) from uv.lock."""
    members, packages = _lock_packages(lock)
    result: dict[str, tuple[str, frozenset[str]]] = {}
    for pkg in packages:
        source = pkg.get("source", {})
        path = source.get("editable") or source.get("virtual") or ""
        groups = [pkg.get("dependencies", [])]
        groups += list(pkg.get("optional-dependencies", {}).values())
        groups += list(pkg.get("dev-dependencies", {}).values())
        deps = {d["name"] for group in groups for d in group if d["name"] in members}
        result[pkg["name"]] = (path, frozenset(deps - {pkg["name"]}))
    return result


def _unit(registry: Registry, module: Module) -> Unit:
    root = registry.root
    pyproject = f"{module.path}/pyproject.toml"
    return Unit(
        name=module.name,
        path=module.path,
        tests=f"{module.path}/tests",
        packages=module.packages(root),
        pyproject=pyproject if (root / pyproject).is_file() else None,
        qt=module.qt,
    )


def _member_unit(registry: Registry, name: str, path: str) -> Unit:
    root = registry.root
    pyproject = f"{path}/pyproject.toml"
    src = root / path / "src"
    return Unit(
        name=name,
        path=path,
        tests=f"{path}/tests",
        packages=tuple(sorted(p.parent.name for p in src.glob("*/__init__.py"))),
        pyproject=pyproject if (root / pyproject).is_file() else None,
        qt=False,
    )


def all_units(registry: Registry, lock: Path) -> tuple[Unit, ...]:
    """Every module plus every workspace member that is not a module."""
    units = [_unit(registry, m) for m in registry.modules]
    module_paths = {m.path for m in registry.modules}
    for name, (path, _deps) in sorted(workspace(registry, lock).items()):
        if path and path not in module_paths:
            units.append(_member_unit(registry, name, path))
    return tuple(units)


def _dependents(registry: Registry, lock: Path) -> dict[str, set[str]]:
    """Module name -> modules that depend on it, directly."""
    by_path = {m.path: m.name for m in registry.modules}
    result: dict[str, set[str]] = {m.name: set() for m in registry.modules}
    ws = workspace(registry, lock)
    for path, deps in ws.values():
        if path not in by_path:
            continue
        for dep in deps:
            dep_path = ws[dep][0]
            if dep_path in by_path:
                result[by_path[dep_path]].add(by_path[path])
    for module in registry.modules:
        for dep in module.depends:
            result[dep].add(module.name)
    return result


def compute(registry: Registry, files: Iterable[str], lock: Path) -> Affected:
    """Owners of `files` plus their dependents; any unowned file means all."""
    changed = frozenset(files)
    owners = {f: registry.owner(f) for f in changed}
    if any(owner is None for owner in owners.values()):
        return everything(registry, lock, changed)
    dependents = _dependents(registry, lock)
    todo = [o.name for o in owners.values() if o is not None]
    modules: set[str] = set()
    while todo:
        name = todo.pop()
        if name not in modules:
            modules.add(name)
            todo.extend(dependents[name])
    units = tuple(_unit(registry, m) for m in registry.modules if m.name in modules)
    return Affected(False, changed, frozenset(modules), units)


def everything(registry: Registry, lock: Path, changed: frozenset[str] = frozenset()) -> Affected:
    """The result when every unit runs."""
    return Affected(
        True,
        changed,
        frozenset(m.name for m in registry.modules),
        all_units(registry, lock),
    )


def _cmd(args: argparse.Namespace) -> int:
    registry = load()
    lock = registry.root / "uv.lock"
    if args.all:
        result = everything(registry, lock)
    else:
        result = compute(registry, git.changed_files(args.base), lock)
    modules = sorted(result.modules)
    units = sorted(u.name for u in result.units)
    if args.output == "json":
        print(json.dumps({"all": result.all, "modules": modules, "units": units}))
    elif args.output == "github":
        flag = "true" if result.all else "false"
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as out:
            out.write(f"all={flag}\nmodules={json.dumps(modules)}\nunits={json.dumps(units)}\n")
    else:
        print(f"all: {str(result.all).lower()}")
        print("modules: " + " ".join(modules))
        print("units: " + " ".join(units))
    return 0


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add the `affected` command."""
    p = sub.add_parser("affected", help="modules and units a change touches")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--base")
    group.add_argument("--all", action="store_true")
    p.add_argument("--output", choices=["text", "json", "github"], default="text")
    p.set_defaults(func=_cmd)
