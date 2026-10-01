# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path
from typing import Any

from mhfu.files import Extracted
from mhfu_studio.map.core.atlas import Atlas
from mhfu_studio.map.core.edit import Selection
from mhfu_studio.map.panels.assets import AssetsPanel
from mhfu_studio.map.workspace import MapWorkspace
from mhfu_studio.shell.studio import Studio
from mhfu_studio.ui import kit


def panel(qtbot: Any, ws: MapWorkspace) -> AssetsPanel:
    studio = Studio([ws])
    p = AssetsPanel(ws, studio)
    qtbot.addWidget(p)
    studio.listen(p.sync)
    p.sync()
    return p


def pick(box: Any, data: str) -> None:
    i = box.findData(data)
    assert i >= 0, data
    box.setCurrentIndex(i)
    box.activated.emit(i)


def loaded(game: Extracted, atlas: Atlas) -> MapWorkspace:
    ws = MapWorkspace(game, atlas)
    ws.load_stage(139, row=0)
    return ws


def test_nothing_loaded(qtbot: Any, game: Extracted, atlas: Atlas) -> None:
    p = panel(qtbot, MapWorkspace(game, atlas))
    assert p.gate.currentWidget() is p.gate.empty and kit.missing_tips(p) == []


def test_lists(qtbot: Any, game: Extracted, atlas: Atlas) -> None:
    ws = loaded(game, atlas)
    p = panel(qtbot, ws)
    assert kit.missing_tips(p) == [] and p.section.count() == 2
    assert "(loaded)" in p.section.itemText(0)
    pick(p.section, "98")
    assert p.src == 98 and p.group.count() == 5 and p.slot_box.count() == 2
    pick(p.group, "0.1")
    assert p.object.count() == 2 and "72 f" in p.object.itemText(0)
    assert not p.unsaved.isHidden() and not p.copy_button.isEnabled()


def test_copy_object(qtbot: Any, game: Extracted, atlas: Atlas, doc_dir: Path) -> None:
    ws = loaded(game, atlas)
    ws.doc.save(doc_dir)
    sc, sess = ws.scene, ws.session
    assert sc is not None and sess is not None
    ws.tools.select(Selection.object(sc, (0, 1), 1))
    p = panel(qtbot, ws)
    pick(p.section, "98")
    pick(p.group, "0.3")
    assert p.into == "0.1" and "replacing the selection" in p.need.text()
    assert p.copy_button.isEnabled()
    p.copy_button.click()
    op = sess.ops[-1]
    assert op["op"] == "pack" and (op["sub"], op["group"]) == (0, 1)
    assert (doc_dir / op["obj"]).is_file() and op["obj"].startswith("assets/st098_g3")


def test_copy_texture(qtbot: Any, game: Extracted, atlas: Atlas) -> None:
    ws = loaded(game, atlas)
    p = panel(qtbot, ws)
    pick(p.section, "98")
    pick(p.slot_box, "1")
    ws.tex_target = 0
    p.sync()
    assert p.copy_tex.text() == "Copy into slot 0" and p.thumb.pixmap().width() == 96
    p.copy_tex.click()
    assert ws.session is not None
    assert ws.session.ops[-1] == {"op": "texture", "slot": 0, "from": {"stage": 98, "slot": 1}}
