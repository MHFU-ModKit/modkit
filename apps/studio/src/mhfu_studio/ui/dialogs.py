# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The window's questions: native file dialogs and message boxes. Tests replace these."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from mhfu_studio.shell import places
from mhfu_studio.shell.studio import doc_name
from mhfu_studio.shell.text import plain

if TYPE_CHECKING:
    from mhfu_studio.shell.studio import Studio
    from mhfu_studio.shell.workspace import Workspace

Answer = Literal["save", "discard", "cancel"]
ALL = "All files (*)"


def filters(workspaces: Iterable[Workspace]) -> str:
    """Their filter pairs ("Map document", "map.toml", ...) as one Qt filter."""
    out: list[str] = []
    for w in workspaces:
        pairs: Sequence[str] = w.filters
        for name, pattern in zip(pairs[::2], pairs[1::2], strict=True):
            f = f"{name} ({pattern})"
            if f not in out and f != ALL:
                out.append(f)
    return ";;".join([*out, ALL])


def _folder(studio: Studio) -> str:
    doc = studio.active.document
    path = None if doc is None else doc.path
    return str(path.parent if path is not None else Path.cwd())


def ask_open(parent: QWidget, studio: Studio) -> Path | None:
    """Any workspace's document; the active workspace's kind is offered first."""
    got, _ = QFileDialog.getOpenFileName(
        parent,
        "Open a document",
        _folder(studio),
        filters([studio.active, *studio.workspaces]),
    )
    return Path(got) if got else None


def open_document(parent: QWidget, studio: Studio) -> None:
    """Asks for a document and opens it (`Studio.open_later`); says why when it did not open
    (a cancel is silent, a wait shows on the start page)."""
    path = ask_open(parent, studio)
    if path is not None and not studio.open_later(path) and studio.opening is None:
        if studio.message:
            warn(parent, "Not opened", plain(studio.message))


def ask_folder(parent: QWidget, place: places.Place) -> Path | None:
    """A folder for `place`, the dialog starting where it is now."""
    now = places.find(place).path
    got = QFileDialog.getExistingDirectory(
        parent, f"Choose the {place.name}", str(now if now is not None else Path.home())
    )
    return Path(got) if got else None


def ask_save_as(parent: QWidget, ws: Workspace) -> Path | None:
    doc = ws.document
    start = str(doc.path if doc is not None and doc.path is not None else Path.cwd())
    got, _ = QFileDialog.getSaveFileName(parent, f"Save {doc_name(ws)} as", start, filters([ws]))
    return Path(got) if got else None


def confirm_unsaved(parent: QWidget, names: Sequence[str]) -> Answer:
    """Save, discard or cancel for every document in `names` at once."""
    b = QMessageBox.StandardButton
    got = QMessageBox.question(
        parent,
        "Unsaved edits",
        f"Save the edits to {', '.join(names)} first? Edits you don't save are lost.",
        b.Save | b.Discard | b.Cancel,
        b.Save,
    )
    return "save" if got == b.Save else "discard" if got == b.Discard else "cancel"


def revert_box(parent: QWidget, name: str) -> QMessageBox:
    """Revert or Cancel for `name`; Cancel is the default, so Enter never discards."""
    b = QMessageBox.StandardButton
    box = QMessageBox(
        QMessageBox.Icon.Question,
        "Revert",
        f"Discard the unsaved edits to {name} and go back to the saved file?",
        b.Cancel,
        parent,
    )
    box.addButton("Revert", QMessageBox.ButtonRole.DestructiveRole)
    box.setDefaultButton(b.Cancel)
    box.setEscapeButton(b.Cancel)
    return box


def confirm_revert(parent: QWidget, name: str) -> bool:
    box = revert_box(parent, name)
    box.exec()
    got = box.clickedButton()
    box.deleteLater()
    return got is not None and box.buttonRole(got) == QMessageBox.ButtonRole.DestructiveRole


def warn(parent: QWidget, title: str, text: str) -> None:
    QMessageBox.warning(parent, title, text)
