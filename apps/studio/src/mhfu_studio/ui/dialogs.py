# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The window's questions: native file dialogs and message boxes. Tests replace these."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from mhfu_studio.shell.studio import doc_name
from mhfu_studio.shell.widgets import plain

if TYPE_CHECKING:
    from mhfu_studio.shell.studio import Studio
    from mhfu_studio.shell.workspace import Workspace

Answer = Literal["save", "discard", "cancel"]
ALL = "All files (*)"


def filters(workspaces: Iterable[Workspace]) -> str:
    """Their portable-file-dialogs pairs ("Map document", "map.toml", ...) as one Qt filter."""
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
    """Asks for a document and opens it; says why when it did not open (a cancel is silent)."""
    path = ask_open(parent, studio)
    if path is not None and not studio.open(path) and studio.message:
        warn(parent, "Not opened", plain(studio.message))


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


def warn(parent: QWidget, title: str, text: str) -> None:
    QMessageBox.warning(parent, title, text)
