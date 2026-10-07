# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from typing import Any

import pytest
from mhfu_port.manifest import AttackWindow
from mhfu_studio.monster.panels.timeline import LANE_H, STRIP_TIP, TimelinePanel
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell.studio import Studio
from mhfu_studio.ui import kit, theme
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

LEFT = Qt.MouseButton.LeftButton


def make(qtbot: Any, ws: MonsterWorkspace) -> TimelinePanel:
    """Shown, and synced on every change as the window would."""
    studio = Studio([ws])
    p = TimelinePanel(ws, studio)
    studio.listen(p.sync)
    qtbot.addWidget(p)
    p.resize(900, 320)
    p.show()
    qtbot.waitExposed(p)
    p.sync()
    return p


@pytest.fixture
def panel(qtbot: Any, workspace: MonsterWorkspace) -> TimelinePanel:
    workspace.play_slot(1)
    workspace.play_pause()
    return make(qtbot, workspace)


def test_empty(qtbot: Any, workspace: MonsterWorkspace) -> None:
    p = make(qtbot, MonsterWorkspace())
    assert p.empty.currentWidget() is p.no_scene and kit.missing_tips(p) == []
    assert workspace.vp is not None
    workspace.vp.play_clip(None)
    p = make(qtbot, workspace)
    assert p.empty.currentWidget() is p.no_clip and p.pages.currentWidget() is p.empty


def test_loaded(panel: TimelinePanel) -> None:
    assert kit.missing_tips(panel) == []
    assert panel.pages.currentWidget() is not panel.empty
    assert panel.frame.text() == "frame 0.0 / 10" and panel.loop.isChecked()
    assert panel.hint.isVisible() and not panel.strip.markers
    assert "impact" in panel.impact_note.text() and not panel.speed.isVisibleTo(panel)
    panel.more.set_open(True)
    assert panel.speed.isVisibleTo(panel) and kit.missing_tips(panel) == []


def test_transport(panel: TimelinePanel, workspace: MonsterWorkspace) -> None:
    assert workspace.vp is not None
    pb = workspace.vp.playback
    panel.play.click()
    assert pb.playing and panel.timer.isActive()
    panel.play.click()
    assert not pb.playing and not panel.timer.isActive()
    panel.fwd.click()
    assert pb.phase == pb.speed and panel.frame.text() == f"frame {pb.speed:.1f} / 10"
    panel.back.click()
    panel.rewind.click()
    assert pb.phase == 0.0
    panel.speed.setValue(1.5)
    assert pb.speed == 1.5
    panel.loop.click()
    assert not pb.loop
    panel.in_place.click()
    assert workspace.vp.strip_root


def test_playback_ends_by_itself(panel: TimelinePanel, workspace: MonsterWorkspace) -> None:
    assert workspace.vp is not None
    pb = workspace.vp.playback
    pb.loop = False
    panel.play.click()
    workspace.frame(1.0)
    panel._tick()
    assert not pb.playing and not panel.timer.isActive()
    assert panel.frame.text() == "frame 10.0 / 10"


def test_scrub(panel: TimelinePanel, workspace: MonsterWorkspace) -> None:
    assert workspace.vp is not None
    s = panel.strip
    x = round(s.x_of(6.0))
    QTest.mousePress(s, LEFT, Qt.KeyboardModifier.NoModifier, QPoint(x, 10))
    assert workspace.vp.playback.phase == pytest.approx(6.0, abs=0.2)
    QTest.mouseMove(s, QPoint(round(s.x_of(3.0)), 10))
    QTest.mouseRelease(s, LEFT, Qt.KeyboardModifier.NoModifier, QPoint(round(s.x_of(3.0)), 10))
    assert workspace.vp.frame == pytest.approx(3.0, abs=0.2)
    QTest.mousePress(s, LEFT, Qt.KeyboardModifier.NoModifier, QPoint(s.width() - 1, 10))
    assert workspace.vp.playback.phase == 10.0, "a scrub stops at the clip's end"


def test_markers_and_the_past_end(panel: TimelinePanel, workspace: MonsterWorkspace) -> None:
    workspace.select_pair(1, 4, "charge")
    panel.sync()
    s = panel.strip
    assert s.markers == workspace.markers and not panel.hint.isVisible()
    never = [m for m in s.markers if m.unreachable]
    assert [m.frame for m in never] == [40]
    assert s.span > s.end == 10.0, "the strip runs on past the clip"
    assert s.x_of(10.0) < s.x_of(40.0) <= s.width()
    assert s.marker_at(s.x_of(40.0), 10) is never[0]
    assert s.marker_at(s.x_of(40.0), 40) is None, "the legend row is not a marker"
    assert "hit check" in STRIP_TIP
    s.grab()  # paints


def test_set_impact(panel: TimelinePanel, workspace: MonsterWorkspace) -> None:
    workspace.seek(4.0)
    panel.sync()
    assert panel.impact.text() == "Set impact = frame 4"
    panel.impact.click()
    assert workspace.manifest is not None
    assert workspace.manifest.clips["walk"].impact_frame == 4
    assert panel.impact_note.text() == "impact at 4"


def test_sync_shows_an_outside_change(panel: TimelinePanel, workspace: MonsterWorkspace) -> None:
    workspace.step(1)
    assert panel.frame.text() == "frame 0.0 / 10", "nothing re-reads without a change"
    panel.sync()
    assert panel.frame.text() != "frame 0.0 / 10"


def test_labels_read_in_a_light_theme(qtbot: Any, workspace: MonsterWorkspace) -> None:
    theme.apply(theme.theme("Moss", False))
    try:
        workspace.play_slot(1)
        workspace.play_pause()
        workspace.select_pair(1, 4)
        p = make(qtbot, workspace)
        s = p.strip
        mk = next(m for m in s.markers if not m.unreachable)
        img = s.grab().toImage()
        under = img.pixelColor(round(s.x_of(mk.frame) + 2), 4)  # the label's chip
        strip = theme.color(theme.current().view)
        assert under.lightnessF() < strip.lightnessF() - 0.2
    finally:
        theme.apply(theme.theme("Ember", True))


def lane_drag(p: TimelinePanel, lane: int, a: float, b: float) -> None:
    lanes = p.lanes
    y = round((lane + 0.5) * LANE_H)
    pa, pb = QPoint(round(lanes.x_of(a)), y), QPoint(round(lanes.x_of(b)), y)
    QTest.mousePress(lanes, LEFT, Qt.KeyboardModifier.NoModifier, pa)
    QTest.mouseMove(lanes, (pa + pb) / 2)
    QTest.mouseMove(lanes, pb)
    QTest.mouseRelease(lanes, LEFT, Qt.KeyboardModifier.NoModifier, pb)


@pytest.fixture
def own(qtbot: Any, workspace: MonsterWorkspace) -> TimelinePanel:
    """The walk made an own move, paused at frame 0."""
    workspace.play_slot(1)
    workspace.new_move("stamp")
    workspace.vp.playback.pause()  # type: ignore[union-attr]
    return make(qtbot, workspace)


def test_no_lanes_without_an_own_move(panel: TimelinePanel) -> None:
    assert not panel.lanes.isVisibleTo(panel) and not panel.attack_row.isVisibleTo(panel)


def test_lanes_for_an_own_move(own: TimelinePanel) -> None:
    assert own.lanes.isVisible() and own.attack_row.isVisible() and kit.missing_tips(own) == []
    assert own.lanes.height() == int(LANE_H) + 2, "the lane to add on"


def test_two_windows_by_drag(own: TimelinePanel, workspace: MonsterWorkspace) -> None:
    own.attack_id.setValue(6)
    lane_drag(own, 0, 2.0, 5.0)
    own.sync()
    own.attack_id.setValue(7)
    assert workspace.own_move().attacks[0].id == 7, "the picked one's id"  # type: ignore[union-attr]
    workspace.picked_window = None
    own.sync()
    own.attack_id.setValue(6)
    lane_drag(own, 1, 9.0, 6.0)
    mv = workspace.own_move()
    assert mv is not None and mv.attacks == [AttackWindow(7, 2, 5), AttackWindow(6, 6, 9)]
    assert own.lanes.height() == int(LANE_H * 3) + 2
    lane_drag(own, 1, 9.0, 10.0)
    assert workspace.own_move().attacks[1] == AttackWindow(6, 6, 10)  # type: ignore[union-attr]
    own.lanes.grab()  # paints


def test_playhead_shows_what_is_out(own: TimelinePanel, workspace: MonsterWorkspace) -> None:
    own.attack_id.setValue(6)
    lane_drag(own, 0, 2.0, 5.0)
    workspace.seek(3.0)
    own.sync()
    assert own.attack_live.text() == "out: 6"
    workspace.seek(6.0)
    own.sync()
    assert own.attack_live.text() == "no attack out"
    assert "power 64, hit group 2" in own.attack_what.text()


def test_delete_key_removes(own: TimelinePanel, workspace: MonsterWorkspace) -> None:
    lane_drag(own, 0, 2.0, 5.0)
    assert workspace.picked_window == 0
    own.lanes.setFocus()
    QTest.keyClick(own.lanes, Qt.Key.Key_Delete)
    assert workspace.own_move().attacks == []  # type: ignore[union-attr]
