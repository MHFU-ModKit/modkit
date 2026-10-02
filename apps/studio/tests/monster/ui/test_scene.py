# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from typing import Any

import pytest
from mhfu_studio.monster.panels.scene import JointsPanel, ScenePanel, ViewPanel
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell.studio import Studio
from mhfu_studio.ui import kit
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton


def build(cls: Any, ws: MonsterWorkspace, qtbot: Any) -> Any:
    """The panel on a studio that syncs it after every action, as the window does."""
    studio = Studio([ws])
    p = cls(ws, studio)
    qtbot.addWidget(p)
    studio.listen(p.sync)
    p.sync()
    return p


@pytest.mark.parametrize("cls", [ScenePanel, ViewPanel, JointsPanel])
def test_empty(cls: Any, qtbot: Any) -> None:
    p = build(cls, MonsterWorkspace(), qtbot)
    assert p.pages.currentWidget() is p.pages.empty
    assert kit.missing_tips(p) == []


@pytest.mark.parametrize("cls", [ScenePanel, ViewPanel, JointsPanel])
def test_loaded(cls: Any, workspace: MonsterWorkspace, qtbot: Any) -> None:
    p = build(cls, workspace, qtbot)
    assert p.pages.currentWidget() is p.pages.page
    assert kit.missing_tips(p) == []


def test_scene_counts(workspace: MonsterWorkspace, qtbot: Any) -> None:
    p = build(ScenePanel, workspace, qtbot)
    assert p.name.text() == "t" and p.values["bones"][1].text() == "3"
    assert p.values["host"][1].text() == "Tigrex (em75)"
    assert not p.values["vertices"][1].isVisibleTo(p), "the mesh's counts are in More"


def test_revert_is_undone(workspace: MonsterWorkspace) -> None:
    studio, doc = Studio([workspace]), workspace.doc
    assert doc is not None
    doc.edit(lambda m: setattr(m.hurtboxes[0], "radius", 1.0))
    studio.revert()
    assert not doc.dirty and doc.manifest.hurtboxes[0].radius != 1.0
    studio.undo()
    assert doc.manifest.hurtboxes[0].radius == 1.0


def test_view_toggles(workspace: MonsterWorkspace, qtbot: Any) -> None:
    p, vp = build(ViewPanel, workspace, qtbot), workspace.vp
    assert vp is not None and vp.mesh is not None
    p.wire.click()
    qtbot.mouseClick(p.shading.buttons["flat"], Qt.MouseButton.LeftButton)
    assert vp.wireframe and vp.mesh.mode == 1
    yaw = vp.camera.yaw
    next(b for b in p.findChildren(QPushButton) if b.text() == "Side").click()
    assert vp.camera.yaw != yaw
    p.host.click()
    assert workspace.show_host and vp.reference is not None
    vp.show_ground = False
    p.sync()
    assert not p.ground.isChecked() and p.host.text() == "Base monster beside"
    assert not p.axes.isVisibleTo(p)
    p.more.set_open(True)
    assert p.axes.isVisibleTo(p) and p.fov.isVisibleTo(p)


def test_joints_pick_and_tag(workspace: MonsterWorkspace, qtbot: Any) -> None:
    p, vp = build(JointsPanel, workspace, qtbot), workspace.vp
    assert vp is not None and vp.mesh is not None
    p.table.cellClicked.emit(2, 1)
    assert vp.selected_joint == 2 and p.table.currentRow() == 2
    p.table.cellClicked.emit(2, 2)
    assert vp.selected_joint is None
    p.table.cellClicked.emit(1, 0)
    assert vp.mesh.tagged == (1,) and vp.selected_joint is None
    assert vp.skeleton is not None and vp.skeleton.fork is not None
    assert "(the fork)" in p.table.item(vp.skeleton.fork, 1).toolTip()
    assert p.head.text().startswith(f"fork {vp.skeleton.fork}")
    qtbot.mouseClick(p.isolate.buttons["only"], Qt.MouseButton.LeftButton)
    assert vp.mesh.isolate == 1
    vp.select_joint(0)
    vp.tag_joints(())
    p.sync()
    assert p.table.currentRow() == 0 and p._tags[1] == frozenset()
