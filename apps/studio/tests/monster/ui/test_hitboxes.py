# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path
from typing import Any

import pytest
from mhfu_studio.monster.panels.hitboxes import HitboxesPanel
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell.studio import Studio
from mhfu_studio.ui import kit
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton


def build(ws: MonsterWorkspace, qtbot: Any) -> HitboxesPanel:
    studio = Studio([ws])
    p = HitboxesPanel(ws, studio)
    qtbot.addWidget(p)
    studio.listen(p.sync)
    p.sync()
    return p


def on_port(ws: MonsterWorkspace, qtbot: Any, set_index: int = 2) -> HitboxesPanel:
    """The port's hitboxes shown, set `set_index` picked."""
    p = build(ws, qtbot)
    qtbot.mouseClick(p.source.buttons["port"], Qt.MouseButton.LeftButton)
    p.draw.click()
    p.sets.picked.emit(set_index)
    return p


def test_empty(qtbot: Any) -> None:
    p = build(MonsterWorkspace(), qtbot)
    assert p.pages.currentWidget() is p.pages.empty and kit.missing_tips(p) == []


def test_host_sets(workspace: MonsterWorkspace, qtbot: Any) -> None:
    p = build(workspace, qtbot)
    assert kit.missing_tips(p) == [] and p.sets.rowCount() == 4
    assert p.to_port.isVisibleTo(p) and not p.edit_box.isVisibleTo(p)
    assert "MEASURED" in p.join.text()


def test_edit_hitbox_and_undo(workspace: MonsterWorkspace, qtbot: Any) -> None:
    p = on_port(workspace, qtbot)
    assert workspace.doc is not None and workspace.vp is not None
    assert workspace.vp.attacks is not None and workspace.selected_set == 2
    assert p.vols.rowCount() == 1
    p.vols.cellClicked.emit(0, 0)
    assert workspace.selected_attack_volume == 0 and p.edit_box.isVisibleTo(p)
    p.form.radius.setValue(60.0)
    assert workspace.doc.dirty and workspace.doc.manifest.hitboxes[0].radius == 60.0
    assert workspace.vp.attacks.volumes[0].radius == 60.0 and p.vols.item(0, 0).text() == "0 *"
    p.studio.undo()  # the window's Edit > Undo
    assert workspace.doc.manifest.hitboxes[0].radius == 30.0 and p.form.radius.value() == 30.0
    next(b for b in p.form.findChildren(QPushButton) if b.text() == "×2").click()
    assert workspace.doc.manifest.hitboxes[0].radius == 60.0
    workspace.vp.select_joint(1)
    p.sync()
    p.form.joint.click()
    assert workspace.doc.manifest.hitboxes[0].bone == 1
    p.save.save.click()
    assert not workspace.doc.dirty


def test_offset_and_capsule(workspace: MonsterWorkspace, qtbot: Any) -> None:
    p = on_port(workspace, qtbot)
    assert workspace.doc is not None
    p.vols.picked.emit(0)
    p.form.offset.boxes[1].setValue(25.0)
    assert workspace.doc.manifest.hitboxes[0].offset == [0.0, 25.0, 0.0]
    qtbot.mouseClick(p.form.shape.buttons["capsule"], Qt.MouseButton.LeftButton)
    hb = workspace.doc.manifest.hitboxes[0]
    assert hb.shape == "capsule" and hb.to == [0.0, 0.0, 200.0] and p.form.to.isVisibleTo(p)


def test_adopt_an_empty_set(workspace: MonsterWorkspace, qtbot: Any) -> None:
    p = on_port(workspace, qtbot, 3)
    assert workspace.doc is not None
    assert p.set_hint.isVisibleTo(p) and "nothing authored" in p.set_hint.text()
    p.adopt_set.click()
    assert [h.set for h in workspace.doc.manifest.hitboxes] == [2, 3] and p.vols.rowCount() == 1


def test_levers(workspace: MonsterWorkspace, qtbot: Any) -> None:
    p = on_port(workspace, qtbot)
    assert workspace.doc is not None
    assert p.records.rowCount() == 1 and p.records.item(0, 1).text() == "40"  # type: ignore[union-attr]
    p.records.item(0, 2).setText("0x20")  # type: ignore[union-attr]
    assert workspace.doc.manifest.attacks[0].element == 0x20
    p.records.item(0, 1).setText("64")  # type: ignore[union-attr]
    assert workspace.doc.manifest.attacks[0].power is None
    p.records.setCurrentCell(0, 0)
    p.to_host.click()
    assert workspace.doc.manifest.attacks == []


def test_move_sets_and_keep_only(workspace: MonsterWorkspace, qtbot: Any) -> None:
    workspace.select_pair(1, 4)
    p = on_port(workspace, qtbot)
    assert workspace.doc is not None and p.move_only.isVisibleTo(p) and p.adopt_pair.isVisibleTo(p)
    p.sets.picked.emit(2)  # unpick
    p.adopt_pair.click()
    assert [h.set for h in workspace.doc.manifest.hitboxes] == [2, 2]
    workspace.select_set(2)
    workspace.select_attack_volume(1)
    p.sync()
    assert p.vols.currentRow() == 1 and p.form.bone.value() == 1
    p.keep.click()
    assert len(workspace.doc.manifest.hitboxes) == 1 and workspace.selected_attack_volume == 0


def test_export_and_deploy(
    workspace: MonsterWorkspace, qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = on_port(workspace, qtbot)
    mods = tmp_path / "mods"
    mods.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(MonsterWorkspace, "mods_dir", staticmethod(lambda: mods))
    p.sync()
    assert p.export.deploy.isEnabled()
    p.export.deploy.click()
    assert (mods / "t_hit.lua").is_file() and not (tmp_path / "t_hit.lua").exists()
    assert workspace.message.startswith("sent t_hit.lua")
