# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Brain tab's names: the script's variables and distance ranges with what the file calls
them, and the one way every view asks for a name (`rename`)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mhfu.em.script import catalogue as cat
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QHeaderView, QInputDialog, QTreeWidget, QTreeWidgetItem, QWidget

from mhfu_studio.monster import brain
from mhfu_studio.monster import brain_edit as be
from mhfu_studio.ui import theme

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace
    from mhfu_studio.shell.studio import Studio

ROUTINE, VARS, RANGES = "routine", "vars", "ranges"
"""What a name is of: a routine (by name), a variable or a distance range (by number)."""
OWN_BYTES = 4
"""Variables 1 to 4 are bytes of the monster; the others call the species overlay."""


def ask_name(parent: QWidget, what: str, current: str) -> str | None:
    """The name the user types for `what` (blank takes it away); None for Cancel. The seam the
    tests replace."""
    got, ok = QInputDialog.getText(
        parent, "Name", f"A name for {what} (blank takes it away):", text=current
    )
    return got if ok else None


def what_of(table: str, key: str | int) -> str:
    """`routine call_3`, `variable 4`, `distance range 2`, `the range past the limits`."""
    if table == ROUTINE:
        return f"routine {key}"
    if table == VARS:
        return f"variable {key}"
    return "the range past the limits" if key == cat.BEYOND else f"distance range {key}"


def rename(
    parent: QWidget, ws: MonsterWorkspace, studio: Studio, row: int, table: str, key: str | int
) -> None:
    """Asks for the name of a routine, a variable or a distance range of `row`'s script and puts
    it in the brain file, one undo step; Cancel, or the same name, changes nothing."""
    w = ws.brain_words(row)
    if table == ROUTINE:
        current = w.routines.get(str(key), "")
    else:
        current = (w.vars if table == VARS else w.ranges).get(int(key), "")
    got = ask_name(parent, what_of(table, key), current)
    if got is None or got.strip() == current:
        return
    if table == ROUTINE:
        g = be.rename_routine(str(key), got)
    elif table == VARS:
        g = be.name_var(int(key), got)
    else:
        g = be.name_range(int(key), got)
    studio.act(g.label, lambda: ws.edit_brain(g, row))()


def var_tip(v: int, uses: int) -> str:
    where = (
        "a byte of the monster's own"
        if v <= OWN_BYTES
        else "a function of the species overlay: setting it calls one, testing it asks one"
    )
    used = f"{uses} node{'' if uses == 1 else 's'} name it" if uses else "this script never uses it"
    return f"Variable {v}: {where}. {used}. Double-click to name it"


def range_tip(k: int, words: cat.Words) -> str:
    sets = [", ".join("skipped" if v < 0 else f"{v:g}" for v in s) for s in words.limits]
    limits = f"Limits, set 0: {sets[0]}; set 1: {sets[1]}" if len(sets) == 2 else "Limits unknown"
    what = (
        "Farther than the last limit, or no target"
        if k == cat.BEYOND
        else f"Within limit {k} of the set in use"
    )
    return (
        f"{what}. {limits} (the species' own; set 0 at spawn, its code may switch to set 1)."
        " Double-click to name it"
    )


class Names(QTreeWidget):
    """The script's variables and distance ranges, each with the file's name for it and how
    often the script names it. Double-click, Return or F2 names one: `named` carries the table
    (`VARS`, `RANGES`) and the number."""

    named = Signal(str, int)
    KEY = Qt.ItemDataRole.UserRole

    def __init__(self) -> None:
        super().__init__()
        self.setToolTip(
            "Names for the script's variables and distance ranges, shown wherever the script"
            " uses them. Double-click one to name it; the names are the brain file's and change"
            " no bytes"
        )
        self.setColumnCount(3)
        self.setHeaderHidden(True)
        self.setIndentation(12)
        self.setRootIsDecorated(False)
        head = self.header()
        head.setStretchLastSection(False)
        for c, mode in enumerate(
            (
                QHeaderView.ResizeMode.Fixed,
                QHeaderView.ResizeMode.Stretch,
                QHeaderView.ResizeMode.Fixed,
            )
        ):
            head.setSectionResizeMode(c, mode)
        self.itemActivated.connect(self._ask)
        self._shown: tuple[cat.Words, brain.Uses] | None = None

    def show_names(self, words: cat.Words, uses: brain.Uses) -> None:
        """Shows the names, keeping the row picked and the scroll."""
        if self._shown == (words, uses):
            return
        self._shown = (words, uses)
        item = self.currentItem()
        here = None if item is None else item.data(0, self.KEY)
        scroll = self.verticalScrollBar().value()
        self.clear()
        plain = cat.Words(limits=words.limits)
        rows: list[QTreeWidgetItem] = []
        for title, table, keys in (
            ("Variables", VARS, range(1, cat.VARS + 1)),
            (f"Distance ranges ({uses.ranges} switches)", RANGES, range(1, cat.BEYOND + 1)),
        ):
            top = QTreeWidgetItem([title, "", ""])
            top.setFlags(Qt.ItemFlag.ItemIsEnabled)
            font = top.font(0)
            font.setBold(True)
            top.setFont(0, font)
            self.addTopLevelItem(top)
            top.setFirstColumnSpanned(True)  # only once it is in the tree
            for k in keys:
                if table == VARS:
                    n = uses.vars[k]
                    cells = [f"var {k}", words.vars.get(k, ""), f"×{n}" if n else ""]
                    tip = var_tip(k, n)
                    quiet = not n
                else:
                    cells = [plain.bucket(k - 1, k == cat.BEYOND), words.ranges.get(k, ""), ""]
                    tip = range_tip(k, words)
                    quiet = not uses.ranges
                row = QTreeWidgetItem(cells)
                row.setData(0, self.KEY, (table, k))
                for c in range(3):
                    row.setToolTip(c, tip)
                    if quiet:
                        row.setForeground(c, theme.text(muted=True))
                top.addChild(row)
                rows.append(row)
                if (table, k) == here:
                    self.setCurrentItem(row)
        self.expandAll()
        self._fit(rows)
        self.verticalScrollBar().setValue(scroll)

    def _fit(self, rows: list[QTreeWidgetItem]) -> None:
        """The number and count columns as wide as their rows; a group's title spans them all,
        so it is not measured."""
        fm = self.fontMetrics()
        for c, pad in ((0, self.indentation() + 12), (2, 8)):
            room = max((fm.horizontalAdvance(r.text(c)) for r in rows), default=0)
            self.header().resizeSection(c, room + pad)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_F2:
            self._ask(self.currentItem())
            return
        super().keyPressEvent(event)

    def _ask(self, item: QTreeWidgetItem | None) -> None:
        key = None if item is None else item.data(0, self.KEY)
        if isinstance(key, tuple):
            self.named.emit(*key)
