# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Native panels: the map Selection form, its edit history, and the shell's Findings.

Qt widgets keep their own state, so each panel has `sync()` to re-read the workspace; the
window calls it after anything that may have changed it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mhfu_studio.shell.findings import LEVELS
from mhfu_studio.shell.widgets import LEVEL_COLORS, plain

if TYPE_CHECKING:
    from mhfu_studio.map.workspace import MapWorkspace
    from mhfu_studio.shell.app import Studio

REMOVE_TIP = (
    "Clears the objects' primitives: a degenerate strip draws nothing, and the primitives"
    " become free budget for adding."
)
BUDGET_TIP = (
    "A section draws only what its primitives already hold (a new primitive never draws)."
    " Removing frees primitives, adding fills free ones, transforms spend nothing."
)


def _button(text: str, tip: str, slot: Callable[[], None]) -> QPushButton:
    b = QPushButton(text)
    b.setToolTip(tip)
    b.clicked.connect(slot)
    return b


def _row(*widgets: QWidget) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    for x in widgets:
        lay.addWidget(x)
    return w


class Vec3(QWidget):
    """Three spin boxes for x, y, z."""

    def __init__(self, value: float, step: float, decimals: int) -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.boxes = []
        for axis in "xyz":
            b = QDoubleSpinBox()
            b.setRange(-1e6, 1e6)
            b.setDecimals(decimals)
            b.setSingleStep(step)
            b.setValue(value)
            b.setPrefix(f"{axis} ")
            b.setAccelerated(True)
            lay.addWidget(b)
            self.boxes.append(b)
        self.default = value

    def value(self) -> list[float]:
        return [b.value() for b in self.boxes]

    def reset(self) -> None:
        for b in self.boxes:
            b.setValue(self.default)


class SelectionPanel(QWidget):
    """What is selected and a numeric transform about its centre."""

    def __init__(self, ws: MapWorkspace, changed: Callable[[], None]) -> None:
        super().__init__()
        self.ws, self.changed = ws, changed
        lay = QVBoxLayout(self)
        self.what = QLabel()
        self.what.setWordWrap(True)
        self.stats = QLabel()
        self.stats.setWordWrap(True)
        self.stats.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.what)
        lay.addWidget(self.stats)
        self.buttons = _row(
            _button("Frame", "Point the camera at the selection (F)", self._do(ws.frame_selection)),
            _button("Clear", "Select nothing (Esc)", self._do(self._clear)),
            _button(
                "Remove", REMOVE_TIP + " (Del)", self._do(lambda: ws.remove_selected(solid=False))
            ),
            _button(
                "Remove + collision",
                REMOVE_TIP + " The collision triangles inside the objects' box go too.",
                self._do(lambda: ws.remove_selected(solid=True)),
            ),
        )
        lay.addWidget(self.buttons)
        box = QGroupBox("Move, rotate or scale about the selection's centre")
        form = QFormLayout(box)
        self.by = Vec3(0.0, 10.0, 1)
        self.turn = Vec3(0.0, 5.0, 1)
        self.factor = Vec3(1.0, 0.05, 3)
        form.addRow("Move by", self.by)
        form.addRow("Rotate (degrees)", self.turn)
        form.addRow("Scale", self.factor)
        apply = _button("Apply", "One edit; undo takes it back", self._do(self._apply))
        apply.setDefault(True)
        reset = _button("Reset fields", "Back to no change", self._reset)
        form.addRow(_row(apply, reset))
        self.transform = box
        lay.addWidget(box)
        self.budget = QLabel()
        self.budget.setWordWrap(True)
        self.budget.setToolTip(BUDGET_TIP)
        lay.addWidget(self.budget)
        lay.addStretch(1)
        self.sync()

    def _do(self, fn: Callable[[], object]) -> Callable[[], None]:
        def run() -> None:
            fn()
            self.changed()

        return run

    def _clear(self) -> None:
        from mhfu_studio.map.core.edit import Selection

        self.ws.tools.select(Selection(self.ws.tools.kind))

    def _apply(self) -> None:
        self.ws.apply_numeric(self.by.value(), self.turn.value(), self.factor.value())
        self._reset()

    def _reset(self) -> None:
        for v in (self.by, self.turn, self.factor):
            v.reset()

    def sync(self) -> None:
        from mhfu_studio.map.core.edit import COLLISION

        ws = self.ws
        sc, sess = ws.scene, ws.session
        if sc is None or sess is None:
            self.what.setText(ws.data_error or "No section loaded.")
            self.stats.clear()
            self.buttons.setEnabled(False)
            self.transform.setEnabled(False)
            self.budget.clear()
            return
        sel = ws.col_sel if ws.tools.kind == COLLISION else ws.selection
        self.what.setText(sel.describe(sc))
        self.buttons.setEnabled(not sel.empty and ws.tools.kind != COLLISION)
        self.transform.setEnabled(not sel.empty)
        if sel.empty:
            self.stats.setText(
                "Click an object in the view; shift-click adds, a drag selects a box"
            )
        else:
            lo, hi = sel.bounds(sc)
            c, size = (lo + hi) * 0.5, hi - lo
            self.stats.setText(
                f"centre ({c[0]:.0f}, {c[1]:.0f}, {c[2]:.0f})   "
                f"size {size[0]:.0f} x {size[1]:.0f} x {size[2]:.0f}"
            )
        b = sess.budget()
        self.budget.setText(
            f"Budget: {b['drawn']} of {b['total']} triangles drawn; {b['free']} free in cleared"
            " primitives."
        )


class HistoryPanel(QWidget):
    """The loaded section's edits, newest first, with undo and redo."""

    def __init__(self, ws: MapWorkspace, studio: Studio, changed: Callable[[], None]) -> None:
        super().__init__()
        self.ws = ws
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        lay.addWidget(self.list)

        def step(fn: Callable[[], None]) -> Callable[[], None]:
            def run() -> None:
                fn()
                changed()

            return run

        self.undo = _button("Undo", "Take back the last edit (Ctrl+Z)", step(studio.undo))
        self.redo = _button("Redo", "Do it again (Ctrl+Shift+Z)", step(studio.redo))
        lay.addWidget(_row(self.undo, self.redo))
        self.sync()

    def sync(self) -> None:
        from mhfu_studio.map.core.edit import describe_op

        sess = self.ws.session
        ops = sess.ops if sess is not None else []
        self.list.clear()
        for i, op in reversed(list(enumerate(ops, 1))):
            self.list.addItem(f"{i:3d}  {describe_op(op)}")
        if not ops:
            self.list.addItem(QListWidgetItem("No edits yet."))
        self.undo.setEnabled(sess is not None and sess.can_undo())
        self.redo.setEnabled(sess is not None and sess.can_redo())


class FindingsPanel(QWidget):
    """The document's findings; activating one shows what it is about."""

    def __init__(self, studio: Studio, changed: Callable[[], None]) -> None:
        super().__init__()
        self.studio, self.changed = studio, changed
        lay = QVBoxLayout(self)
        check = _button("Check", "Run every check on the document now", self._check)
        self.auto = QCheckBox("re-check while editing")
        self.auto.setChecked(True)
        self.auto.toggled.connect(self._auto)
        self.counts = QLabel()
        head = _row(check, self.auto, self.counts)
        head.layout().addStretch(1)  # type: ignore[union-attr]
        lay.addWidget(head)
        self.list = QListWidget()
        self.list.itemActivated.connect(self._reveal)
        self.list.itemClicked.connect(self._reveal)
        lay.addWidget(self.list)
        self._shown: list[object] | None = None

    def _check(self) -> None:
        self.studio.findings.stale()
        self.sync()

    def _auto(self, on: bool) -> None:
        self.studio.findings.auto = on

    def _reveal(self, item: QListWidgetItem) -> None:
        target = item.data(Qt.ItemDataRole.UserRole)
        if target is not None:
            self.studio.active.reveal(target)
            self.changed()

    def sync(self) -> None:
        found = self.studio.findings.get(self.studio.active.document)
        if found == self._shown:
            return
        self._shown = list(found)
        self.counts.setText(", ".join(f"{sum(f.level == lv for f in found)} {lv}" for lv in LEVELS))
        self.list.clear()
        for f in found:
            item = QListWidgetItem(plain(str(f)))
            item.setForeground(QColor.fromRgbF(*LEVEL_COLORS[f.level]))
            item.setData(Qt.ItemDataRole.UserRole, f.target)
            if f.target is not None:
                item.setToolTip("Click to show it")
            self.list.addItem(item)
        if not found:
            self.list.addItem("Nothing to report.")
