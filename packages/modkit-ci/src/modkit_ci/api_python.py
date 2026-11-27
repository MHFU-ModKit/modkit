# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Python API breaks and missing docstrings, via griffe."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import griffe

from modkit_ci.findings import Finding
from modkit_ci.registry import Module


def _load(root: Path, module: Module, package: str) -> griffe.Module:
    loaded = griffe.load(package, search_paths=[root / module.path / "src"], allow_inspection=False)
    assert isinstance(loaded, griffe.Module)
    return loaded


def _try_load(root: Path, module: Module, package: str) -> griffe.Module | None:
    if package not in module.packages(root):
        return None
    return _load(root, module, package)


def _walk(obj: griffe.Object) -> Iterator[griffe.Object]:
    """Public members below `obj`, depth first; private containers are not entered."""
    for name, member in sorted(obj.members.items()):
        if member.is_alias or (name.startswith("__") and name.endswith("__")):
            continue
        if not member.is_public:
            continue
        assert isinstance(member, griffe.Object)
        yield member
        yield from _walk(member)


def _has_docstring(obj: griffe.Object) -> bool:
    return obj.docstring is not None and bool(obj.docstring.value.strip())


def _objects(top: griffe.Module) -> Iterator[tuple[str, griffe.Object, bool]]:
    for obj in (top, *_walk(top)):
        yield obj.path, obj, _has_docstring(obj)


def public_objects(root: Path, module: Module) -> Iterator[tuple[str, griffe.Object, bool]]:
    """Public objects as (path, object, has_docstring), packages included."""
    for package in module.packages(root):
        yield from _objects(_load(root, module, package))


def _where(obj: griffe.Object, head_root: Path) -> tuple[str | None, int | None]:
    """The object's place in the head tree; none for an object that only the base has."""
    path = obj.filepath if isinstance(obj.filepath, Path) else None
    if path is None or not path.resolve().is_relative_to(head_root.resolve()):
        return None, None
    return path.resolve().relative_to(head_root.resolve()).as_posix(), obj.lineno


def _describe(b: griffe.Breakage) -> str:
    return f"{b.obj.path}: {b.kind.value}" + (f" ({b.details})" if b.details else "")


def check(module: Module, base_root: Path, head_root: Path) -> list[Finding]:
    """Breaks and doc findings of `module` between two trees."""
    out: list[Finding] = []
    for package in sorted({*module.packages(base_root), *module.packages(head_root)}):
        old = _try_load(base_root, module, package)
        new = _try_load(head_root, module, package)
        if new is None:
            out.append(Finding(module.name, "break", f"package {package} was removed"))
            continue
        before = {p: d for p, _, d in _objects(old)} if old else {}
        if old:
            for b in griffe.find_breaking_changes(old, new):
                path, line = _where(b.obj, head_root)
                out.append(Finding(module.name, "break", _describe(b), path, line))
        for obj_path, obj, documented in _objects(new):
            if documented:
                continue
            if obj_path not in before:
                msg = f"{obj_path}: new public object without a docstring"
            elif before[obj_path]:
                msg = f"{obj_path}: docstring removed"
            else:
                continue
            path, line = _where(obj, head_root)
            out.append(Finding(module.name, "doc", msg, path, line))
    return out
