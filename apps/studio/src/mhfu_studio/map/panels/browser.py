# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Areas panel: every map and its areas in walking order; a click loads an area alone."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mhfu_studio.shell.text import plain
from mhfu_studio.ui import kit, theme

from ..core.atlas import STAGE_NAMES, MapRow, Section, stage_id, stage_title
from .common import Gate, fit

if TYPE_CHECKING:
    from mhfu_studio.shell.studio import Studio
    from mhfu_studio.ui.theme import Theme

    from ..workspace import MapWorkspace

DATA = Qt.ItemDataRole.UserRole
TREE_TIP = (
    "The game's maps and the areas the player walks through in each, entry area first. Click"
    " an area to load it; the loaded one is bold, with a pin."
)


def row_text(r: MapRow) -> str:
    return f"{r.name}  ({len(r.sections)} area{'' if len(r.sections) == 1 else 's'})"


def section_text(s: Section) -> tuple[str, str]:
    """The area's name and, small beside it, its id."""
    return STAGE_NAMES.get(s.stage, "Area"), stage_id(s.stage)


def section_tip(s: Section, row: str) -> str:
    if not s.present:
        return f"{stage_id(s.stage)} is an empty stub the game never loads"
    where = "the entry area" if s.is_entry else f"area {s.slot}"
    return f"{stage_title(s.stage)}: {where} of {row}, {s.size:,} bytes. Click to load it."


class BrowserPanel(kit.Panel):
    def __init__(self, ws: MapWorkspace, studio: Studio) -> None:
        super().__init__(scroll=False)
        self.ws, self.studio = ws, studio
        self.error = kit.Alert(level="error")
        self.tree = QTreeWidget()
        self.tree.setToolTip(TREE_TIP)
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        head = self.tree.header()
        head.setStretchLastSection(False)
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        head.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tree.itemClicked.connect(self._clicked)
        self.tree.itemActivated.connect(self._clicked)
        self.here = kit.Section(
            "This area", tip="Where its exits lead and where the player arrives from"
        )
        self.title = kit.label(role="title")
        self.exits = kit.Items(
            tip="Where each exit of this area leads. Click one to load that area, the way the"
            " player would walk through it.",
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
            top.setToolTip(0, f"{r.name}: click to show or hide its areas")
            for s in r.sections:
                it = QTreeWidgetItem(list(section_text(s)))
                tip = section_tip(s, r.name)
                for col in (0, 1):
                    it.setToolTip(col, tip)
                if s.present:
                    it.setData(0, DATA, (r.index, s.stage))
                else:
                    it.setFlags(Qt.ItemFlag.NoItemFlags)
                top.addChild(it)
                self._items[(r.index, s.stage)] = it
            self.tree.addTopLevelItem(top)
            top.setFirstColumnSpanned(True)

    def _clicked(self, item: QTreeWidgetItem) -> None:
        got = item.data(0, DATA)
        if got is None:
            item.setExpanded(not item.isExpanded())
            return
        row, stage = got
        self.studio.act(f"load {stage_id(stage)}", lambda: self.ws.load_stage(stage, row=row))()

    def _exit(self, stage: object) -> None:
        if isinstance(stage, int):
            self.studio.act(f"load {stage_id(stage)}", lambda: self.ws.load_stage(stage))()

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
        self.title.setText(stage_title(sc.stage))
        self.exits.setVisible(bool(sc.exits))
        rebuilt = self.exits.set_items(
            [
                kit.Item(
                    f"Exit {e.index} \u2192 {stage_title(e.target)}",
                    e.target,
                    f"Walking into exit {e.index} takes the player there. Click to load it.",
                )
                for e in sc.exits
            ]
        )
        if rebuilt:
            fit(self.exits)
        self.arrivals.setText(self._arrivals(sc.stage))

    def _mark(self, now: tuple[int, int] | None) -> None:
        """The loaded area bold with a pin, and in view; the last one plain again."""
        if theme.current() is not self._theme:
            self._theme = theme.current()
            muted = theme.level("info")  # the muted text colour
            for item in self._items.values():
                item.setForeground(1, muted)
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
            names = [stage_title(n) for n in sorted({n for n, _ in self.ws.atlas.arrivals(stage)})]
            self._arrived[stage] = (
                f"The player arrives here from {', '.join(names)}."
                if names
                else "No other area of its map leads here."
            )
        if self.ws.scene is not None and not self.ws.scene.exits:
            return "It has no exits. " + self._arrived.get(stage, "")
        return self._arrived.get(stage, "")
