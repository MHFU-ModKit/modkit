# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from typing import Any

from mhfu_studio.monster import clips
from mhfu_studio.monster.panels.clips import ClipsPanel
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell.studio import Studio
from mhfu_studio.ui import kit, theme


def build(ws: MonsterWorkspace, qtbot: Any) -> ClipsPanel:
    studio = Studio([ws])
    p = ClipsPanel(ws, studio)
    qtbot.addWidget(p)
    studio.listen(p.sync)
    p.sync()
    return p


def test_empty(qtbot: Any) -> None:
    p = build(MonsterWorkspace(), qtbot)
    assert p.pages.currentWidget() is p.pages.empty and kit.missing_tips(p) == []


def test_loaded(workspace: MonsterWorkspace, qtbot: Any) -> None:
    p = build(workspace, qtbot)
    assert p.pages.currentWidget() is p.pages.page and kit.missing_tips(p) == []
    assert p.table.rowCount() == 2 and p.count.text() == "2 clips, 1 looping"
    assert not p.kind_row.isVisibleTo(p) and p.notes.isVisibleTo(p)


def test_pick_name_save(workspace: MonsterWorkspace, qtbot: Any) -> None:
    ws, p = workspace, build(workspace, qtbot)
    assert ws.doc is not None and ws.scene is not None and ws.vp is not None
    p.table.cellClicked.emit(1, 0)
    assert ws.vp.clip is not None and ws.vp.clip.slot == 2 and ws.edit_slot == 2
    assert p.name.text() == "clip_02" and p.table.currentRow() == 1
    p.name.setText("strike")
    p.label.setText("the head comes down")
    p.apply.click()
    assert ws.doc.dirty and ws.doc.manifest.clips["strike"].slot == 2
    assert ws.scene.clip(2).names == ("strike",) and p.save.save.isVisibleTo(p)
    p.save.save.click()
    assert not ws.doc.dirty and not p.save.save.isVisibleTo(p)


def test_outside_pick_shows(workspace: MonsterWorkspace, qtbot: Any) -> None:
    ws, p = workspace, build(workspace, qtbot)
    ws.play_slot(1)
    p.sync()
    assert p.table.currentRow() == 0 and p.name.text() == "walk"


def test_filter(workspace: MonsterWorkspace, qtbot: Any) -> None:
    p = build(workspace, qtbot)
    p.filter.setText("walk")
    assert workspace.clip_filter == "walk" and p.table.rowCount() == 1


def test_filler_is_loud(workspace: MonsterWorkspace, qtbot: Any) -> None:
    ws = workspace
    cov = clips.Coverage(has_source=True)
    cov.slots[1] = clips.SlotCoverage(1, clips.CARRIED, 10, True)
    cov.slots[2] = clips.SlotCoverage(2, clips.FILLER, 6, False)
    ws._coverage, ws._vocab = (cov, []), None
    p = build(ws, qtbot)
    assert p.filler.isVisibleTo(p) and "FILLER" in p.filler.text()
    item = p.table.item(1, 0)
    assert item is not None and item.foreground().color() == theme.level("warning")
    assert "idle copy" in item.toolTip()
    p.table.cellClicked.emit(1, 0)
    assert p.why.property("level") == "warning"
