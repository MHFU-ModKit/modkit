# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""What the shell asks of a document, and snapshot undo for documents that are values."""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Generic, Protocol, TypeVar

from mhfu_studio.shell.findings import Finding

T = TypeVar("T")


class Document(Protocol):
    """An open file in a workspace: the shell's save, undo, dirty marker and findings list."""

    @property
    def path(self) -> Path | None: ...

    @property
    def dirty(self) -> bool:
        """Changed since it was opened or last saved."""
        ...

    def save(self, path: Path | None = None) -> Path:
        """Writes to `path`, or where it came from; returns where it went."""
        ...

    def findings(self) -> list[Finding]: ...

    def can_undo(self) -> bool: ...

    def can_redo(self) -> bool: ...

    def undo(self) -> None: ...

    def redo(self) -> None: ...


class History(Generic[T]):
    """Undo and redo over whole snapshots of `value`; `copy` must return an independent value."""

    def __init__(self, value: T, copy: Callable[[T], T] = copy.deepcopy, limit: int = 200) -> None:
        self._copy = copy
        self._limit = limit
        self._undo: list[tuple[T, int]] = []
        self._redo: list[tuple[T, int]] = []
        self._value = value
        self._version = 0
        self._next = 1
        self._saved = 0

    @property
    def value(self) -> T:
        """The current state; change it only through `commit` or `edit`."""
        return self._value

    def commit(self, value: T) -> None:
        """Makes `value` current, keeping the previous state for undo."""
        self._undo.append((self._value, self._version))
        del self._undo[: -self._limit]
        self._redo.clear()
        self._value, self._version = value, self._next
        self._next += 1

    def edit(self, change: Callable[[T], None]) -> None:
        """Runs `change` on a copy of the current state and commits it."""
        value = self._copy(self._value)
        change(value)
        self.commit(value)

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> None:
        if self._undo:
            self._redo.append((self._value, self._version))
            self._value, self._version = self._undo.pop()

    def redo(self) -> None:
        if self._redo:
            self._undo.append((self._value, self._version))
            self._value, self._version = self._redo.pop()

    @property
    def dirty(self) -> bool:
        return self._version != self._saved

    def saved(self) -> None:
        """Marks the current state as the one on disk."""
        self._saved = self._version
