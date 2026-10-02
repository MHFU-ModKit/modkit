# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from typing import Any

import pytest
from mhfu_studio.monster.panels.action import ActionPanel
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell.studio import Studio
from mhfu_studio.ui import kit
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton, QWidget


def make(qtbot: Any, ws: MonsterWorkspace) -> ActionPanel:
    """Shown, and synced on every change as the window would."""
    studio = Studio([ws])
    p = ActionPanel(ws, studio)
    studio.listen(p.sync)
    qtbot.addWidget(p)
    p.resize(1000, 700)
    p.show()
    qtbot.waitExposed(p)
    p.sync()
    return p


@pytest.fixture
def panel(qtbot: Any, workspace: MonsterWorkspace) -> ActionPanel:
    workspace.play_slot(1)
    return make(qtbot, workspace)


def click_row(t: kit.Table, data: object) -> None:
    row = next(
        r for r in range(t.rowCount()) if t.item(r, 0).data(Qt.ItemDataRole.UserRole) == data
    )
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


def test_empty(qtbot: Any) -> None:
    p = make(qtbot, MonsterWorkspace())
    assert p.pages.currentWidget() is p.no_scene and kit.missing_tips(p) == []


def test_loaded(panel: ActionPanel) -> None:
    assert kit.missing_tips(panel) == []
    assert panel.pages.currentWidget() is panel.split and not panel.align.isVisible()
    assert panel.species.currentText() == "Tigrex (em75), base" and not panel.browsing.isVisible()
    assert not panel.species.isVisible() and "Tigrex (em75)" in panel.host_note.text()
    assert panel.pairs.rowCount() == 8 and panel.count.text().startswith("8 of 8 actions")
    assert panel.moves.rowCount() == 1 and panel.moves.item(0, 0).text() == "charge"


def test_a_pair_from_the_table(panel: ActionPanel, workspace: MonsterWorkspace) -> None:
    click_row(panel.pairs, (1, 4))
    assert workspace.pair == (1, 4) and workspace.alignment is not None
    assert panel.align.isVisible() and "(1,4)" in panel.title.text()
    assert panel.pairs.currentRow() == next(
        r for r in range(8) if panel.pairs.item(r, 0).text() == "(1,4)"
    )
    assert "frames 5, 8, 40" in panel.headline.text()
    button(panel, "All actions").click()
    assert workspace.pair is None and not panel.align.isVisible()


def test_a_move_plays_its_clip(panel: ActionPanel, workspace: MonsterWorkspace) -> None:
    click_row(panel.moves, "charge")
    assert workspace.pair == (1, 4) and workspace.move == "charge"
    assert workspace.vp is not None and workspace.vp.clip is not None
    assert workspace.vp.clip.slot == 1


def test_filter(panel: ActionPanel) -> None:
    panel.filter.setText("stays")
    assert panel.pairs.rowCount() == 2 and panel.count.text().startswith("2 of 8")
    panel.filter.setText("")
    assert panel.pairs.rowCount() == 8


def test_bind(panel: ActionPanel, workspace: MonsterWorkspace) -> None:
    workspace.select_pair(1, 3)
    panel.sync()
    assert panel.bind_row.isVisible() and panel.bind_name.placeholderText() == "move_1_3"
    assert panel.bind_name.text() == ""
    panel.bind_name.setText("stop")
    panel.bind.click()
    assert workspace.manifest is not None and workspace.manifest.moves["stop"].sub == 3
    assert panel.moves.rowCount() == 2 and panel.bind_name.text() == "stop"


def test_rebind_in_place(panel: ActionPanel, workspace: MonsterWorkspace) -> None:
    click_row(panel.moves, "charge")
    assert panel.bind_name.text() == "charge"
    workspace.play_slot(2)
    panel.sync()
    assert "clip_02" in panel.bind_note.text()
    panel.bind.click()
    m = workspace.manifest
    assert m is not None and list(m.moves) == ["charge"] and m.moves["charge"].clip == "clip_02"


def test_browsing_another_host(panel: ActionPanel, workspace: MonsterWorkspace) -> None:
    i = panel.species.findData(7)
    panel.species.setCurrentIndex(i)
    panel.species.activated.emit(i)
    assert workspace.browse == 7 and panel.browsing.isVisible()
    assert panel.no_intel.isVisible() and "em07" in panel.no_intel.text()
    assert not panel.table_side.isVisible()


def test_hosts_compared(panel: ActionPanel, workspace: MonsterWorkspace) -> None:
    assert not panel.hosts.isVisible()
    panel.more.set_open(True)
    panel.compare.click()
    assert panel.hosts.isVisible() and panel.hosts.rowCount() == 1
    assert panel.hosts.item(0, 0).text() == "Tigrex (em75), base" and not panel.poll.isActive()
    click_row(panel.hosts, 75)
    assert workspace.browse == 75


def test_the_host_beside(panel: ActionPanel, workspace: MonsterWorkspace) -> None:
    workspace.select_pair(1, 4)
    panel.sync()
    panel.to_view.click()
    assert workspace.take_focus() == "View", "the base monster beside lives in View"
    workspace.set_show_host(True)
    panel.sync()
    assert workspace.vp is not None and workspace.vp.reference is not None
    b = button(panel.host_plays, "1")
    assert "●" in b.text()
    b.click()
    assert workspace.host_clip == 1


def test_hits_and_the_hand_offs(panel: ActionPanel, workspace: MonsterWorkspace) -> None:
    workspace.select_pair(1, 4)
    panel.sync()
    assert "attack 6 (power 64" in panel.hits_text.text()
    button(panel.then, "(0,3)").click()
    assert workspace.pair == (0, 3)
    button(panel, "Show in Moves").click()
    assert workspace.take_focus() == "Moves" and workspace.graph.picked == (0, 3)


def test_edit_set_in_hitboxes(panel: ActionPanel, workspace: MonsterWorkspace) -> None:
    workspace.select_pair(1, 4)
    panel.sync()
    assert "attack 6 (power 64, element 0x10) using hit group 2" in panel.hits_text.text()
    button(panel.hits, "Edit hit group 2").click()
    assert workspace.selected_set == 2 and workspace.take_focus() == "Hitboxes"


def test_findings_fold(panel: ActionPanel, workspace: MonsterWorkspace) -> None:
    workspace.select_pair(1, 4)
    panel.sync()
    al = workspace.alignment
    assert al is not None and al.findings
    assert panel.show_findings.isChecked() == bool(al.errors)
    assert not any(f.code in panel.dots.text() for f in al.findings), "codes go in the tips"
    assert panel.findings.isVisible() == bool(al.errors)
    panel.show_findings.click()
    assert panel.findings.isVisible() != bool(al.errors)
    assert panel.dots.isVisible() == bool(al.errors)
    assert panel.findings.findChildren(QWidget)


def test_species_effects(panel: ActionPanel, workspace: MonsterWorkspace) -> None:
    workspace.select_pair(1, 4)
    panel.more.set_open(True)
    panel.sync()
    assert panel.show_effects.isVisible() and not panel.effects_box.isVisible()
    panel.show_effects.click()
    panel.sync()
    assert panel.effects.rowCount() == 1 and panel.effects.item(0, 1).text() == "2"
    assert "joint 2" in panel.effects.item(0, 0).toolTip()


def test_narrow_puts_the_table_under(panel: ActionPanel, qtbot: Any) -> None:
    assert panel.split.orientation() == Qt.Orientation.Horizontal
    panel.resize(380, 700)
    qtbot.waitUntil(lambda: panel.split.orientation() == Qt.Orientation.Vertical)
