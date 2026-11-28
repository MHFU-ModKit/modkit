# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Backward-compat corpus check of the module's document schemas (stub)."""

from __future__ import annotations

from pathlib import Path

from modkit_ci.findings import Finding
from modkit_ci.registry import Module


def check(module: Module, base_root: Path, head_root: Path) -> list[Finding]:
    """Base corpus files that no longer load or round-trip at head."""
    raise NotImplementedError
