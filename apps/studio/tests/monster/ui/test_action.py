# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from typing import Any

import pytest
from mhfu_studio.monster import actions
from mhfu_studio.monster.panels.action import ActionsPanel
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell.studio import Studio
from mhfu_studio.ui import kit
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton, QWidget


def make(qtbot: Any, ws: MonsterWorkspace) -> ActionsPanel:
    """Shown, and synced on every change as the window would."""
    studio = Studio([ws])
    p = ActionsPanel(ws, studio)
    studio.listen(p.sync)
    qtbot.addWidget(p)
    p.resize(460, 900)
    p.show()
    qtbot.waitExposed(p)
    p.sync()
    return p


@pytest.fixture
def panel(qtbot: Any, workspace: MonsterWorkspace) -> ActionsPanel:
    return make(qtbot, workspace)


def click_row(t: kit.Table, data: object) -> None:
    row = next(
        r for r in range(t.rowCount()) if t.item(r, 0).data(Qt.ItemDataRole.UserRole) == data
    )
    t.scrollToItem(t.item(row, 0))
    rect = t.visualItemRect(t.item(row, 0))
    QTest.mouseClick(
        t.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, rect.center()
    )


def button(w: QWidget, text: str) -> QPushButton:
    """A shown button whose text starts so; rebuilt rows show theirs one loop turn later."""
    QTest.qWait(1)
    found = [b for b in w.findChildren(QPushButton) if b.text().startswith(text) and b.isVisible()]
    assert found, f"no {text!r} button among {[b.text() for b in w.findChildren(QPushButton)]}"
    return found[0]


def cells(t: kit.Table, row: int) -> list[str]:
    return [t.item(row, c).text() for c in range(t.columnCount())]


def test_empty(qtbot: Any) -> None:
    p = make(qtbot, MonsterWorkspace())
    assert p.pages.currentWidget() is p.no_scene and kit.missing_tips(p) == []


def test_rows(panel: ActionsPanel) -> None:
    assert kit.missing_tips(panel) == []
    assert panel.table.rowCount() == 5 and panel.count.text() == "5 of 5"
    assert cells(panel.table, 0) == ["charge (1,4)", "walk", "group 2", "clip too short"]
    assert cells(panel.table, 1)[:3] == ["(3,9) +1", "anim 14 · missing", "group 3"]
    assert not panel.picked.isVisible() and "Base monster: Tigrex (em75)" == panel.host_note.text()
    panel.filter.setText("missing")
    assert panel.table.rowCount() == 4
    panel.filter.setText("(3,10)")
    assert cells(panel.table, 0)[0] == "(3,9) +1", "an action listed alike is found"


def test_a_row_plays_from_the_start(panel: ActionsPanel, workspace: MonsterWorkspace) -> None:
    vp = workspace.vp
    assert vp is not None
    workspace.play_slot(2)
    workspace.seek(4.0)
    click_row(panel.table, (1, 4, "charge"))
    assert workspace.pair == (1, 4) and workspace.move == "charge"
    assert vp.clip is not None and vp.clip.slot == 1
    assert vp.playback.phase == 0.0 and vp.playback.playing
    assert panel.picked.isVisible() and panel.title.text() == "charge (1,4)"
    assert panel.plays.text().startswith("Plays walk (anim 1)")
    assert [m.label for m in workspace.markers if m.kind == "gate"] == ["5", "40"]
    assert panel.table.currentRow() == 0 and panel.dots.isVisible()


def test_the_base_monster_beside(panel: ActionsPanel, workspace: MonsterWorkspace) -> None:
    panel.beside.click()
    assert workspace.show_host and workspace.vp is not None and workspace.vp.reference is not None
    click_row(panel.table, (1, 4, "charge"))
    ref = workspace.vp.reference
    assert workspace.host_clip == 1 and ref.playback.phase == 0.0
    panel.beside.click()
    assert workspace.vp.reference is None


def test_change_clip_in_place(panel: ActionsPanel, workspace: MonsterWorkspace) -> None:
    m0 = workspace.manifest
    assert m0 is not None
    m0.moves["charge"].hold_max = 9  # what the bind keeps
    click_row(panel.table, (1, 4, "charge"))
    button(panel, "Change clip").click()
    assert panel.picker.isVisible() and panel.clips.rowCount() == 2
    assert not panel.use.isEnabled(), "the clip it plays already"
    click_row(panel.clips, 2)
    assert workspace.vp is not None and workspace.vp.clip is not None
    assert workspace.vp.clip.slot == 2 and workspace.vp.playback.phase == 0.0
    assert workspace.pair == (1, 4) and panel.preview.isVisible() and panel.use.isEnabled()
    panel.use.click()
    m = workspace.manifest
    assert m is not None and list(m.moves) == ["charge"]
    assert (m.moves["charge"].clip, m.moves["charge"].hold_max) == ("clip_02", 9)
    assert cells(panel.table, 0)[1] == "clip_02" and not panel.preview.isVisible()
    assert not panel.picker.isVisible(), "the list closes once the clip is used"


def test_use_names_a_new_move(panel: ActionsPanel, workspace: MonsterWorkspace) -> None:
    click_row(panel.table, (0, 3, None))
    assert workspace.move is None and panel.plays.text() == "Plays anim 9 · missing"
    assert not panel.unbind.isVisible() and not panel.name_form.isVisible()
    button(panel, "Change clip").click()
    click_row(panel.clips, 1)
    panel.use.click()
    m = workspace.manifest
    assert m is not None and m.moves["move_0_3"].clip == "walk" and workspace.move == "move_0_3"
    assert ("move_0_3 (0,3)", "walk") == tuple(cells(panel.table, 1)[:2])
    panel.name.setText("skid")
    panel.name.editingFinished.emit()
    assert "skid" in workspace.manifest.moves and workspace.move == "skid"  # type: ignore[union-attr]
    panel.unbind.click()
    assert list(workspace.manifest.moves) == ["charge"]  # type: ignore[union-attr]
    assert workspace.pair == (0, 3) and workspace.move is None


def test_set_impact_updates_the_timing(panel: ActionsPanel, workspace: MonsterWorkspace) -> None:
    click_row(panel.table, (1, 4, "charge"))
    workspace.seek(6.0)
    panel.studio.act("impact", workspace.set_impact_here)()
    assert workspace.manifest is not None and workspace.manifest.clips["walk"].impact_frame == 6
    assert cells(panel.table, 0)[3] == "clip too short", "the late check wins"
    assert [m.kind for m in workspace.markers].count("impact") == 1


def test_copy_lua(panel: ActionsPanel, workspace: MonsterWorkspace) -> None:
    assert workspace.manifest is not None
    text = actions.lua_moves(workspace.manifest)
    assert panel.lua.text() == text and "charge = { main = 1, sub = 4" in text
    assert "not from this file" in panel.lua_note.text()
    panel.copy.click()
    assert QGuiApplication.clipboard().text() == text + "\n"
    assert workspace.message.startswith("copied 1 moves as Lua")


def test_more_has_the_graph_and_every_action(
    panel: ActionsPanel, workspace: MonsterWorkspace
) -> None:
    assert not panel.graph.isVisible()
    panel.more.set_open(True)
    assert panel.graph.isVisible() and panel.pairs.rowCount() == 8
    click_row(panel.pairs, (1, 3))
    assert workspace.pair == (1, 3) and "code at 0x00000100" in panel.handler.text()
    assert (1, 3) in panel.graph.canvas.nodes and panel.graph.canvas.picked == (1, 3)
    button(panel.then, "(0,1)").click()
    assert workspace.pair == (0, 1)


def test_browsing_another_monster(panel: ActionsPanel, workspace: MonsterWorkspace) -> None:
    panel.more.set_open(True)
    i = panel.species.findData(7)
    panel.species.setCurrentIndex(i)
    panel.species.activated.emit(i)
    assert workspace.browse == 7 and panel.browsing.isVisible() and not panel.pairs.isVisible()
    click_row(panel.table, (1, 4, "charge"))
    assert workspace.browse == 75 and workspace.pair == (1, 4), "a row is the base monster's"


def test_hosts_compared(panel: ActionsPanel, workspace: MonsterWorkspace) -> None:
    panel.more.set_open(True)
    panel.compare.click()
    assert panel.hosts.isVisible() and panel.hosts.rowCount() == 1 and not panel.poll.isActive()
    click_row(panel.hosts, 75)
    assert workspace.browse == 75


def test_edit_set_in_hitboxes(panel: ActionsPanel, workspace: MonsterWorkspace) -> None:
    click_row(panel.table, (1, 4, "charge"))
    assert "attack 6 (power 64) using hit group 2" in panel.hits_text.text()
    button(panel.hits, "Edit hit group 2").click()
    assert workspace.selected_set == 2 and workspace.take_focus() == "Hitboxes"


def test_findings_in_more(panel: ActionsPanel, workspace: MonsterWorkspace) -> None:
    click_row(panel.table, (1, 4, "charge"))
    al = workspace.alignment
    assert al is not None and al.errors and panel.dots.level == "error"
    assert not any(f.code in panel.dots.text() for f in al.findings), "codes go in the tips"
    panel.more.set_open(True)
    assert not panel.findings.isVisible()
    panel.show_findings.click()
    assert panel.findings.isVisible() and panel.findings.findChildren(QWidget)
