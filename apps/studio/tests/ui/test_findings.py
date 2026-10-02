# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A finding's tooltip, and landing on the control that fixes it."""

from types import SimpleNamespace
from typing import Any

from mhfu_studio.shell.findings import Finding
from mhfu_studio.ui import findings, kit
from PySide6.QtWidgets import QScrollArea, QWidget


def test_tip_says_what_fixes_it() -> None:
    assert findings.tip(Finding("warning", "X", "m", "w", ("a", 1), "name")).endswith(
        "Click to go to what fixes it"
    )
    assert findings.tip(Finding("info", "Y", "m", fix="Do this.")) == "info: Y\nDo this."
    shown = findings.tip(Finding("error", "Z", "m", target=3, fix="Do that."))
    assert shown == "error: Z\nDo that.\nClick to show it"
    assert findings.tip(Finding("error", "Z", "m")) == "error: Z"


def test_land_opens_scrolls_and_focuses(qtbot: Any) -> None:
    p = kit.Panel()
    filler = QWidget()
    filler.setFixedHeight(900)
    more = kit.More(tip="hidden things")
    field = kit.text_field(tip="a name")
    more.body.addWidget(field)
    for w in (filler, more):
        p.body.addWidget(w)
    qtbot.addWidget(p)
    p.resize(240, 200)
    p.show()
    qtbot.waitExposed(p)
    ws = SimpleNamespace(landing="name")
    findings.take(ws, {"other": filler})
    assert ws.landing == "name", "not this panel's"
    findings.take(ws, {"name": field})
    assert ws.landing == "" and more.toggle.isChecked()
    qtbot.waitUntil(lambda: p.focusWidget() is field)
    area = p.findChild(QScrollArea)
    assert area is not None and area.verticalScrollBar().value() > 0
