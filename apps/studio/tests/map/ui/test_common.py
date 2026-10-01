# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from typing import Any

import pytest
from mhfu.files import Extracted
from mhfu_studio.map.core.atlas import Atlas
from mhfu_studio.map.panels.common import NO_DATA, NO_SECTION, Gate, thumbnail
from mhfu_studio.map.workspace import MapWorkspace
from PySide6.QtWidgets import QLabel


def test_gate(qtbot: Any, game: Extracted, atlas: Atlas, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MHFU_DATA", raising=False)
    page = QLabel("page")
    gate = Gate(page, "see it")
    qtbot.addWidget(gate)
    assert not gate.check(MapWorkspace())
    assert gate.empty.title.text() == NO_DATA and "MHFU_DATA" in gate.empty.hint.text()
    ws = MapWorkspace(game, atlas)
    assert gate.check(ws, section=False) and gate.currentWidget() is page
    assert not gate.check(ws) and gate.empty.title.text() == NO_SECTION
    assert "to see it." in gate.empty.hint.text()
    ws.load_stage(139)
    assert gate.check(ws) and gate.currentWidget() is page


def test_thumbnail(qapp: Any, game: Extracted, atlas: Atlas) -> None:
    ws = MapWorkspace(game, atlas)
    ws.load_stage(139)
    assert ws.scene is not None
    wide = ws.scene.texture(1)
    assert wide is not None and (wide.width, wide.height) == (32, 8)
    pix = thumbnail(wide, 64)
    assert (pix.width(), pix.height()) == (64, 16)
