# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The module registry: release-please-config.json with the `x-modkit` key per package."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

REPO: Final = "repo"
CONFIG: Final = "release-please-config.json"
MANIFEST: Final = ".release-please-manifest.json"

RELEASE_TYPES: Final = ("python", "simple")
_API: Final = ("python", "lua")
_KEYS: Final = ("paths", "depends", "api", "lua", "schemas", "qt")
_SCHEMA_KEYS: Final = ("name", "load", "dump", "corpus")


class RegistryError(ValueError):
    """The registry file is malformed."""


@dataclass(frozen=True)
class Schema:
    name: str
    load: str
    dump: str | None
    corpus: tuple[str, ...]


@dataclass(frozen=True)
class Module:
    name: str
    path: str
    release_type: str
    version: str
    paths: tuple[str, ...]  # path first, then x-modkit.paths
    depends: tuple[str, ...]
    api: tuple[str, ...]
    lua: tuple[str, ...]
    schemas: tuple[Schema, ...]
    qt: bool

    def tests(self, root: Path) -> Path:
        """The module's test directory."""
        return root / self.path / "tests"

    def packages(self, root: Path) -> tuple[str, ...]:
        """Import names under `<path>/src`."""
        src = root / self.path / "src"
        return tuple(sorted(p.parent.name for p in src.glob("*/__init__.py")))


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


@dataclass(frozen=True)
class Registry:
    root: Path
    modules: tuple[Module, ...]

    def get(self, name: str) -> Module:
        """The module called `name`; KeyError if there is none."""
        for module in self.modules:
            if module.name == name:
                return module
        raise KeyError(name)

    def scopes(self) -> tuple[str, ...]:
        """Valid commit scopes: module names and `repo`."""
        return (*(m.name for m in self.modules), REPO)

    def owner(self, path: str) -> Module | None:
        """The module whose longest path (own or extra) is a prefix of `path`."""
        best: tuple[int, Module] | None = None
        for module in self.modules:
            for prefix in module.paths:
                if _under(path, prefix) and (best is None or len(prefix) > best[0]):
                    best = (len(prefix), module)
        return best[1] if best else None

    def release_owner(self, path: str) -> Module | None:
        """The module release-please attributes `path` to: its own dir only."""
        best: Module | None = None
        for module in self.modules:
            if _under(path, module.path) and (best is None or len(module.path) > len(best.path)):
                best = module
        return best


def find_root(start: Path | None = None) -> Path:
    """The nearest ancestor of `start` (default: cwd) holding the registry file."""
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / CONFIG).is_file():
            return candidate
    raise RegistryError(f"no {CONFIG} above {here}")


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise RegistryError(f"{path.name}: {err}") from err
    if not isinstance(data, dict):
        raise RegistryError(f"{path.name}: not an object")
    return data


def _strings(where: str, value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise RegistryError(f"{where}: expected a list of strings")
    return tuple(value)


def _schema(where: str, raw: Any) -> Schema:
    if not isinstance(raw, dict):
        raise RegistryError(f"{where}: schema is not an object")
    unknown = sorted(set(raw) - set(_SCHEMA_KEYS))
    if unknown:
        raise RegistryError(f"{where}: unknown schema key {unknown[0]}")
    for key in ("name", "load", "corpus"):
        if key not in raw:
            raise RegistryError(f"{where}: schema lacks {key}")
    dump = raw.get("dump")
    if dump is not None and not isinstance(dump, str):
        raise RegistryError(f"{where}: dump is not a string")
    return Schema(
        name=str(raw["name"]),
        load=str(raw["load"]),
        dump=dump,
        corpus=_strings(f"{where}: corpus", raw["corpus"]),
    )


def _is_dir(root: Path, rel: str) -> bool:
    return (root / rel).is_dir()


def load(root: Path | None = None) -> Registry:
    """Read and validate the registry of `root` (default: found from the cwd)."""
    root = find_root() if root is None else root
    config = _read(root / CONFIG)
    manifest = _read(root / MANIFEST)
    packages = config.get("packages")
    if not isinstance(packages, dict):
        raise RegistryError(f"{CONFIG}: no packages")
    if set(manifest) != set(packages):
        diff = sorted(set(manifest) ^ set(packages))
        raise RegistryError(f"{MANIFEST} and {CONFIG} disagree on: {', '.join(diff)}")

    modules: list[Module] = []
    seen: set[str] = set()
    for path, entry in packages.items():
        if not isinstance(entry, dict):
            raise RegistryError(f"{path}: not an object")
        name = entry.get("component")
        if not isinstance(name, str) or not name:
            raise RegistryError(f"{path}: missing component")
        if name == REPO:
            raise RegistryError(f"{path}: component may not be {REPO!r}")
        if name in seen:
            raise RegistryError(f"{path}: duplicate component {name}")
        seen.add(name)
        release_type = entry.get("release-type")
        if release_type not in RELEASE_TYPES:
            raise RegistryError(f"{path}: release-type must be one of {', '.join(RELEASE_TYPES)}")
        if not _is_dir(root, path):
            raise RegistryError(f"{path}: not a directory")
        if release_type == "python" and not (root / path / "pyproject.toml").is_file():
            raise RegistryError(f"{path}: python module without pyproject.toml")

        extra = entry.get("x-modkit", {})
        if not isinstance(extra, dict):
            raise RegistryError(f"{path}: x-modkit is not an object")
        unknown = sorted(set(extra) - set(_KEYS))
        if unknown:
            raise RegistryError(f"{path}: unknown x-modkit key {unknown[0]}")
        extra_paths = _strings(f"{path}: paths", extra.get("paths", []))
        for p in extra_paths:
            if not _is_dir(root, p):
                raise RegistryError(f"{path}: x-modkit path {p} is not a directory")
        api = _strings(f"{path}: api", extra.get("api", []))
        bad_api = sorted(set(api) - set(_API))
        if bad_api:
            raise RegistryError(f"{path}: unknown api {bad_api[0]}")
        lua = _strings(f"{path}: lua", extra.get("lua", []))
        if "lua" in api and not lua:
            raise RegistryError(f"{path}: api lua needs x-modkit.lua")
        for p in lua:
            if not _is_dir(root, p):
                raise RegistryError(f"{path}: lua dir {p} is not a directory")
        schemas = tuple(_schema(path, s) for s in extra.get("schemas", []))
        qt = extra.get("qt", False)
        if not isinstance(qt, bool):
            raise RegistryError(f"{path}: qt is not a bool")
        modules.append(
            Module(
                name=name,
                path=path,
                release_type=release_type,
                version=str(manifest[path]),
                paths=(path, *extra_paths),
                depends=_strings(f"{path}: depends", extra.get("depends", [])),
                api=api,
                lua=lua,
                schemas=schemas,
                qt=qt,
            )
        )
    for module in modules:
        for dep in module.depends:
            if dep not in seen:
                raise RegistryError(f"{module.path}: depends on unknown module {dep}")
    return Registry(root=root, modules=tuple(modules))


def _cmd(args: argparse.Namespace) -> int:
    try:
        reg = load()
    except RegistryError as err:
        print(f"registry: {err}")
        return 1
    if args.json:
        rows = [
            {
                "name": m.name,
                "path": m.path,
                "release_type": m.release_type,
                "version": m.version,
                "paths": list(m.paths),
                "depends": list(m.depends),
                "api": list(m.api),
                "lua": list(m.lua),
                "schemas": [
                    {"name": s.name, "load": s.load, "dump": s.dump, "corpus": list(s.corpus)}
                    for s in m.schemas
                ],
                "qt": m.qt,
            }
            for m in reg.modules
        ]
        print(json.dumps(rows, indent=2))
    else:
        for m in reg.modules:
            print(f"{m.name:14} {m.path:24} {m.release_type:7} {m.version}")
    return 0


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add the `registry` command."""
    p = sub.add_parser("registry", help="print the module registry")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd)
