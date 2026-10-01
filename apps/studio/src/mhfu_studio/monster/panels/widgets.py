# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Qt pieces the monster panels share: data-colour swatches, a table that carries them, an
editable number grid, the no-scene state, and the save and export rows."""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from typing import TYPE_CHECKING

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mhfu_studio.monster.panels import common
from mhfu_studio.shell.findings import Level
from mhfu_studio.shell.widgets import plain
from mhfu_studio.ui import dialogs, kit, theme

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace
    from mhfu_studio.shell.studio import Studio

SWATCH = 12
IGNORED = QSizePolicy.Policy.Ignored
LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
OPEN_HINT = "Open a port manifest (ports/<name>.toml) or a monster PAC to work on it here."
OPEN_TIP = "Choose a port manifest or a monster PAC; the monster workspace opens it"


def _opaque(c: Sequence[float]) -> tuple[float, float, float, float]:
    return (float(c[0]), float(c[1]), float(c[2]), 1.0)


def swatch_icon(color: Sequence[float]) -> QIcon:
    """A square of a data colour (RGB or RGBA): the view's own, so a row and its gizmo match."""
    pm = QPixmap(SWATCH, SWATCH)
    pm.fill(theme.color(_opaque(color)))
    return QIcon(pm)


class Swatch(QLabel):
    """A data colour beside a heading or a field; `set(None)` hides it."""

    def __init__(self, color: Sequence[float] | None = None, tip: str = "") -> None:
        super().__init__()
        if tip:
            self.setToolTip(tip)
        self.set(color)

    def set(self, color: Sequence[float] | None) -> None:
        self.setVisible(color is not None)
        if color is not None:
            self.setPixmap(swatch_icon(color).pixmap(SWATCH, SWATCH))


def alert(text: str = "", level: Level = "warning") -> QLabel:
    """A wrapped line in a level's colour: what must not be missed."""
    w = kit.label(text)
    set_level(w, level)
    return w


def set_level(w: QLabel, level: Level | None) -> None:
    """Colours `w` as `level` (None: plain); the stylesheet does the colouring."""
    if w.property("level") != level:
        w.setProperty("level", level)
        w.style().unpolish(w)
        w.style().polish(w)


class Table(kit.Table):
    """`kit.Table` whose rows may also carry a swatch (in `swatch_column`), a tip and a level.
    A theme change rebuilds it: level colours are baked into the items."""

    def __init__(self, headers: Sequence[str], *, tip: str, swatch_column: int = 0) -> None:
        super().__init__(headers, tip=tip)
        self.horizontalHeader().setDefaultAlignment(LEFT)
        self.swatch_column = swatch_column
        self._extra: object = None

    def set_rows(
        self,
        rows: Sequence[Sequence[str]],
        data: Sequence[Hashable] | None = None,
        *,
        colors: Sequence[Sequence[float] | None] | None = None,
        tips: Sequence[str] | None = None,
        levels: Sequence[Level | None] | None = None,
    ) -> bool:
        extra = (
            theme.current(),
            None if colors is None else tuple(None if c is None else tuple(c) for c in colors),
            None if tips is None else tuple(tips),
            None if levels is None else tuple(levels),
        )
        if extra != self._extra:
            self._extra, self._shown = extra, None
        if not super().set_rows(rows, data):
            return False
        for r in range(len(rows)):
            color = None if colors is None else colors[r]
            tip = "" if tips is None else tips[r]
            level = None if levels is None else levels[r]
            for c in range(self.columnCount()):
                it = self.item(r, c)
                if it is None:
                    continue
                if tip:
                    it.setToolTip(tip)
                if level is not None:
                    it.setForeground(theme.level(level))
            it = self.item(r, self.swatch_column)
            if color is not None and it is not None:
                it.setIcon(swatch_icon(color))
        return True

    def fit(self, most: int = 8) -> None:
        """As tall as its rows, up to `most` of them; past that it scrolls."""
        n = max(1, min(self.rowCount(), most))
        head = self.horizontalHeader().sizeHint().height()
        self.setFixedHeight(head + n * self.verticalHeader().defaultSectionSize() + 4)


class Grid(QTableWidget):
    """Numbers to type into: `edited(row, column, value)` after an edit parses (`0x` hex too)
    and falls in `lo..hi`. Cells are rebuilt only when what they show changes."""

    edited = Signal(int, int, int)

    def __init__(
        self,
        columns: Sequence[str],
        *,
        tip: str,
        lo: int = 0,
        hi: int = 255,
        rows: Sequence[str] = (),
    ) -> None:
        super().__init__(len(rows), len(columns))
        self.setToolTip(tip)
        self.lo, self.hi = lo, hi
        self.setHorizontalHeaderLabels(list(columns))
        if rows:
            self.setVerticalHeaderLabels(list(rows))
        else:
            self.verticalHeader().hide()
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.horizontalHeader().setStretchLastSection(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        self._shown: object = None
        self.itemChanged.connect(self._changed)

    def set_cells(
        self,
        cells: Sequence[Sequence[str]],
        *,
        editable: bool | Sequence[bool] = True,
        tips: Sequence[str] | None = None,
        headers: Sequence[str] | None = None,
    ) -> bool:
        """Rows of cell text; `editable` for all, or per column. True when it rebuilt."""
        key = (tuple(map(tuple, cells)), editable, None if tips is None else tuple(tips), headers)
        if key == self._shown:
            return False
        self._shown = key
        with QSignalBlocker(self):
            self.setRowCount(len(cells))
            if headers is not None:
                self.setVerticalHeaderLabels(list(headers))
            for r, row in enumerate(cells):
                for c, text in enumerate(row):
                    it = QTableWidgetItem(text)
                    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    on = editable if isinstance(editable, bool) else editable[c]
                    if not on:
                        it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    if tips is not None and tips[r]:
                        it.setToolTip(tips[r])
                    self.setItem(r, c, it)
        return True

    def fit(self, most: int = 10) -> None:
        n = max(1, min(self.rowCount(), most))
        head = self.horizontalHeader().sizeHint().height()
        self.setFixedHeight(head + n * self.verticalHeader().defaultSectionSize() + 4)

    def _changed(self, it: QTableWidgetItem) -> None:
        try:
            v = int(it.text().strip(), 0)
        except ValueError:
            v = None
        if v is None or not self.lo <= v <= self.hi:
            self._shown = None  # the next sync puts the old value back
            return
        self.edited.emit(it.row(), it.column(), v)


class Pages(QStackedWidget):
    """A panel's page, or its empty state; only the one shown takes room."""

    def __init__(self, page: QWidget, empty: QWidget) -> None:
        super().__init__()
        self.page, self.empty = page, empty
        self.addWidget(page)
        self.addWidget(empty)

    def show_page(self, on: bool) -> None:
        cur = self.page if on else self.empty
        if self.currentWidget() is cur and cur.sizePolicy().verticalPolicy() is not IGNORED:
            return
        for w in (self.page, self.empty):
            p = QSizePolicy.Policy.Preferred if w is cur else QSizePolicy.Policy.Ignored
            w.setSizePolicy(p, p)
        self.setCurrentWidget(cur)
        self.updateGeometry()


class NoScene(kit.Empty):
    """What a monster panel shows with nothing open, and the way to open something."""

    def __init__(self, studio: Studio, hint: str = OPEN_HINT) -> None:
        super().__init__("No monster open", hint, ("Open…", OPEN_TIP, self._open))
        self.studio = studio

    def _open(self) -> None:
        path = dialogs.ask_open(self, self.studio)
        if path is not None and not self.studio.open(path):
            dialogs.warn(self, "Not opened", plain(self.studio.message))


class SaveRow(QWidget):
    """Save or discard the manifest's staged edits (each is already an undo step)."""

    def __init__(self, ws: MonsterWorkspace, studio: Studio) -> None:
        super().__init__()
        self.ws = ws
        self.save = kit.button(
            "Save",
            tip="Writes every staged edit into the port manifest on disk. Export and deploy"
            " read the saved file, so save before you ship.",
            on=studio.save,
            role="primary",
            icon="ph.floppy-disk",
        )
        self.discard = kit.button(
            "Discard",
            tip="Throws away every edit since the last save and goes back to the file on disk."
            " Undo brings them back.",
            on=studio.act("discard", ws.revert),
            role="danger",
        )
        self.hint = kit.label("Nothing to save", role="muted", wrap=False)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        for w in (self.save, self.discard, self.hint):
            lay.addWidget(w)
        lay.addStretch(1)

    def sync(self) -> None:
        doc = self.ws.doc
        self.setVisible(doc is not None)
        dirty = doc is not None and doc.dirty
        where = doc.path.name if doc is not None and doc.path is not None else "the manifest"
        self.save.setText(f"Save to {where}")
        self.save.setVisible(dirty)
        self.discard.setVisible(dirty)
        self.hint.setVisible(not dirty)


class ExportRow(QWidget):
    """The SAVED manifest's tables as `<name>_hit.lua`, and onto the memory stick."""

    def __init__(self, ws: MonsterWorkspace, studio: Studio) -> None:
        super().__init__()
        self.ws = ws
        self.export = kit.button(
            "Export",
            tip="Writes <name>_hit.lua beside you: the SAVED hurtboxes, damage grid, hitboxes"
            " and attack records as the one P.hit() call mhfu_port.lua makes in the game, each"
            " table written over the host's.",
            on=studio.act("export", lambda: common.export_hit(ws)),
            icon="ph.export",
        )
        self.deploy = kit.button(
            "Deploy to memstick",
            tip="Exports, then copies the module to the memory stick's mods folder (and"
            " mhfu_port.lua when the stick's is older: a stale library silently skips fields it"
            " does not know). A running game reloads it; a cold one loads it at boot.",
            on=studio.act("deploy", self._deploy),
            role="primary",
            icon="ph.rocket-launch",
        )
        self.hint = kit.label(role="muted")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(kit.row(self.export, self.deploy, stretch=True))
        lay.addWidget(self.hint)

    def _deploy(self) -> None:
        mods = common.mods_dir()
        if mods is None:
            raise FileNotFoundError("no memory stick with the framework's mods folder")
        common.deploy_hit(self.ws, mods)

    def sync(self) -> None:
        doc = self.ws.doc
        self.setVisible(doc is not None and doc.path is not None)
        if doc is None:
            return
        ok = common.exportable(self.ws)
        self.export.setEnabled(ok)
        self.deploy.setEnabled(ok and common.mods_dir() is not None)
        if not ok:
            text = "Nothing to export yet: save volumes, a grid, a hitbox set or a record first."
        elif common.mods_dir() is None:
            text = "No memory stick found to deploy to; Export still writes the module."
        elif doc.dirty:
            text = "Exports the SAVED file: save first, or your latest edits stay behind."
        else:
            text = ""
        self.hint.setText(text)
        self.hint.setVisible(bool(text))
