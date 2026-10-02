# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The shell's Findings dock: what the checks found in the active workspace's document. A line
says where and what; its colour is the level, its tooltip the level, the code and what to do.

A click reveals the finding's target, and the panel holding its `focus` control lands on it
(`take`): the dock comes forward, scrolls to the control and gives it the keyboard."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Protocol

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mhfu_studio.shell.findings import LEVELS, Finding
from mhfu_studio.shell.text import plain
from mhfu_studio.ui import kit

if TYPE_CHECKING:
    from mhfu_studio.shell.studio import Studio

PLURAL = {"error": "errors", "warning": "warnings", "info": "info"}
OPEN_HINT = (
    "Findings are what the checks spot in a document: a missing file, a clash, a value out of"
    " range. Open a map document or a port manifest to see its findings here."
)


def line(f: Finding) -> str:
    return plain(f"{f.where}: {f.message}" if f.where else f.message)


#: points kept clear above and below a control scrolled to
MARGIN = 24


class Landing(Protocol):
    #: the control a revealed finding names, until its panel lands on it
    landing: str


def tip(f: Finding) -> str:
    head = f"{f.level}: {f.code}"
    if f.focus:
        return f"{head}\nClick to go to what fixes it"
    show = "\nClick to show it" if f.target is not None else ""
    return f"{head}\n{f.fix}{show}" if f.fix else head + show


def take(ws: Landing, controls: Mapping[str, QWidget]) -> None:
    """Lands on the control `ws.landing` names when it is one of `controls`, once."""
    w = controls.get(ws.landing)
    if w is not None:
        ws.landing = ""
        land(w)


def land(w: QWidget) -> None:
    """Opens a `kit.More` around `w`, then scrolls its panel to it and gives it the keyboard,
    once the layout the panel's sync changed has settled."""
    p = w.parentWidget()
    while p is not None:
        if isinstance(p, kit.More):
            p.set_open(True)
        p = p.parentWidget()
    QTimer.singleShot(0, w, lambda: _land(w))


def _land(w: QWidget) -> None:
    QCoreApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest.value)
    section, p = None, w.parentWidget()
    while p is not None and not isinstance(p, QScrollArea):
        if section is None and isinstance(p, kit.Section | kit.More):
            section = p
        p = p.parentWidget()
    inner = None if p is None else p.widget()
    if p is not None and inner is not None:
        if section is not None:  # its title at the top, then as little more as `w` needs
            p.verticalScrollBar().setValue(section.mapTo(inner, QPoint(0, 0)).y())
        p.ensureWidgetVisible(w, 0, MARGIN)
    if isinstance(w, QAbstractItemView) and w.currentIndex().isValid():
        w.scrollTo(w.currentIndex())
    keys = [w, *(k for k in w.findChildren(QWidget) if k.isVisibleTo(w))]
    got = next((k for k in keys if k.focusPolicy() & Qt.FocusPolicy.TabFocus), None)
    if got is not None:
        got.setFocus(Qt.FocusReason.OtherFocusReason)


class FindingsPanel(kit.Panel):
    def __init__(self, studio: Studio, ask_open: Callable[[], object]) -> None:
        super().__init__(scroll=False)
        self.studio = studio
        check = kit.button(
            "Check",
            tip="Runs every check on the document now, without waiting for the next pass",
            on=studio.act("check", studio.findings.stale),
            icon="ph.check-circle",
        )
        self.auto = kit.check(
            "Re-check while editing",
            tip="Checks the document again every few seconds while you edit. Turn it off if a"
            " large document feels slow; Check still runs them on demand.",
            on=self._auto,
            checked=studio.findings.auto,
        )
        self.pills = {lv: kit.pill(lv) for lv in LEVELS}
        self.list = kit.Items(
            tip="What the checks found. Click one that names a place to jump to it.",
            empty="Nothing to report.",
        )
        self.list.picked.connect(self._reveal)
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(kit.row(check, self.auto, *self.pills.values(), stretch=True, spacing=12))
        lay.addWidget(self.list)
        self.empty = kit.Empty(
            "No document open",
            OPEN_HINT,
            ("Open…", "Choose a document; the workspace that reads it comes up with it", ask_open),
        )
        self.stack = QStackedWidget()
        self.stack.addWidget(page)
        self.stack.addWidget(self.empty)
        self.page = page
        self.body.addWidget(self.stack)

    def _auto(self, on: bool) -> None:
        self.studio.act(
            "re-check while editing", lambda: setattr(self.studio.findings, "auto", on)
        )()

    def _reveal(self, f: Finding) -> None:
        self.studio.act("show finding", lambda: self.studio.active.reveal(f.target, f.focus))()

    def sync(self) -> None:
        doc = self.studio.active.document
        if doc is None:
            self.stack.setCurrentWidget(self.empty)
            return
        self.stack.setCurrentWidget(self.page)
        found = self.studio.findings.get(doc)
        for lv, pill in self.pills.items():
            n = sum(f.level == lv for f in found)
            pill.setText(f"{n} {lv if n == 1 else PLURAL[lv]}")
            pill.setVisible(n > 0)
        if self.auto.isChecked() != self.studio.findings.auto:
            self.auto.blockSignals(True)
            self.auto.setChecked(self.studio.findings.auto)
            self.auto.blockSignals(False)
        self.list.set_items(
            [kit.Item(line(f), f if f.target is not None else None, tip(f), f.level) for f in found]
        )
