# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from typing import Any

import pytest
from mhfu.files import Extracted
from mhfu_studio.map.core.atlas import Atlas
from mhfu_studio.map.core.edit import COLLISION, CollisionSelection, Selection
from mhfu_studio.map.panels.collision import CollisionPanel
from mhfu_studio.map.panels.common import NO_AREA, NO_DATA
from mhfu_studio.map.workspace import MapWorkspace
from mhfu_studio.shell.studio import Studio
from mhfu_studio.ui import kit
from PySide6.QtWidgets import QPushButton


@pytest.fixture
def panel(qtbot: Any, ws: MapWorkspace, studio: Studio) -> CollisionPanel:
    p = CollisionPanel(ws, studio)
    qtbot.addWidget(p)
    p.sync()
    return p


def press(panel: CollisionPanel, text: str) -> None:
    panel.sync()
    b = next(b for b in panel.findChildren(QPushButton) if b.text() == text)
    assert b.isEnabled(), text
    b.click()
    panel.sync()


def pick(ws: MapWorkspace, *tris: tuple[int, int]) -> None:
    ws.tools.set_kind(COLLISION)
    ws.tools.select_collision(CollisionSelection(list(tris)))


def test_empty(qtbot: Any, game: Extracted, atlas: Atlas, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MHFU_DATA", raising=False)
    for w, title in ((MapWorkspace(), NO_DATA), (MapWorkspace(game, atlas), NO_AREA)):
        p = CollisionPanel(w, Studio([w]))
        qtbot.addWidget(p)
        p.sync()
        assert p.gate.empty.title.text() == title and kit.missing_tips(p) == []


def test_shows(panel: CollisionPanel, ws: MapWorkspace) -> None:
    assert ws.vp is not None and ws.vp.collision is not None
    assert kit.missing_tips(panel) == []
    assert sorted(panel.chunk_checks) == [0, 1] and "4 triangles" in panel.chunk_checks[1].text()
    assert "grid of 5 x 5 cells" in panel.chunk_checks[1].toolTip()
    assert (
        panel.classes["climb"].text() == "climbable  (1)"
        and not panel.classes["climb"].icon().isNull()
    )
    assert panel.climb_text.text() == "1 climbable triangle" and panel.surface.isHidden()
    more = panel.findChild(kit.More)
    assert more is not None and all(more.isAncestorOf(w) for w in (panel.chunk_box, *panel.flags))
    assert not panel.hint.isHidden() and panel.edit.isHidden()
    assert not any(w.isEnabled() for w in panel.filters)  # drawn only while shown
    ws.tools.show_layer(True)
    panel.sync()
    assert all(w.isEnabled() for w in panel.filters)
    panel.classes["floor"].setChecked(False)
    panel.chunk_checks[0].setChecked(False)
    panel.fill.slider.setValue(1000)
    assert not ws.vp.collision.show_class["floor"] and not ws.vp.collision.show_chunk[0]
    assert ws.vp.collision.fill_alpha == 1.0
    xray, edges = ws.vp.collision_xray, ws.vp.collision_edges
    panel.xray.click()
    panel.edge_on.click()
    assert ws.vp.collision_xray != xray and ws.vp.collision_edges != edges


def test_climbable(panel: CollisionPanel, ws: MapWorkspace) -> None:
    assert ws.vp is not None
    target = ws.vp.camera.target.copy()
    press(panel, "Frame them")
    assert (ws.vp.camera.target != target).any()
    press(panel, "Select them")
    assert ws.tools.kind == COLLISION and ws.col_sel.tris == [(0, 0)]


def test_one_triangle(panel: CollisionPanel, ws: MapWorkspace) -> None:
    assert ws.scene is not None and ws.session is not None
    pick(ws, (0, 1))
    panel.sync()
    assert panel.hint.isHidden() and not panel.one.isHidden()
    assert (
        "wall triangle 1: wall" in panel.info.text()
        and panel.what.text() == "1 collision triangle: wall"
    )
    assert panel.verts[0].value() == pytest.approx([1500.0, 0.0, 300.0])
    press(panel, "Climbable")
    assert (0, 1) in ws.scene.climbable() and panel.flags[1].value() == 10
    press(panel, "Not climbable")
    assert (0, 1) not in ws.scene.climbable()
    panel.verts[1].boxes[1].setValue(900.0)
    press(panel, "Apply corners")
    assert ws.scene.chunk(0).verts[1][1][1] == pytest.approx(900.0)
    normal = ws.scene.chunk(0).normals[1].copy()
    press(panel, "Flip")
    assert ws.scene.chunk(0).normals[1] == pytest.approx(-normal, abs=1e-4)
    press(panel, "Delete")
    assert not ws.scene.chunk(0).alive[1]


def test_flags_on_many(panel: CollisionPanel, ws: MapWorkspace) -> None:
    assert ws.scene is not None
    pick(ws, (1, 0), (1, 1))
    panel.sync()
    assert panel.one.isHidden() and "2 triangles; materials [3]" in panel.info.text()
    assert "too flat to climb" in panel.warn.text()
    panel.flags[1].setValue(7)
    press(panel, "Apply flags")
    assert ws.scene.chunk(1).material[0] == 7 and ws.scene.chunk(1).material[1] == 7
    press(panel, "Rock wall")
    assert "too flat to climb" in ws.message


def test_new_collision(panel: CollisionPanel, ws: MapWorkspace) -> None:
    assert ws.scene is not None and ws.session is not None
    assert not panel.box.isEnabled()
    ws.tools.select(Selection.face(ws.scene, (0, 1), 0))
    panel.add_flags[1].setValue(10)
    press(panel, "Turn 1 selected face into collision")
    op = ws.session.ops[-1]
    assert op["op"] == "collision" and len(op["add"]) == 1 and op["flags"]["material"] == 10
    panel.add_chunk.setCurrentIndex(panel.add_chunk.findData("1"))
    panel.inflate.setValue(20.0)
    press(panel, "Box collider")
    op = ws.session.ops[-1]
    assert op["chunk"] == 1 and op["inflate"] == 20.0 and "solid_box" in op
