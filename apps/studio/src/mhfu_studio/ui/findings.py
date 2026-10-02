# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The shell's Findings dock: what the checks found in the active workspace's document. A line
says where and what; its colour is the level, its tooltip the level and the code."""

from __future__ import annotations

from collections.abc import Callable, Hashable
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

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


def tip(f: Finding) -> str:
    return f"{f.level}: {f.code}" + ("\nClick to show it" if f.target is not None else "")


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

    def _reveal(self, target: Hashable) -> None:
        self.studio.act("show finding", lambda: self.studio.active.reveal(target))()

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
        self.list.set_items([kit.Item(line(f), f.target, tip(f), f.level) for f in found])
