# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Map panel: every row and its sections in walking order; a click loads a section alone."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mhfu_studio.shell.widgets import plain
from mhfu_studio.ui import kit, theme

from ..core.atlas import ROW_NAMES, MapRow, Section
from .common import Gate

if TYPE_CHECKING:
    from mhfu_studio.shell.studio import Studio
    from mhfu_studio.ui.theme import Theme

    from ..workspace import MapWorkspace

DATA = Qt.ItemDataRole.UserRole
EXITS_SHOWN = 5
TREE_TIP = (
    "The game's areas (rows) and the sections the player walks through in each, entry area"
    " first. Click a section to load it alone; the loaded one is bold, with a pin. Greyed"
    " sections are empty stubs the game never loads."
)


def row_text(r: MapRow) -> str:
    named = f"  {r.name}" if r.index in ROW_NAMES else ""
    return f"row {r.index}{named}  ({len(r.sections)} sections)"


def section_text(s: Section) -> str:
    name = "" if s.name == f"st{s.stage:03d}" else f"  {s.name}"
    return f"{s.slot}  st{s.stage:03d}{name}{'  (entry)' if s.is_entry else ''}"


def section_tip(s: Section) -> str:
    if not s.present:
        return f"st{s.stage:03d} is a placeholder: an empty stub with nothing to load"
    where = "the entry area" if s.is_entry else f"section {s.slot}"
    return f"st{s.stage:03d}, {where} of row {s.row}, {s.size:,} bytes. Click to load it."


class BrowserPanel(kit.Panel):
    def __init__(self, ws: MapWorkspace, studio: Studio) -> None:
        super().__init__(scroll=False)
        self.ws, self.studio = ws, studio
        self.error = kit.pill("error")
        self.error.setWordWrap(True)
        self.tree = QTreeWidget()
        self.tree.setToolTip(TREE_TIP)
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tree.itemClicked.connect(self._clicked)
        self.tree.itemActivated.connect(self._clicked)
        self.here = kit.Section(
            "Loaded section", tip="Where its exits lead and where the player arrives from"
        )
        self.title = kit.label(role="title")
        self.exits = kit.Items(
            tip="Where each exit of this section leads. Click one to load that section, the"
            " way the player would walk through it.",
        )
        self.exits.picked.connect(self._exit)
        self.arrivals = kit.label(role="muted")
        for w in (self.title, self.exits, self.arrivals):
            self.here.body.addWidget(w)
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.error)
        lay.addWidget(self.tree, 1)
        lay.addWidget(self.here)
        self.gate = Gate(page, "")
        self.body.addWidget(self.gate)
        self._items: dict[tuple[int, int], QTreeWidgetItem] = {}
        self._shown: tuple[int, int] | None = None
        self._arrived: dict[int, str] = {}
        self._theme: Theme | None = None

    def _build(self) -> None:
        atlas = self.ws.atlas
        if atlas is None or self._items:
            return
        for r in atlas.live_rows():
            top = QTreeWidgetItem([row_text(r)])
            top.setToolTip(0, f"{r.name}: click to show or hide its sections")
            for s in r.sections:
                it = QTreeWidgetItem([section_text(s)])
                it.setToolTip(0, section_tip(s))
                if s.present:
                    it.setData(0, DATA, (r.index, s.stage))
                else:
                    it.setFlags(Qt.ItemFlag.NoItemFlags)
                top.addChild(it)
                self._items[(r.index, s.stage)] = it
            self.tree.addTopLevelItem(top)

    def _clicked(self, item: QTreeWidgetItem) -> None:
        got = item.data(0, DATA)
        if got is None:
            item.setExpanded(not item.isExpanded())
            return
        row, stage = got
        self.studio.act(f"load st{stage:03d}", lambda: self.ws.load_stage(stage, row=row))()

    def _exit(self, stage: object) -> None:
        if isinstance(stage, int):
            self.studio.act(f"load st{stage:03d}", lambda: self.ws.load_stage(stage))()

    def sync(self) -> None:
        ws = self.ws
        if not self.gate.check(ws, section=False):
            return
        self._build()
        self.error.setText(plain(ws.load_error))
        self.error.setVisible(bool(ws.load_error))
        sc = ws.scene
        now = (ws.row, sc.stage) if sc is not None and ws.row is not None else None
        if now != self._shown or theme.current() is not self._theme:
            self._mark(now)
        self.here.setVisible(sc is not None)
        if sc is None:
            return
        self.title.setText(f"st{sc.stage:03d}  {sc.name}")
        self.exits.setVisible(bool(sc.exits))
        rebuilt = self.exits.set_items(
            [
                kit.Item(
                    f"exit {e.index}  to st{e.target:03d}  {e.target_name}",
                    e.target,
                    f"Walking into exit {e.index} takes the player to st{e.target:03d}."
                    " Click to load it.",
                )
                for e in sc.exits
            ]
        )
        if rebuilt and sc.exits:  # as tall as its exits, up to EXITS_SHOWN
            rows = min(len(sc.exits), EXITS_SHOWN) * self.exits.sizeHintForRow(0)
            self.exits.setFixedHeight(rows + 2 * self.exits.frameWidth() + 4)
        self.arrivals.setText(self._arrivals(sc.stage))

    def _mark(self, now: tuple[int, int] | None) -> None:
        """The loaded section bold with a pin, and in view; the last one plain again."""
        self._theme = theme.current()
        pin = theme.icon("ph.map-pin-fill", accent=True)
        for key in (self._shown, now):
            it = self._items.get(key) if key is not None else None
            if it is not None:
                f = it.font(0)
                f.setBold(key == now)
                it.setFont(0, f)
                it.setIcon(0, pin if key == now else QIcon())
        self._shown = now
        it = self._items.get(now) if now is not None else None
        top = it.parent() if it is not None else None
        if it is not None and top is not None:
            top.setExpanded(True)
            # after the layout settles: a dock just shown has no height yet
            QTimer.singleShot(0, self.tree, lambda: self.tree.scrollToItem(it))

    def _arrivals(self, stage: int) -> str:
        """Where the player arrives from, read once per stage."""
        if stage not in self._arrived and self.ws.atlas is not None:
            names = sorted({f"st{n:03d}" for n, _ in self.ws.atlas.arrivals(stage)})
            self._arrived[stage] = (
                f"The player arrives here from {', '.join(names)}."
                if names
                else "No other section of its row leads here."
            )
        if self.ws.scene is not None and not self.ws.scene.exits:
            return "It has no exits. " + self._arrived.get(stage, "")
        return self._arrived.get(stage, "")
