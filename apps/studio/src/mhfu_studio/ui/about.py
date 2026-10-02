# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Help menu's windows: Keyboard shortcuts and About."""

from __future__ import annotations

from collections.abc import Iterable
from importlib import metadata
from typing import TYPE_CHECKING

import PySide6
from PySide6.QtCore import qVersion
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QDialog, QHBoxLayout, QMenu, QVBoxLayout, QWidget

from mhfu_studio.shell.text import keys
from mhfu_studio.ui import kit
from mhfu_studio.ui.view import MOUSE

if TYPE_CHECKING:
    from mhfu_studio.shell.studio import Studio

#: where, keys, what they do
Row = tuple[str, str, str]
CLOSE_TIP = "Closes this window"


def native(seq: QKeySequence | str) -> str:
    """A key sequence as the system writes it: "⌘S" on macOS, "Ctrl+S" elsewhere."""
    return QKeySequence(seq).toString(QKeySequence.SequenceFormat.NativeText)


def menu_rows(actions: Iterable[QAction], where: str = "") -> list[Row]:
    """Every menu action with a shortcut, submenus too."""
    out: list[Row] = []
    for a in actions:
        sub = a.menu()
        if isinstance(sub, QMenu):
            out += menu_rows(sub.actions(), f"{where} > {a.text()}" if where else a.text())
        elif not a.shortcut().isEmpty():
            out.append((f"{where} menu", native(a.shortcut()), a.text().rstrip("…")))
    return out


def rows(studio: Studio, menus: Iterable[QAction]) -> list[Row]:
    """The menus, each workspace's tools and view keys, the mouse."""
    out = [(where.replace("&", ""), k, does) for where, k, does in menu_rows(menus)]
    for ws in studio.workspaces:
        name = ws.name.capitalize()
        for g in ws.tool_groups():
            out += [(f"{name} toolbar", native(t.key), f"{g.label}: {t.label}") for t in g.tools]
        out += [(f"{name} view", keys(s.keys), s.does) for s in ws.shortcuts()]
    out += [("The view", keys(s.keys), s.does) for s in MOUSE]
    return [r for r in out if r[1]]


def _dialog(title: str, parent: QWidget) -> tuple[QDialog, QVBoxLayout]:
    d = QDialog(parent)
    d.setWindowTitle(title)
    lay = QVBoxLayout(d)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(10)
    return d, lay


def _close(d: QDialog, lay: QVBoxLayout) -> None:
    row = QHBoxLayout()
    row.addStretch(1)
    row.addWidget(kit.button("Close", tip=CLOSE_TIP, on=d.close))
    lay.addLayout(row)


class Shortcuts:
    """Help > Keyboard shortcuts: one table."""

    def __init__(self, studio: Studio, menus: Iterable[QAction], parent: QWidget) -> None:
        self.dialog, lay = _dialog("Keyboard shortcuts", parent)
        self.table = kit.Table(
            ["Where", "Keys", "What they do"],
            tip="Every key and mouse gesture the studio knows; a tool's key works while its"
            " workspace is open",
        )
        self.table.set_rows(rows(studio, menus))
        lay.addWidget(self.table, 1)
        _close(self.dialog, lay)
        self.dialog.resize(760, 600)


class About:
    """Help > About: what the studio is, its versions, what each workspace has open and the
    renderer."""

    def __init__(self, studio: Studio, parent: QWidget) -> None:
        self.studio = studio
        self.dialog, lay = _dialog("About MHFU Studio", parent)
        lay.addWidget(kit.label("MHFU Studio", role="title"))
        lay.addWidget(
            kit.label(
                "Edits Monster Hunter Freedom Unite's monsters and maps, and sends the edits"
                " into the game running in PPSSPP.",
                role="muted",
            )
        )
        facts = kit.Form()
        version = (
            f"{metadata.version('mhfu-studio')}, Qt {qVersion()}, PySide6 {PySide6.__version__}"
        )
        facts.row("Version", kit.label(version, selectable=True))
        self.renderer = kit.label(selectable=True)
        facts.row("Renderer", self.renderer)
        self.open = {ws.name: kit.label(selectable=True) for ws in studio.workspaces}
        for name, lb in self.open.items():
            facts.row(name.capitalize(), lb)
        lay.addWidget(facts)
        _close(self.dialog, lay)

    def sync(self) -> None:
        self.renderer.setText(self.studio.renderer or "no 3D view yet")
        for ws in self.studio.workspaces:
            self.open[ws.name].setText(ws.status() or "nothing open")
