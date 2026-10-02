# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from typing import Any

import pytest
from mhfu.files import Extracted
from mhfu_studio.map.core.atlas import Atlas
from mhfu_studio.map.core.edit import COLLISION, CollisionSelection, Selection
from mhfu_studio.map.panels.common import NO_AREA, NO_DATA
from mhfu_studio.map.panels.selection import SelectionPanel
from mhfu_studio.map.workspace import MapWorkspace
from mhfu_studio.shell.studio import Studio
from mhfu_studio.ui import kit
from PySide6.QtWidgets import QPushButton


@pytest.fixture
def panel(qtbot: Any, ws: MapWorkspace, studio: Studio) -> SelectionPanel:
    p = SelectionPanel(ws, studio)
    qtbot.addWidget(p)
    p.sync()
    return p


def test_empty(qtbot: Any, game: Extracted, atlas: Atlas, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MHFU_DATA", raising=False)
    for w, title in ((MapWorkspace(), NO_DATA), (MapWorkspace(game, atlas), NO_AREA)):
        p = SelectionPanel(w, Studio([w]))
        qtbot.addWidget(p)
        p.sync()
        assert p.gate.currentWidget() is p.gate.empty and p.gate.empty.title.text() == title
        assert kit.missing_tips(p) == []


def test_tips(panel: SelectionPanel) -> None:
    assert panel.gate.currentWidget() is panel.gate.page
    assert kit.missing_tips(panel) == []


def test_nothing_selected(panel: SelectionPanel) -> None:
    assert panel.what.text() == "nothing selected" and "Click something" in panel.stats.text()
    assert not panel.frame.isEnabled() and not panel.transform.isEnabled()
    assert "triangles drawn" in panel.budget.text() and not panel.undo.isEnabled()
    assert panel.count.text() == "No edits yet"


def test_transform(panel: SelectionPanel, ws: MapWorkspace) -> None:
    assert ws.scene is not None and ws.session is not None
    ws.tools.select(Selection.object(ws.scene, (0, 1), 1))
    panel.sync()
    assert "group 1: 1 object(s)" in panel.what.text() and "centre (" in panel.stats.text()
    assert "group 1: material 1" in panel.groups.text() and panel.remove.isEnabled()
    panel.by.boxes[0].setValue(100.0)
    panel.factor.boxes[1].setValue(2.0)
    press(panel, "Apply")
    op = ws.session.ops[0]
    assert op["by"][0] == pytest.approx(100.0) and op["scale"][1] == pytest.approx(2.0)
    assert panel.count.text() == "1 edit in 1 step" and "transform" in panel.ops.item(0).text()
    assert panel.undo.isEnabled()
    press(panel, "Reset fields")
    assert panel.by.value() == [0.0, 0.0, 0.0] and panel.factor.value() == [1.0, 1.0, 1.0]


def test_undo_redo(panel: SelectionPanel, ws: MapWorkspace) -> None:
    assert ws.scene is not None and ws.session is not None
    ws.tools.select(Selection.object(ws.scene, (0, 1), 1))
    ws.apply_numeric([0.0, 50.0, 0.0], [0.0, 0.0, 0.0], [1.0, 1.0, 1.0])
    panel.sync()  # an edit from elsewhere shows
    assert panel.ops.count() == 1
    press(panel, "Undo")
    assert (
        not ws.session.ops and panel.redo.isEnabled() and panel.ops.item(0).text().startswith("No")
    )
    press(panel, "Redo")
    assert len(ws.session.ops) == 1


def test_frame_clear_remove(panel: SelectionPanel, ws: MapWorkspace) -> None:
    assert ws.scene is not None and ws.session is not None and ws.vp is not None
    ws.tools.select(Selection.object(ws.scene, (0, 1), 1))
    panel.sync()
    target = ws.vp.camera.target.copy()
    press(panel, "Frame")
    assert (ws.vp.camera.target != target).any()
    press(panel, "Remove")
    assert ws.session.ops[-1]["op"] == "clear" and ws.selection.empty
    ws.tools.select(Selection.object(ws.scene, (0, 1), 0))
    press(panel, "Remove + collision")
    assert ws.session.ops[-1]["op"] == "clear" and ws.session.ops[-1].get("solid")
    ws.tools.select(Selection.object(ws.scene, (0, 1), 0))
    press(panel, "Clear")
    assert ws.selection.empty


def test_collision_kind(panel: SelectionPanel, ws: MapWorkspace) -> None:
    assert ws.session is not None
    ws.tools.set_kind(COLLISION)
    ws.tools.select_collision(CollisionSelection([(1, 0)]))
    panel.sync()
    assert panel.what.text().startswith("collision:") and "Collision panel" in panel.stats.text()
    assert not panel.remove.isEnabled() and panel.transform.isEnabled()
    panel.by.boxes[2].setValue(30.0)
    press(panel, "Apply")
    assert ws.session.ops[-1]["op"] == "collision" and "verts" in ws.session.ops[-1]


def press(panel: SelectionPanel, text: str) -> None:
    """Clicks the button `text` as the window would: synced before and after."""
    panel.sync()
    b = next(b for b in panel.findChildren(QPushButton) if b.text() == text)
    assert b.isEnabled(), text
    b.click()
    panel.sync()
