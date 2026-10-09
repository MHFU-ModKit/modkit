# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from typing import Any

import pytest
from mhfu_port.behaviour import KINDS
from mhfu_port.manifest import Steer
from mhfu_studio.monster.panels.moves import MovesPanel
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell.studio import Studio
from mhfu_studio.ui import kit


def make(qtbot: Any, ws: MonsterWorkspace) -> MovesPanel:
    """Shown, and synced on every change as the window would."""
    studio = Studio([ws])
    p = MovesPanel(ws, studio)
    studio.listen(p.sync)
    qtbot.addWidget(p)
    p.resize(460, 900)
    p.show()
    qtbot.waitExposed(p)
    p.sync()
    return p


@pytest.fixture
def panel(qtbot: Any, workspace: MonsterWorkspace) -> MovesPanel:
    workspace.play_slot(1)
    return make(qtbot, workspace)


def rows(p: MovesPanel) -> list[list[str]]:
    t = p.table
    return [[t.item(r, c).text() for c in range(t.columnCount())] for r in range(t.rowCount())]


def test_empty(qtbot: Any) -> None:
    p = make(qtbot, MonsterWorkspace())
    assert p.pages.currentWidget() is p.no_scene and kit.missing_tips(p) == []


def test_new_from_the_clip_on_screen(panel: MovesPanel, workspace: MonsterWorkspace) -> None:
    assert rows(panel) == [["charge", "(1,4)", "walk", "the action's"]]
    assert not panel.editor.isVisible() and kit.missing_tips(panel) == []
    panel.new.click()
    assert workspace.move == "walk" and rows(panel)[1] == ["walk", "own move", "walk", "·"]
    assert panel.editor.isVisible() and panel.own_body.isVisible()
    assert panel.title.text() == "walk · own move" and not panel.play_clip.isVisible()
    assert kit.missing_tips(panel) == []


def test_fields(panel: MovesPanel, workspace: MonsterWorkspace) -> None:
    panel.new.click()
    panel.length.setValue(40)
    panel.hub.click()
    panel.sub.setValue(7)
    panel.host_attacks.click()
    panel.after.activated.emit(panel.after.findData("charge"))
    mv = workspace.own_move()
    assert mv is not None and (mv.length, mv.carrier, mv.host_attacks, mv.after) == (
        40,
        (0, 7),
        True,
        "charge",
    ), "the carrier starts at the base monster's first hub"
    panel.length.setValue(0)
    panel.hub.click()
    mv = workspace.own_move()
    assert mv is not None and (mv.length, mv.carrier) == (None, None)
    assert panel.length.text() == "until the clip ends" and not panel.main.isEnabled()


def test_steer(panel: MovesPanel, workspace: MonsterWorkspace) -> None:
    panel.new.click()
    assert not panel.angle.isVisible() and not panel.rate.isVisible()
    panel.turn.buttons["fixed"].click()
    assert panel.angle.isVisible() and panel.frames.value() == 5
    panel.angle.setValue(90.0)
    panel.walls.click()
    panel.dir.setValue(180.0)
    mv = workspace.own_move()
    assert mv is not None
    assert mv.steer == Steer("fixed", angle=90.0, frames=5, walls=False, dir=180.0)
    panel.turn.buttons["hunter"].click()
    assert panel.rate.isVisible() and not panel.angle.isVisible()
    assert panel.rate.text() == "the charge's"
    panel.rate.setValue(64)
    assert workspace.own_move().steer.rate == 64  # type: ignore[union-attr]


def test_rename_and_delete(panel: MovesPanel, workspace: MonsterWorkspace) -> None:
    panel.new.click()
    panel.name.setText("stomp")
    panel.name.editingFinished.emit()
    assert workspace.move == "stomp" and [r[0] for r in rows(panel)] == ["charge", "stomp"]
    panel.delete.click()
    assert workspace.move is None and [r[0] for r in rows(panel)] == ["charge"]
    assert not panel.editor.isVisible()


def test_attacks_and_findings(panel: MovesPanel, workspace: MonsterWorkspace) -> None:
    panel.new.click()
    workspace.next_window_id = 6
    workspace.press_window(0, 2.0)
    workspace.windows.release(5.0)
    workspace.next_window_id = 3
    workspace.press_window(1, 6.0)
    workspace.windows.release(9.0)
    panel.sync()
    assert panel.attacks.item(0).text() == "attack 6: frames 2-5 (power 64, hit group 2)"
    assert panel.attacks.item(1).text() == "attack 3: frames 6-9 (no record)"
    alerts = [w.text() for w in panel.found.findChildren(kit.Alert)]
    assert any("has no attack record 3" in a for a in alerts) and panel.found.isVisible()
    panel.attacks.picked.emit(0)
    assert workspace.picked_window == 0


def test_a_pair_move_points_to_actions(panel: MovesPanel, workspace: MonsterWorkspace) -> None:
    panel.table.picked.emit("charge")
    assert workspace.pair == (1, 4) and workspace.move == "charge"
    assert panel.pair_note.isVisible() and not panel.own_body.isVisible()
    assert "(1,4)" in panel.pair_note.text()


def test_force_says_what_the_block_says(panel: MovesPanel) -> None:
    assert panel.force.toolTip() == KINDS["force"].tip


def test_play_in_game_button(qtbot: Any, workspace: MonsterWorkspace) -> None:
    workspace.play_slot(1)
    p = make(qtbot, workspace)
    p.table.picked.emit("charge")
    assert not p.in_game.isEnabled() and "rides the base monster's" in p.game_hint.text()
    p.new.click()
    assert not p.in_game.isEnabled() and "nothing to build" in p.game_hint.text()
    assert not p.force.isEnabled() and '"!"' in p.force.toolTip()
    played: list[bool] = []
    workspace.play_move_blocker = lambda: None  # type: ignore[method-assign]
    workspace.play_move_in_game = played.append  # type: ignore[method-assign,assignment]
    q = make(qtbot, workspace)
    assert q.in_game.isEnabled() and q.force.isEnabled() and not q.game_hint.isVisible()
    q.in_game.click()
    q.force.click()
    q.in_game.click()
    assert played == [False, True]
