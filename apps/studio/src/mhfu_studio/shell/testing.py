# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Toolkit-free test doubles: a document and a workspace."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from mhfu_studio.shell.document import History
from mhfu_studio.shell.findings import Finding
from mhfu_studio.shell.workspace import Workspace

if TYPE_CHECKING:
    import moderngl

    from mhfu_studio.shell.viewport import Viewport


class FakeDocument:
    """A `Document` over a list of strings, with `History` undo and settable findings."""

    def __init__(self, path: Path | None = None, value: Sequence[str] = ()) -> None:
        self.path = path
        self.history: History[list[str]] = History(list(value))
        self.found: list[Finding] = []
        self.checks = 0
        self.saved_to: list[Path] = []

    @property
    def dirty(self) -> bool:
        return self.history.dirty

    def edit(self, item: str) -> None:
        self.history.edit(lambda v: v.append(item))

    def save(self, path: Path | None = None) -> Path:
        where = path or self.path
        if where is None:
            raise ValueError("no path to save to")
        self.path = where
        self.saved_to.append(where)
        self.history.saved()
        return where

    def findings(self) -> list[Finding]:
        self.checks += 1
        return list(self.found)

    def can_undo(self) -> bool:
        return self.history.can_undo()

    def can_redo(self) -> bool:
        return self.history.can_redo()

    def undo(self) -> None:
        self.history.undo()

    def redo(self) -> None:
        self.history.redo()


class FakeWorkspace(Workspace):
    """Opens files with `suffix` as a `FakeDocument`; its viewport is `make_viewport(ctx)`."""

    def __init__(
        self,
        name: str,
        suffix: str = ".txt",
        make_viewport: Callable[[moderngl.Context], Viewport] | None = None,
    ) -> None:
        self.name = name
        self.suffix = suffix
        self.filters = (f"{name} files", f"*{suffix}")
        self.make_viewport = make_viewport
        self.doc: FakeDocument | None = None
        self.vp: Viewport | None = None
        self.log: list[tuple[str, object]] = []

    @property
    def document(self) -> FakeDocument | None:
        return self.doc

    @property
    def viewport(self) -> Viewport | None:
        return self.vp

    def can_open(self, path: Path) -> bool:
        return path.suffix == self.suffix

    def open(self, path: Path) -> None:
        if not path.exists():
            raise FileNotFoundError(path)
        self.doc = FakeDocument(path, path.read_text().split())
        self.log.append(("open", path))

    def setup(self, ctx: moderngl.Context) -> Viewport:
        if self.make_viewport is None:
            from mhfu_studio.harness.selftest import Selftest

            self.make_viewport = Selftest
        self.vp = self.make_viewport(ctx)
        return self.vp

    def status(self) -> str:
        return f"{0 if self.doc is None else len(self.doc.history.value)} items"

    def reveal(self, target: Hashable, focus: str = "") -> None:
        self.log.append(("reveal", target))

    def refresh(self) -> None:
        self.log.append(("refresh", None))

    def close(self) -> None:
        if self.vp is not None:
            self.vp.release()
            self.vp = None
        self.log.append(("close", None))
