# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Python API breaks and missing docstrings, via griffe (stub)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import griffe

from modkit_ci.findings import Finding
from modkit_ci.registry import Module


def check(module: Module, base_root: Path, head_root: Path) -> list[Finding]:
    """Breaks and doc findings of `module` between two trees."""
    raise NotImplementedError


def public_objects(root: Path, module: Module) -> Iterator[tuple[str, griffe.Object, bool]]:
    """Public objects as (path, object, has_docstring)."""
    raise NotImplementedError
