# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Lua API model and its break and doc rules (stub)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from modkit_ci.findings import Finding
from modkit_ci.registry import Module


@dataclass(frozen=True)
class Param:
    name: str
    type: str
    optional: bool
    desc: str


@dataclass(frozen=True)
class Func:
    name: str
    params: tuple[Param, ...]
    returns: tuple[str, ...]
    desc: str
    examples: tuple[str, ...]
    file: str
    line: int


@dataclass(frozen=True)
class Field:
    name: str
    type: str
    optional: bool
    desc: str


@dataclass(frozen=True)
class Class:
    name: str
    fields: tuple[Field, ...]
    desc: str
    file: str
    line: int


@dataclass(frozen=True)
class Const:
    name: str
    value: str
    desc: str
    file: str
    line: int


@dataclass(frozen=True)
class LuaApi:
    funcs: tuple[Func, ...]
    consts: tuple[Const, ...]
    classes: tuple[Class, ...]


def model(doc: list[dict[str, Any]], root: Path, dirs: Sequence[str]) -> LuaApi:
    """The entries of LuaLS doc JSON defined under `dirs`, sorted by (file, line)."""
    raise NotImplementedError


def load(root: Path, dirs: Sequence[str]) -> LuaApi:
    """LuaLS doc of `root` through `model`."""
    raise NotImplementedError


def check(module: Module, base_root: Path, head_root: Path) -> list[Finding]:
    """Lua breaks and doc findings of `module` between two trees."""
    raise NotImplementedError
