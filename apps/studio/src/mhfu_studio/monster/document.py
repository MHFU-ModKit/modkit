# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A port manifest open for editing: the shell's `Document` over `mhfu_port.manifest`."""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from mhfu_port import manifest
from mhfu_port.manifest import Manifest

from mhfu_studio.shell.document import Document, History
from mhfu_studio.shell.findings import Finding

if TYPE_CHECKING:
    from mhfu.em.intel import SpeciesIntel


class PortDocument:
    """Every edit is one undo step and must leave a manifest that loads: `edit` runs the loader's
    checks and refuses the change otherwise. `pac` and `intel` are the evidence `findings`
    checks against; the workspace sets them when it has them."""

    def __init__(self, m: Manifest, path: Path | None = None) -> None:
        self._history = History(m)
        self._path = path if path is not None else m.path
        self._saved = copy.deepcopy(m)
        self.pac: bytes | None = None
        """The built model PAC the clips and bones are checked against."""
        self.sources: dict[int, int] = {}
        """Its layout: entry -> MHP3rd clip id; empty: the manifest's pins."""
        self.intel: SpeciesIntel | None = None
        """The host species' intel."""

    @classmethod
    def open(cls, path: str | Path) -> PortDocument:
        return cls(manifest.load(path), Path(path))

    @property
    def manifest(self) -> Manifest:
        """The current state; change it only through `edit`."""
        return self._history.value

    @property
    def saved_manifest(self) -> Manifest:
        """The state on disk, as last opened or saved."""
        return self._saved

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def dirty(self) -> bool:
        return self._history.dirty

    def edit(self, change: Callable[[Manifest], object]) -> None:
        """Runs `change` on a copy and commits it; raises `ManifestError` and keeps the current
        state when the result would not load."""
        m = copy.deepcopy(self.manifest)
        change(m)
        manifest.check(m)
        self._history.commit(m)

    def save(self, path: Path | None = None) -> Path:
        target = manifest.save(self.manifest, path if path is not None else self._path)
        self._path = target
        self._saved = copy.deepcopy(self.manifest)
        self._history.saved()
        return target

    def revert(self) -> None:
        """Back to the file on disk, as one more undo step."""
        if self.dirty:
            self._history.commit(copy.deepcopy(self._saved))
            self._history.saved()

    def findings(self) -> list[Finding]:
        from mhfu_studio.monster.validate import validate

        return validate(self.manifest, pac=self.pac, intel=self.intel, sources=self.sources)

    def can_undo(self) -> bool:
        return self._history.can_undo()

    def can_redo(self) -> bool:
        return self._history.can_redo()

    def undo(self) -> None:
        self._history.undo()

    def redo(self) -> None:
        self._history.redo()


def _is_document(d: PortDocument) -> Document:
    """mypy checks here that `PortDocument` is a shell `Document`."""
    return d
