# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A species' brain file open for editing, and the builds of its rows. No toolkit here.

    doc = SpeciesDocument.for_species(76, brain.rows(game))   # its overlay's file, or an empty one
    doc.edit(lambda bf: ...)                                   # one undo step
    builds = Builds(doc)                                       # on the brain worker:
    built = builds.get(76, lift, intel)                        # cached per (row, edit version)

The document is the shell's `Document` over `mhfu.em.script.brainfile`. An overlay with no file
yet starts empty in memory: an empty file builds the game's own bytes, so there is nothing to
start, and the first save makes the file.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from mhfu.em.script import brainfile, build
from mhfu.em.script.brainfile import BrainFile, BrainFileError
from mhfu.em.script.build import Built
from mhfu.em.script.editset import EditSet, equal

from mhfu_studio.monster.brain import NoScript
from mhfu_studio.monster.species import NAMES
from mhfu_studio.shell import places
from mhfu_studio.shell.document import Document, History
from mhfu_studio.shell.findings import Finding

if TYPE_CHECKING:
    from mhfu.em.intel import SpeciesIntel
    from mhfu.em.script import ir


def overlay_of(row: int, serves: Mapping[int, int]) -> int:
    """The em overlay whose file holds species row `row`'s edits; `serves` is `brain.rows(game)`.
    `NoScript` for a row no overlay serves."""
    try:
        return serves[row]
    except KeyError:
        raise NoScript(f"no overlay serves script row {row}") from None


class SpeciesDocument:
    """Every edit is one undo step and must leave a file that checks (`BrainFile.check`): `edit`
    refuses the change otherwise. Use it from the GUI thread; a worker reads `snapshot`."""

    def __init__(self, bf: BrainFile, path: Path | None = None) -> None:
        self._history = History(bf, BrainFile.copy)
        self._path = path
        self._saved = bf.copy()
        self._version = 0
        self._seen = (0, bf)

    @classmethod
    def open(cls, path: str | Path) -> SpeciesDocument:
        return cls(brainfile.load(path), Path(path))

    @classmethod
    def for_overlay(cls, overlay: int, given: str | Path | None = None) -> SpeciesDocument:
        """The overlay's file in the brains folder (`places.brain_file`); with none there, an
        empty document with that path."""
        path = places.brain_file(overlay, given)
        if path.is_file():
            return cls.open(path)
        return cls(brainfile.new(overlay, NAMES.get(overlay, "")), path)

    @classmethod
    def for_species(
        cls, row: int, serves: Mapping[int, int], given: str | Path | None = None
    ) -> SpeciesDocument:
        """`for_overlay` of the overlay that serves `row`."""
        return cls.for_overlay(overlay_of(row, serves), given)

    @property
    def brainfile(self) -> BrainFile:
        """The current state; change it only through `edit`."""
        return self._history.value

    @property
    def overlay(self) -> int:
        return self.brainfile.overlay

    @property
    def version(self) -> int:
        """Counts up with every change of state (edit, undo, redo, revert), never back: a state
        reached again by undo has a new number. Saving keeps it."""
        return self._seen[0]

    def snapshot(self) -> tuple[int, BrainFile]:
        """The version and its file, read as one; that file is never changed again."""
        return self._seen

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def dirty(self) -> bool:
        return self._history.dirty

    def edit(self, change: Callable[[BrainFile], object]) -> None:
        """Runs `change` on a copy and commits it; raises `BrainFileError` and keeps the current
        state when the result is no file."""
        bf = self.brainfile.copy()
        change(bf)
        bf.check()
        self._history.commit(bf)
        self._publish()

    def save(self, path: Path | None = None) -> Path:
        target = path if path is not None else self._path
        if target is None:
            raise BrainFileError("the brain file has no path to save to")
        self._path = brainfile.save(self.brainfile, target)
        self._saved = self.brainfile.copy()
        self._history.saved()
        return self._path

    def revert(self) -> None:
        """Back to the file as last opened or saved, as one more undo step."""
        if self.dirty:
            self._history.commit(self._saved.copy())
            self._history.saved()
            self._publish()

    def findings(self) -> list[Finding]:
        """File-level problems: what would not save and read back. Builds are `Builds`'."""
        try:
            brainfile.loads(brainfile.dumps(self.brainfile))
        except (ValueError, TypeError) as e:
            return [Finding("error", "BRAIN_FILE", str(e), self._path.name if self._path else "")]
        return []

    def can_undo(self) -> bool:
        return self._history.can_undo()

    def can_redo(self) -> bool:
        return self._history.can_redo()

    def undo(self) -> None:
        self._history.undo()
        self._publish()

    def redo(self) -> None:
        self._history.redo()
        self._publish()

    def _publish(self) -> None:
        self._version += 1
        self._seen = (self._version, self.brainfile)


def _is_document(d: SpeciesDocument) -> Document:
    """mypy checks here that `SpeciesDocument` is a shell `Document`."""
    return d


@dataclass(frozen=True)
class _Entry:
    version: int
    lift: ir.Program
    intel: SpeciesIntel | None
    edits: EditSet | None
    """The file's edits for the row at that version; None when it had none."""
    built: Built


def _same(a: EditSet | None, b: EditSet | None) -> bool:
    return a is b or (a is not None and b is not None and equal(a, b))


class Builds:
    """The builds of a document's rows: `get` on one worker, `latest` and `current` from the GUI.

    One entry per row, the newest. A change of the document rebuilds a row only when that row's
    own edits changed, so an edit of another row is a hit, and the GUI shows `latest` while the
    next build computes."""

    def __init__(self, doc: SpeciesDocument) -> None:
        self._doc = doc
        self._lock = threading.Lock()
        self._rows: dict[int, _Entry] = {}

    def get(self, row: int, lift: ir.Program, intel: SpeciesIntel | None = None) -> Built:
        """Row `row` as the document is now, through `build.compile`: `lift` is its game's
        program and `intel` its overlay's species intel (adds PAIR_UNKNOWN). Computes unless that
        build is held (em75: tens of ms, em01: about two seconds). Two callers for one row both
        compute."""
        version, bf = self._doc.snapshot()
        held = self._held(row, version, bf)
        if held is not None and held.lift is lift and held.intel is intel:
            return held.built
        built = build.compile(bf, None, row, lift=lift, intel=intel)
        self._keep(row, _Entry(version, lift, intel, bf.edits(row), built))
        return built

    def latest(self, row: int) -> Built | None:
        """The newest build of the row, of whatever version; None before the first."""
        with self._lock:
            held = self._rows.get(row)
        return None if held is None else held.built

    def current(self, row: int) -> Built | None:
        """`latest` when it is the build of the row's edits as they are now, else None (`get` is
        due)."""
        held = self._held(row, *self._doc.snapshot())
        return None if held is None else held.built

    def _held(self, row: int, version: int, bf: BrainFile) -> _Entry | None:
        """The row's entry when it is the build of `bf`'s edits for the row; a later version
        with the same edits is the same build, and moves the entry up to it."""
        with self._lock:
            held = self._rows.get(row)
        if held is None:
            return None
        if held.version != version:
            es = bf.edits(row)
            if not _same(held.edits, es):
                return None
            held = _Entry(version, held.lift, held.intel, es, held.built)
            self._keep(row, held)
        return held

    def _keep(self, row: int, entry: _Entry) -> None:
        """Never replaces a newer entry with an older one."""
        with self._lock:
            held = self._rows.get(row)
            if held is None or held.version <= entry.version:
                self._rows[row] = entry
