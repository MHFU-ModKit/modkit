# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from typing import Any

import pytest
from mhfu.files import Extracted
from mhfu_studio.map.core.atlas import Atlas
from mhfu_studio.map.panels.common import NO_AREA, NO_DATA
from mhfu_studio.map.panels.groups import GroupsPanel
from mhfu_studio.map.workspace import MapWorkspace
from mhfu_studio.shell.studio import Studio
from mhfu_studio.ui import kit
from PySide6.QtWidgets import QPushButton


@pytest.fixture
def panel(qtbot: Any, ws: MapWorkspace, studio: Studio) -> GroupsPanel:
    p = GroupsPanel(ws, studio)
    qtbot.addWidget(p)
    p.sync()
    return p


def press(panel: GroupsPanel, text: str) -> None:
    panel.sync()
    b = next(b for b in panel.findChildren(QPushButton) if b.text() == text)
    assert b.isEnabled(), text
    b.click()
    panel.sync()


def row_of(panel: GroupsPanel, label: str) -> int:
    return next(r for r in range(panel.table.rowCount()) if panel.table.item(r, 0).text() == label)


def test_empty(qtbot: Any, game: Extracted, atlas: Atlas, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MHFU_DATA", raising=False)
    for w, title in ((MapWorkspace(), NO_DATA), (MapWorkspace(game, atlas), NO_AREA)):
        p = GroupsPanel(w, Studio([w]))
        qtbot.addWidget(p)
        p.sync()
        assert p.gate.empty.title.text() == title and kit.missing_tips(p) == []


def test_lists_groups(panel: GroupsPanel, ws: MapWorkspace) -> None:
    assert ws.scene is not None
    assert kit.missing_tips(panel) == []
    assert panel.table.rowCount() == len(ws.scene.groups)
    assert panel.summary.text().startswith("Model: 4 groups")
    more = panel.findChild(kit.More)
    assert more is not None and more.isAncestorOf(panel.backdrop) and more.inner.isHidden()
    assert panel.table.item(row_of(panel, "group 1"), 3).text() == "1"
    assert any("(far)" in panel.table.item(r, 0).text() for r in range(panel.table.rowCount()))
    panel.backdrop.setChecked(False)
    panel.sync()
    assert panel.table.rowCount() == len(ws.scene.groups) - 1
    assert panel.material.isHidden() and not panel.frame.isEnabled()


def test_pick_and_frame(panel: GroupsPanel, ws: MapWorkspace) -> None:
    assert ws.vp is not None
    panel.table.cellClicked.emit(row_of(panel, "group 1"), 0)
    panel.sync()
    assert ws.selected_group == (0, 1) and "group 1: material 1" in panel.details.text()
    assert panel.hint.isHidden() and not panel.material.isHidden()
    target = ws.vp.camera.target.copy()
    press(panel, "Frame")
    assert (ws.vp.camera.target != target).any()
    panel.table.cellClicked.emit(row_of(panel, "group 1"), 0)
    panel.sync()
    assert ws.selected_group is None and ws.selection.empty
    target = ws.vp.camera.target.copy()
    panel.table.cellDoubleClicked.emit(row_of(panel, "group 0"), 0)
    assert (ws.vp.camera.target != target).any()


def test_hide_and_show(panel: GroupsPanel, ws: MapWorkspace) -> None:
    assert ws.vp is not None and ws.vp.mesh is not None
    ws.select_group((0, 1))
    press(panel, "Hide")
    assert ws.vp.mesh.is_hidden((0, 1)) and panel.hide_button.text() == "Show"
    assert panel.table.item(row_of(panel, "group 1 (hidden)"), 0) is not None
    press(panel, "Show all")
    assert not ws.vp.mesh.is_hidden((0, 1)) and not panel.show_all.isEnabled()


def test_material(panel: GroupsPanel, ws: MapWorkspace) -> None:
    assert ws.session is not None
    ws.select_group((0, 1))
    panel.sync()
    assert not panel.material.isHidden()
    assert panel.slot.currentData() == "1" and panel.rgba[3].value() == 255
    panel.slot.setCurrentIndex(panel.slot.findData("0"))
    panel.rgba[0].setValue(10)
    press(panel, "Apply material")
    op = ws.session.ops[-1]
    assert op["op"] == "material" and op["texture"] == 0 and op["rgba"][0] == 10
    assert panel.slot.currentData() == "0" and panel.rgba[0].value() == 10
    ws.document.undo()
    ws.refresh()
    ws.frame(0.0)
    panel.sync()
    assert panel.slot.currentData() == "1"


def test_unresolved(panel: GroupsPanel, ws: MapWorkspace) -> None:
    assert ws.scene is not None
    g = next(g for g in ws.scene.groups if g.material is not None)
    g.material = None
    ws.select_group(g.key)
    panel.sync()
    assert not panel.apply.isEnabled() and not panel.unresolved.isHidden()
