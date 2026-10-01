# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path
from typing import Any

import pytest
from mhfu.files import Extracted
from mhfu_studio.map.adding import KINDS, PLACES
from mhfu_studio.map.core.atlas import Atlas
from mhfu_studio.map.core.edit import OBJECT, Selection
from mhfu_studio.map.panels.add import AddPanel
from mhfu_studio.map.panels.common import NO_DATA, NO_SECTION
from mhfu_studio.map.workspace import MapWorkspace
from mhfu_studio.shell.studio import Studio
from mhfu_studio.ui import kit
from PySide6.QtWidgets import QPushButton


@pytest.fixture
def panel(qtbot: Any, ws: MapWorkspace, studio: Studio) -> AddPanel:
    p = AddPanel(ws, studio)
    qtbot.addWidget(p)
    p.sync()
    return p


def press(panel: AddPanel, text: str) -> None:
    panel.sync()
    b = next(b for b in panel.findChildren(QPushButton) if b.text() == text)
    assert b.isEnabled(), text
    b.click()
    panel.sync()


def test_empty(qtbot: Any, game: Extracted, atlas: Atlas, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MHFU_DATA", raising=False)
    for w, title in ((MapWorkspace(), NO_DATA), (MapWorkspace(game, atlas), NO_SECTION)):
        p = AddPanel(w, Studio([w]))
        qtbot.addWidget(p)
        p.sync()
        assert p.gate.empty.title.text() == title and kit.missing_tips(p) == []


def test_tips(panel: AddPanel) -> None:
    assert panel.gate.currentWidget() is panel.gate.page
    assert kit.missing_tips(panel) == []


def test_rows_follow_the_shape(panel: AddPanel, ws: MapWorkspace) -> None:
    rows = panel.what.layout_
    assert rows.isRowVisible(panel.dims) and not rows.isRowVisible(panel.radius)
    panel.shape.setCurrentIndex(KINDS.index("cylinder"))
    panel.shape.activated.emit(KINDS.index("cylinder"))
    panel.sync()
    assert ws.add.kind == "cylinder"
    assert rows.isRowVisible(panel.radius) and rows.isRowVisible(panel.tall)
    assert not rows.isRowVisible(panel.dims) and not rows.isRowVisible(panel.rotate)
    assert "Needs 32 triangles" in panel.fits.text()


def test_fields_write_the_form(panel: AddPanel, ws: MapWorkspace) -> None:
    panel.dims.boxes[0].setValue(250.0)
    panel.rotate.slider.setValue(750)
    panel.replace.setChecked(False)
    assert ws.add.size[0] == 250.0 and ws.add.rotate == pytest.approx(90.0)
    assert not ws.add.replace
    ws.add.radius = 321.0  # from elsewhere
    panel.sync()
    assert panel.radius.value() == 321.0


def test_placement(panel: AddPanel, ws: MapWorkspace) -> None:
    assert ws.scene is not None
    ws.tools.select(Selection.object(ws.scene, (0, 1), 1))
    panel.sync()
    x, y, z = ws.add.placement()
    assert panel.at_shown.text() == f"({x:.0f}, {y:.0f}, {z:.0f})"
    assert not panel.where.layout_.isRowVisible(panel.at)
    i = PLACES.index("typed")
    panel.place.setCurrentIndex(i)
    panel.place.activated.emit(i)
    panel.sync()
    assert panel.where.layout_.isRowVisible(panel.at)
    panel.at.boxes[1].setValue(77.0)
    assert ws.add.placement()[1] == 77.0


def test_add_needs_a_folder(panel: AddPanel, ws: MapWorkspace) -> None:
    press(panel, "Add")
    assert "save the document first" in panel.message.text()


def test_add_and_remove(panel: AddPanel, ws: MapWorkspace, doc_dir: Path) -> None:
    assert ws.scene is not None and ws.session is not None
    ws.document.save(doc_dir)
    ws.tools.select(Selection.object(ws.scene, (0, 1), 1))
    panel.sync()
    assert panel.group.currentText() == "sub0.g1"
    press(panel, "Add")
    assert ws.session.ops[-1]["op"] == "pack" and (doc_dir / "assets" / "box_1.obj").is_file()
    assert "added box_1.obj" in panel.message.text()
    assert ws.selection.n_vertices == 24 and ws.tools.kind == OBJECT
    press(panel, "Remove the selection")
    assert ws.session.ops[-1]["op"] == "clear"


def test_over_budget(panel: AddPanel, ws: MapWorkspace, doc_dir: Path) -> None:
    ws.document.save(doc_dir)
    ws.add.shape = KINDS.index("sphere")
    ws.add.rings, ws.add.segments = 16, 24
    panel.sync()
    assert panel.fits.property("level") == "error"
    press(panel, "Add")
    assert "refused" in panel.message.text() or "no free primitive" in panel.message.text()
