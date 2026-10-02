# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from typing import Any

import pytest
from mhfu.files import Extracted
from mhfu_studio.map.core.atlas import Atlas, Section, stage_name
from mhfu_studio.map.panels.browser import BrowserPanel, section_text
from mhfu_studio.map.workspace import MapWorkspace
from mhfu_studio.shell.studio import Studio
from mhfu_studio.ui import kit
from PySide6.QtCore import Qt


def panel(qtbot: Any, ws: MapWorkspace) -> BrowserPanel:
    p = BrowserPanel(ws, Studio([ws]))
    qtbot.addWidget(p)
    p.sync()
    return p


def test_no_data(qtbot: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MHFU_DATA", raising=False)
    p = panel(qtbot, MapWorkspace())
    assert p.gate.currentWidget() is p.gate.empty and kit.missing_tips(p) == []


def test_unnamed_section() -> None:
    assert section_text(Section(140, 3, 0, stage_name(140), False)) == "3  st140"


def test_click_loads(qtbot: Any, game: Extracted, atlas: Atlas) -> None:
    ws = MapWorkspace(game, atlas)
    p = panel(qtbot, ws)
    assert kit.missing_tips(p) == [] and p.tree.topLevelItemCount() == 1
    assert p.here.isHidden()
    p.tree.itemClicked.emit(p._items[(0, 98)], 0)
    assert ws.scene is not None and (ws.scene.stage, ws.row) == (98, 0)
    p.sync()
    assert p._items[(0, 98)].font(0).bold() and not p.here.isHidden()
    assert "st098" in p.title.text() and "no exits" in p.arrivals.text()


def test_follows_the_workspace(qtbot: Any, game: Extracted, atlas: Atlas) -> None:
    ws = MapWorkspace(game, atlas)
    p = panel(qtbot, ws)
    ws.load_stage(98, row=0)
    p.sync()
    ws.load_stage(139, row=0)
    p.sync()
    bold = [k for k, it in p._items.items() if it.font(0).bold()]
    assert bold == [(0, 139)] and not p._items[(0, 139)].icon(0).isNull()
    assert not ws.load_stage(131) and ws.load_error
    p.sync()
    assert not p.error.isHidden() and p.error.text()


def test_real_exits(qtbot: Any, shipped: Extracted) -> None:
    ws = MapWorkspace(shipped)
    p = panel(qtbot, ws)
    assert ws.atlas is not None and p.tree.topLevelItemCount() == len(ws.atlas.live_rows())
    p.tree.itemClicked.emit(p._items[(11, 98)], 0)
    p.sync()
    assert p.exits.count() > 0 and "arrives here from" in p.arrivals.text()
    target = p.exits.item(0).data(Qt.ItemDataRole.UserRole)
    p.exits.itemClicked.emit(p.exits.item(0))
    assert ws.scene is not None and ws.scene.stage == target and ws.row == 11
