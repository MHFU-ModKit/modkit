# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from typing import Any

import pytest
from mhfu_studio.monster.panels.timeline import STRIP_TIP, TimelinePanel
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
    workspace.play_slot(1, 0.0)
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
    assert "impact" in panel.impact_note.text()


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
    assert "gate" in STRIP_TIP
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
        workspace.play_slot(1, 0.0)
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
