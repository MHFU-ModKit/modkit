# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Backward-compat corpus check of the module's document schemas."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from modkit_ci.findings import Finding
from modkit_ci.registry import Module, Schema


def resolve(spec: str) -> Callable[..., Any]:
    """The callable named `module:qualname`."""
    name, _, qual = spec.partition(":")
    obj: Any = importlib.import_module(name)
    for part in qual.split("."):
        obj = getattr(obj, part)
    return obj  # type: ignore[no-any-return]  # the config names a callable


def _corpus(schema: Schema, root: Path) -> list[Path]:
    return sorted({p for pattern in schema.corpus for p in root.glob(pattern) if p.is_file()})


def _check_schema(module: Module, schema: Schema, base_root: Path) -> list[Finding]:
    try:
        load = resolve(schema.load)
        dump = resolve(schema.dump) if schema.dump else None
    except (ImportError, AttributeError) as e:
        return [Finding(module.name, "break", f"{schema.name}: loader is gone: {e}")]
    out: list[Finding] = []
    for file in _corpus(schema, base_root):
        rel = file.relative_to(base_root).as_posix()
        try:
            loaded = load(file)
        except Exception as e:  # noqa: BLE001  # any loader failure is the finding
            msg = f"{schema.name}: {rel} no longer loads: {type(e).__name__}: {e}"
            out.append(Finding(module.name, "break", msg, rel))
            continue
        if dump is not None and dump(loaded) != file.read_text(encoding="utf-8"):
            msg = f"{schema.name}: {rel} does not round-trip byte-identical"
            out.append(Finding(module.name, "break", msg, rel))
    return out


def check(module: Module, base_root: Path, head_root: Path) -> list[Finding]:
    """Base corpus files that no longer load or round-trip at head."""
    return [f for s in module.schemas for f in _check_schema(module, s, base_root)]
