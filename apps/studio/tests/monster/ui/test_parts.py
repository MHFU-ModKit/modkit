# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from typing import Any

from mhfu_studio.monster.panels.parts import PartsPanel
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell.studio import Studio
from mhfu_studio.ui import kit
from PySide6.QtCore import Qt


def build(ws: MonsterWorkspace, qtbot: Any) -> PartsPanel:
    studio = Studio([ws])
    p = PartsPanel(ws, studio)
    qtbot.addWidget(p)
    studio.listen(p.sync)
    p.sync()
    return p


def on_port(ws: MonsterWorkspace, qtbot: Any) -> PartsPanel:
    p = build(ws, qtbot)
    qtbot.mouseClick(p.source.buttons["port"], Qt.MouseButton.LeftButton)
    p.draw.click()
    return p


def test_empty(qtbot: Any) -> None:
    p = build(MonsterWorkspace(), qtbot)
    assert p.pages.currentWidget() is p.pages.empty and kit.missing_tips(p) == []


def test_host_tables(workspace: MonsterWorkspace, qtbot: Any) -> None:
    p = build(workspace, qtbot)
    assert kit.missing_tips(p) == [] and p.parts.rowCount() == 8
    assert p.provenance.isVisibleTo(p) and p.to_port.isVisibleTo(p)
    assert "the HOST's" in p.grid_head.text() and not p.form_box.isVisibleTo(p)


def test_edit_volume_and_undo(workspace: MonsterWorkspace, qtbot: Any) -> None:
    ws, p = workspace, on_port(workspace, qtbot)
    assert ws.doc is not None and ws.vp is not None and ws.vp.hitboxes is not None
    p.vols.cellClicked.emit(0, 0)
    assert ws.selected_volume == 0 and p.form_box.isVisibleTo(p)
    p.form.radius.setValue(30.0)
    assert ws.doc.dirty and ws.doc.manifest.hurtboxes[0].radius == 30.0
    assert ws.vp.hitboxes.volumes[0].radius == 30.0 and p.vols.item(0, 0).text() == "0 *"
    p.studio.undo()  # the window's Edit > Undo
    assert ws.doc.manifest.hurtboxes[0].radius == 9.0 and p.form.radius.value() == 9.0
    p.part_box.setValue(3)
    assert ws.doc.manifest.hurtboxes[0].part == 3


def test_orphans_warn(workspace: MonsterWorkspace, qtbot: Any) -> None:
    p = on_port(workspace, qtbot)
    assert p.orphans.isVisibleTo(p) and "(40)" in p.orphans.text()


def test_grid_adopt_and_edit(workspace: MonsterWorkspace, qtbot: Any) -> None:
    ws, p = workspace, build(workspace, qtbot)
    assert ws.doc is not None
    p.adopt_grid.click()
    assert ws.parts_source == "port" and len(ws.doc.manifest.hitzones) == 1
    item = p.grid.item(2, 0)
    assert item is not None and item.flags() & Qt.ItemFlag.ItemIsEditable
    item.setText("77")
    assert ws.doc.manifest.hitzones[0].rows[2][0] == 77
    p.grid.setCurrentCell(3, 0)
    p.max_row.click()
    assert ws.doc.manifest.hitzones[0].rows[3][:4] == [3, 255, 255, 255]


def test_name_part(workspace: MonsterWorkspace, qtbot: Any) -> None:
    ws, p = workspace, build(workspace, qtbot)
    assert ws.doc is not None
    p.parts.cellClicked.emit(2, 0)
    assert ws.selected_part == 2 and p.name_row.isVisibleTo(p)
    p.part_name.setText("body")
    p.name_button.click()
    assert ws.doc.manifest.parts["body"].index == 2


def test_outside_pick_shows(workspace: MonsterWorkspace, qtbot: Any) -> None:
    ws, p = workspace, on_port(workspace, qtbot)
    ws.select_volume(1)
    p.sync()
    assert p.vols.currentRow() == 1 and p.form.bone.value() == 40


def test_adopt_volumes(workspace: MonsterWorkspace, qtbot: Any) -> None:
    ws, p = workspace, build(workspace, qtbot)
    assert ws.doc is not None
    p.adopt_vols.click()
    assert len(ws.doc.manifest.hurtboxes) == 5 and p.vols.rowCount() == 5
