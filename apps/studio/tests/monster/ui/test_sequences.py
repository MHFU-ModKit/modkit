# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Sequences on screen: the Moves strip and picker, the Timeline's bar, playing a chain, the Clips
sort, over the synthetic port."""

import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from mhfu_port import continuity
from mhfu_port.manifest import Clip
from mhfu_studio.monster.clip_browser import SourceClip
from mhfu_studio.monster.core.scene import Scene
from mhfu_studio.monster.panels.clips import ClipsPanel
from mhfu_studio.monster.panels.moves import MovesPanel
from mhfu_studio.monster.panels.timeline import TimelinePanel
from mhfu_studio.monster.render.playback import pose_at
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell.studio import Studio
from mhfu_studio.ui import kit
from mhp_formats.anim import Channel, Keyframe, Track, quantize
from mhp_formats.anim import Clip as AnimClip
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest


def show(qtbot: Any, ws: MonsterWorkspace, cls: Any, size: tuple[int, int]) -> Any:
    """A panel shown and synced on every change, as the window would."""
    studio = Studio([ws])
    p = cls(ws, studio)
    studio.listen(p.sync)
    qtbot.addWidget(p)
    p.resize(*size)
    p.show()
    qtbot.waitExposed(p)
    p.sync()
    return p


@pytest.fixture
def ws(workspace: MonsterWorkspace) -> MonsterWorkspace:
    """The synthetic port with clips walk (anim 1, 10 frames) and roar (anim 2, 6) and the own
    moves a (walk), a_2 (roar), a_3 (walk), a sequence; a_3 selected."""
    assert workspace.doc is not None
    workspace.doc.edit(lambda m: m.clips.__setitem__("roar", Clip(slot=2, frames=6)))
    workspace.sync()
    workspace.play_slot(1)
    workspace.new_move("a")
    workspace.add_step("roar")
    workspace.add_step("walk")
    assert workspace.sequence_of() == ["a", "a_2", "a_3"] and workspace.move == "a_3"
    return workspace


@pytest.fixture
def moves(qtbot: Any, ws: MonsterWorkspace) -> MovesPanel:
    return show(qtbot, ws, MovesPanel, (460, 1000))


def names(p: MovesPanel) -> list[str]:
    t = p.table
    return [t.item(r, 0).text() for r in range(t.rowCount())]


def poses(*turns: float) -> continuity.Ends:
    """Clips 1, 2, ... each starting turned by `turns` degrees about x and ending at rest."""
    rad = np.radians(turns)
    c, s = np.cos(rad), np.sin(rad)
    first = np.zeros((len(turns), 1, 3, 3))
    first[:, 0] = np.eye(3)
    first[:, 0, 1, 1], first[:, 0, 1, 2], first[:, 0, 2, 1], first[:, 0, 2, 2] = c, -s, s, c
    last = np.broadcast_to(np.eye(3), first.shape).copy()
    return continuity.Ends(tuple(range(1, len(turns) + 1)), first, last)


def test_the_table_groups_a_sequence(moves: MovesPanel) -> None:
    assert names(moves) == ["charge", "a · 3 steps", "   └ a_2", "   └ a_3"]
    assert moves.table.item(1, 0).data(Qt.ItemDataRole.UserRole) == "a"
    assert moves.table.currentRow() == 3, "the picked step"
    assert kit.missing_tips(moves) == []


def test_the_strip_shows_the_chain(moves: MovesPanel, ws: MonsterWorkspace) -> None:
    chips = moves.chips.chips
    assert chips is not None and list(chips.buttons) == ["a", "a_2", "a_3"]
    assert [b.text() for b in chips.buttons.values()] == ["walk 10f", "roar 6f", "walk 10f"]
    assert chips.value == "a_3"
    chips.buttons["a_2"].click()
    assert ws.move == "a_2" and chips.value == "a_2" and ws.vp.clip.slot == 2  # type: ignore[union-attr]
    moves.table.picked.emit("a")
    assert chips.value == "a"
    assert kit.missing_tips(moves) == []


def test_the_strip_of_a_pair_move_is_read_only(moves: MovesPanel, ws: MonsterWorkspace) -> None:
    moves.table.picked.emit("charge")
    assert list(moves.chips.chips.buttons) == ["charge"]  # type: ignore[union-attr]
    assert not moves.add_step.isEnabled() and not moves.remove_step.isEnabled()
    assert not moves.change_clip.isEnabled()
    assert not moves.earlier.isEnabled() and not moves.split.isEnabled()


def test_add_a_step_through_the_picker(
    moves: MovesPanel, ws: MonsterWorkspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ws, "ends", lambda: poses(0.0, 90.0))
    moves.table.picked.emit("a")
    assert not moves.picker.isVisible()
    moves.add_step.click()
    t = moves.picker.table
    cells = [[t.item(r, c).text() for c in range(t.columnCount())] for r in range(t.rowCount())]
    assert cells == [
        ["walk", "", "10f", "0° good", "charge, a, a_3"],
        ["roar", "", "6f", "90° poor", "a_2"],
    ]
    assert moves.picker.title.text() == "A step after a"
    moves.picker.by.buttons["near"].click()
    assert ws.pick_by == "near" and t.item(0, 0).text() == "roar", "clip 2 is next to clip 1"
    before = ws.manifest
    moves.picker.table.picked.emit("roar")
    assert ws.sequence_of() == ["a", "a_4", "a_2", "a_3"] and ws.move == "a_4"
    assert not moves.picker.isVisible() and moves.chips.chips.value == "a_4"  # type: ignore[union-attr]
    assert ws.manifest is not before and ws.manifest.moves["a_4"].clip == "roar"  # type: ignore[union-attr]
    ws.doc.undo()  # type: ignore[union-attr]
    ws.refresh()
    assert ws.sequence_of("a") == ["a", "a_2", "a_3"], "one undo step"


def test_the_picker_cancels_and_follows_the_move(moves: MovesPanel, ws: MonsterWorkspace) -> None:
    moves.add_step.click()
    assert moves.picker.isVisible() and moves.picker.note.isVisible(), "no donor to rate by"
    moves.picker.cancel.click()
    assert not moves.picker.isVisible()
    moves.add_step.click()
    moves.table.picked.emit("a")
    assert not moves.picker.isVisible(), "another step was picked"


def test_change_the_clip_of_a_step(moves: MovesPanel, ws: MonsterWorkspace) -> None:
    moves.table.picked.emit("a_2")
    moves.change_clip.click()
    assert moves.picker.title.text() == "The clip of a_2"
    moves.picker.table.picked.emit("walk")
    assert ws.manifest.moves["a_2"].clip == "walk"  # type: ignore[union-attr]
    assert moves.chips.chips.buttons["a_2"].text() == "walk 10f"  # type: ignore[union-attr]


def test_earlier_later_split_remove(moves: MovesPanel, ws: MonsterWorkspace) -> None:
    assert not moves.later.isEnabled() and moves.earlier.isEnabled() and moves.split.isEnabled()
    moves.earlier.click()
    assert ws.sequence_of() == ["a", "a_3", "a_2"] and ws.move == "a_3"
    moves.later.click()
    assert ws.sequence_of() == ["a", "a_2", "a_3"]
    moves.table.picked.emit("a")
    assert not moves.earlier.isEnabled() and not moves.split.isEnabled()
    moves.table.picked.emit("a_2")
    moves.split.click()
    assert names(moves) == ["charge", "a", "a_2 · 2 steps", "   └ a_3"]
    moves.remove_step.click()
    assert names(moves) == ["charge", "a", "a_3"] and ws.move == "a_3"


def test_play_sequence_button(moves: MovesPanel, ws: MonsterWorkspace) -> None:
    moves.play_seq.click()
    assert ws.playing_step == "a" and moves.chips.chips.value == "a"  # type: ignore[union-attr]


def test_remove_picks_a_neighbour(moves: MovesPanel, ws: MonsterWorkspace) -> None:
    moves.table.picked.emit("a_2")
    moves.remove_step.click()
    assert ws.sequence_of("a") == ["a", "a_3"] and ws.move == "a"
    moves.table.picked.emit("a")
    moves.remove_step.click()
    assert ws.move == "a_3"
    moves.remove_step.click()
    assert ws.move is None and not moves.editor.isVisible()


def test_a_refusal_reaches_the_status(moves: MovesPanel, ws: MonsterWorkspace) -> None:
    assert ws.doc is not None
    ws.new_rule()
    ws.set_rule(play="a_3")
    moves.table.picked.emit("a_3")
    moves.remove_step.click()
    assert "still used by" in ws.message and ws.sequence_of() == ["a", "a_2", "a_3"]


# ---- the Timeline ----


@pytest.fixture
def timeline(qtbot: Any, ws: MonsterWorkspace) -> TimelinePanel:
    return show(qtbot, ws, TimelinePanel, (900, 320))


def test_the_bar_shows_the_steps(timeline: TimelinePanel, ws: MonsterWorkspace) -> None:
    bar = timeline.steps
    assert bar.isVisible() and bar.picked == "a_3"
    segs = bar.segments()
    assert [s.name for s, _, _ in segs] == ["a", "a_2", "a_3"]
    widths = [b - a for _, a, b in segs]
    assert widths[1] < widths[0] and widths[0] == pytest.approx(widths[2]), "as long as the clips"
    assert segs[2][2] == pytest.approx(bar.width())
    x = round((segs[1][1] + segs[1][2]) / 2)
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, 5))
    assert ws.move == "a_2" and bar.picked == "a_2"
    bar.grab()  # paints
    assert kit.missing_tips(timeline) == []


def test_the_bar_hides_for_one_move(timeline: TimelinePanel, ws: MonsterWorkspace) -> None:
    ws.select_move("charge")
    timeline.sync()
    assert not timeline.steps.isVisibleTo(timeline)


# ---- playing a sequence ----


def test_a_sequence_plays_step_after_step(ws: MonsterWorkspace) -> None:
    vp = ws.vp
    assert vp is not None
    ws.select_move("a_2")
    ws.play_sequence()
    assert ws.move == "a" and ws.playing_step == "a" and vp.clip.slot == 1  # type: ignore[union-attr]
    assert vp.playback.playing and not vp.playback.loop, "a looping clip plays once"
    ws.frame(0.05)
    assert ws.playing_step == "a" and not ws.take_stepped()
    for want, slot in (("a_2", 2), ("a_3", 1)):
        ws.frame(1.0)
        assert (ws.playing_step, ws.move, vp.clip.slot) == (want, want, slot)  # type: ignore[union-attr]
        assert vp.playback.playing and vp.playback.phase == 0.0 and ws.take_stepped()
    ws.frame(1.0)
    assert ws.playing_step is None and not vp.playback.playing and ws.take_stepped()
    assert ws.move == "a_3"


def test_a_loop_plays_on(ws: MonsterWorkspace) -> None:
    assert ws.set_move(after="a")
    ws.select_move("a")
    ws.play_sequence()
    seen = []
    for _ in range(5):
        ws.frame(1.0)
        seen.append(ws.move)
    assert seen == ["a_2", "a_3", "a", "a_2", "a_3"]


def test_the_length_ends_a_step(ws: MonsterWorkspace) -> None:
    ws.select_move("a")
    assert ws.set_move(length=2)  # 2 AI frames at speed 2.0: clip frame 4 of 10
    ws.play_sequence()
    ws.frame(0.1)  # 3 game frames
    assert ws.move == "a_2"


def test_picking_something_else_stops_it(ws: MonsterWorkspace) -> None:
    ws.play_sequence()
    ws.select_move("a_3")
    ws.frame(1.0)
    assert ws.playing_step is None and ws.move == "a_3"
    ws.play_sequence()
    ws.play_slot(2)
    ws.frame(1.0)
    assert ws.playing_step is None


def test_the_timeline_follows_the_sequence(timeline: TimelinePanel, ws: MonsterWorkspace) -> None:
    ws.play_sequence()
    timeline.sync()
    assert timeline.timer.isActive() and timeline.steps.picked == "a"
    ws.frame(1.0)
    timeline._tick()
    assert timeline.steps.picked == "a_2" and timeline.frame.text() == "frame 0.0 / 6"


# ---- the body carried across steps ----


@pytest.fixture
def walking(ws: MonsterWorkspace, synthetic_pac: bytes) -> MonsterWorkspace:
    """`ws` on a rig whose clips both walk the root 100 units forward by frame 10; a turns 90
    degrees (fixed steer), a_2 and a_3 do not."""
    assert ws.doc is not None
    scene = Scene.from_bytes(synthetic_pac, "t", manifest=ws.doc.manifest)
    track = Track([Channel(0x100, [Keyframe(0, 0), Keyframe(quantize("loc", 100.0), 10)])])
    for c in scene.clips:
        c.source = AnimClip([track, Track(), Track()])
    ws.load(scene, ws.doc)
    ws.select_move("a")
    assert ws.set_steer(turn="fixed", angle=90.0)
    return ws


def test_a_sequence_carries_the_body(walking: MonsterWorkspace) -> None:
    ws = walking
    vp = ws.vp
    assert vp is not None and vp.actor is not None and vp.clip is not None
    first, speed = vp.clip, vp.playback.speed
    ws.play_sequence()
    assert vp.actor.carry is None, "the first step starts where clips start"
    at = pose_at(vp.actor.scene, first, first.frames, steer=vp.actor.steer, speed=speed).joints[0]
    ws.frame(1.0)
    c = vp.actor.carry
    assert ws.move == "a_2" and c is not None and c.yaw == pytest.approx(math.pi / 2)
    start = pose_at(vp.actor.scene, vp.clip, 0.0, carry=c).joints[0]
    assert abs(at[0]) > 90.0 and np.allclose(start, at, atol=1e-6), "from where a ended, turned"
    ws.frame(1.0)
    c2 = vp.actor.carry
    assert ws.move == "a_3" and c2 is not None
    assert c2.yaw == pytest.approx(math.pi / 2), "a_2 does not turn"
    assert c2.shift[0] > c.shift[0] + 50.0, "and walked on from there"


def test_anything_else_played_starts_where_clips_start(walking: MonsterWorkspace) -> None:
    ws = walking
    vp = ws.vp
    assert vp is not None and vp.actor is not None
    ws.play_sequence()
    ws.frame(1.0)
    assert vp.actor.carry is not None
    ws.select_move("a_2")  # the same step, picked again
    assert vp.actor.carry is None
    ws.play_sequence()
    ws.frame(1.0)
    assert vp.actor.carry is not None
    ws.play_slot(2)
    assert vp.actor.carry is None
    ws.play_sequence()
    ws.frame(1.0)
    ws.play_sequence()
    assert vp.actor.carry is None, "again from the head"


# ---- the Clips table ----


@pytest.fixture
def clips(qtbot: Any, ws: MonsterWorkspace, monkeypatch: pytest.MonkeyPatch) -> ClipsPanel:
    """The Clips panel over three donor clips, 7 the one on screen: 9 fits after it best."""
    rows = [
        SourceClip(i, None, 10, False, n, "", used)
        for i, n, used in ((5, "five", ()), (7, "seven", ("a",)), (9, "nine", ("a_2", "a_3")))
    ]
    monkeypatch.setattr(ws, "source_rows", lambda: rows)
    monkeypatch.setattr(ws, "browser", lambda: object())
    monkeypatch.setattr(ws, "playing_clip", lambda: 7)
    monkeypatch.setattr(ws, "ends", lambda: poses(80.0, 30.0, 10.0, 0.0, 5.0, 90.0, 45.0, 0.0, 2.0))
    return show(qtbot, ws, ClipsPanel, (460, 800))


def ids(p: ClipsPanel) -> list[str]:
    return [p.table.item(r, 0).text() for r in range(p.table.rowCount())]


def test_the_clips_sort_by_fit(clips: ClipsPanel, ws: MonsterWorkspace) -> None:
    p = clips
    assert ids(p) == ["5", "7", "9"] and p.table.isColumnHidden(5)
    assert [p.table.item(r, 6).text() for r in range(3)] == ["", "a", "a_2, a_3"]
    p.order.buttons["fit"].click()
    assert ws.clip_order == "fit" and not p.table.isColumnHidden(5)
    assert ids(p) == ["9", "5", "7"], "clip 9 starts nearest where 7 ends"
    fits = [p.table.item(r, 5).text() for r in range(3)]
    assert fits == ["2° good", "5° good", "45° poor"]
    assert "from the end of the clip on screen" in p.table.item(0, 0).toolTip()
    p.order.buttons["id"].click()
    assert ids(p) == ["5", "7", "9"]
    assert kit.missing_tips(p) == []


def test_the_sort_says_why_it_cannot(clips: ClipsPanel, ws: MonsterWorkspace) -> None:
    ws.playing_clip = lambda: None  # type: ignore[method-assign]
    clips.order.buttons["fit"].click()
    assert ids(clips) == ["5", "7", "9"] and clips.order_note.isVisibleTo(clips)


# ---- the Zinogre's donor (MHP3RD_DATA) ----


def test_the_zinogre_backflip_sorts_to_its_next_part(
    gl: Any, games: Any, qtbot: Any, zinogre_toml: Path
) -> None:
    ws = MonsterWorkspace(games)
    ws.intel_cache[75] = None
    ws.setup(gl)
    ws.open(zinogre_toml)
    try:
        p = show(qtbot, ws, ClipsPanel, (460, 800))
        ws.play_source(218)
        p.order.buttons["fit"].click()
        assert ids(p)[0] == "219" and p.table.item(0, 5).text().endswith("fair")
    finally:
        ws.close()
