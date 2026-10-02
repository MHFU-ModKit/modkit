# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""What a check found in a document: one type for every workspace and command."""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from dataclasses import dataclass
from typing import Literal

Level = Literal["error", "warning", "info"]
LEVELS: tuple[Level, ...] = ("error", "warning", "info")


@dataclass(frozen=True)
class Finding:
    """`where` is for people ("clip 12", "st098 group 4"); `target` lets a workspace jump to it,
    and `focus` names the control there that fixes it, a key the workspace's panels land on.
    `fix` says what to do when no control does."""

    level: Level
    code: str
    message: str
    where: str = ""
    target: Hashable | None = None
    focus: str = ""
    fix: str = ""

    def __str__(self) -> str:
        at = f" {self.where}:" if self.where else ""
        return f"{self.level}{at} {self.message} [{self.code}]"


def worst(findings: Iterable[Finding]) -> Level | None:
    """The most severe level present, or None when there are no findings."""
    present = {f.level for f in findings}
    return next((lv for lv in LEVELS if lv in present), None)
