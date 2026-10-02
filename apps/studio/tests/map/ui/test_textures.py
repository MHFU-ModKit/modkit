# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path
from typing import Any

import pytest
from mhfu.files import Extracted
from mhfu_studio.map.core.atlas import Atlas
from mhfu_studio.map.panels.textures import TexturesPanel
from mhfu_studio.map.workspace import MapWorkspace
from mhfu_studio.shell.studio import Studio
from mhfu_studio.ui import kit
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QColorDialog, QFileDialog


def panel(qtbot: Any, ws: MapWorkspace) -> TexturesPanel:
    studio = Studio([ws])
    p = TexturesPanel(ws, studio)
    qtbot.addWidget(p)
    studio.listen(p.sync)
    p.sync()
    return p


def loaded(game: Extracted, atlas: Atlas) -> MapWorkspace:
    ws = MapWorkspace(game, atlas)
    ws.load_stage(139, row=0)
    return ws


def test_nothing_loaded(qtbot: Any, game: Extracted, atlas: Atlas) -> None:
    p = panel(qtbot, MapWorkspace(game, atlas))
    assert p.gate.currentWidget() is p.gate.empty and kit.missing_tips(p) == []


def test_grid_picks_the_target(qtbot: Any, game: Extracted, atlas: Atlas) -> None:
    ws = loaded(game, atlas)
    p = panel(qtbot, ws)
    assert kit.missing_tips(p) == [] and p.grid.count() == 2
    assert not p.grid.item(1).icon().isNull() and "1 group(s)" in p.grid.item(0).text()
    assert "group 0" in p.grid.item(0).toolTip() and p.grid.currentRow() == 0
    p.grid.itemClicked.emit(p.grid.item(1))
    assert ws.tex_target == 1 and p.grid.currentRow() == 1 and "32x8" in p.target.text()
    p.keep.click()
    assert ws.tex_keep
    ws.tex_target = 0
    p.sync()
    assert p.grid.currentRow() == 0 and p.from_stage.value() == 139


def test_imports(
    qtbot: Any, game: Extracted, atlas: Atlas, doc_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = loaded(game, atlas)
    p = panel(qtbot, ws)
    sess = ws.session
    assert sess is not None and not p.picture.isEnabled() and not p.unsaved.isHidden()
    ws.tex_target = 1
    monkeypatch.setattr(QColorDialog, "getColor", lambda *a: QColor(10, 20, 30))
    p.flat.click()
    assert sess.ops[-1] == {"op": "texture", "slot": 1, "rgb": [10, 20, 30]}
    before = ws.scene.textures if ws.scene else None
    p.from_stage.setValue(98)
    p.from_slot.setValue(0)
    p.copy.click()
    assert sess.ops[-1] == {"op": "texture", "slot": 1, "from": {"stage": 98, "slot": 0}}
    assert ws.scene is not None and ws.scene.textures is not before
    assert p._iconed is not None and p._iconed[0] is ws.scene.textures
    ws.doc.save(doc_dir)
    p.sync()
    assert p.picture.isEnabled() and p.unsaved.isHidden()
    png = doc_dir.parent / "pic.png"
    img = QImage(8, 8, QImage.Format.Format_RGBA8888)
    img.fill(QColor(200, 0, 0))
    assert img.save(str(png))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(png), ""))
    p.picture.click()
    op = sess.ops[-1]
    assert op["slot"] == 1 and (doc_dir / op["png"]).is_file()


def test_village_bank(qtbot: Any, shipped: Extracted) -> None:
    ws = MapWorkspace(shipped)
    ws.load_stage(139, row=0)
    p = panel(qtbot, ws)
    assert ws.scene is not None and p.grid.count() == len(ws.scene.textures) > 0
    assert all(not p.grid.item(i).icon().isNull() for i in range(p.grid.count()))
