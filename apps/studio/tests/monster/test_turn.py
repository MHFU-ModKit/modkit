# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The turn gizmo, driven with synthetic pointers: over the carried synthetic port, and over
the Zinogre when the games are there."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from mhfu_port import manifest
from mhfu_port.data import Data
from mhfu_port.manifest import Steer
from mhfu_studio.monster.turn import heading
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell.input import Button, Key, Mod, Pointer
from mhfu_studio.shell.overlay import Recorder
from mhfu_studio.shell.workspace import Gesture

VIEW = (320, 240)
Pt = tuple[float, float]


def ev(kind: str, at: Pt, mods: Mod = Mod.NONE) -> Pointer:
    held = Button.LEFT if kind in ("press", "move") else Button.NONE
    changed = Button.NONE if kind == "move" else Button.LEFT
    return Pointer(kind, at[0], at[1], VIEW, button=changed, buttons=held, mods=mods)  # type: ignore[arg-type]


def ring(ws: MonsterWorkspace, deg: float) -> Pt:
    """The point of the ring `deg` degrees round, on screen."""
    assert ws.vp is not None
    p = heading(deg) * ws.turn._radius()
    x, y, _ = ws.vp.camera.project(p, VIEW)[0]
    return float(x), float(y)


def drag_to(ws: MonsterWorkspace, deg: float, mods: Mod = Mod.NONE) -> list[Gesture]:
    a = ws.turn.handle(VIEW)
    assert a is not None
    got = [ws.pointer(ev("press", a, mods))]
    got.append(ws.pointer(ev("move", ring(ws, deg / 2), mods)))
    got.append(ws.pointer(ev("move", ring(ws, deg), mods)))
    got.append(ws.pointer(ev("release", ring(ws, deg), mods)))
    return got


def facing(ws: MonsterWorkspace) -> float:
    """Degrees the drawn skeleton is turned about y from the clip's own pose, at the playhead."""
    vp = ws.vp
    assert vp is not None and vp.actor is not None and vp.clip is not None
    sc = vp.actor.scene
    drawn = vp.actor.skeleton.positions[:, [0, 2]]
    own = sc.rig.world(*sc.curves(vp.clip).at(vp.frame))[:, [0, 2], 3]
    a, b = own - own.mean(0), drawn - drawn.mean(0)
    cross = float(np.sum(a[:, 1] * b[:, 0] - a[:, 0] * b[:, 1]))
    return math.degrees(math.atan2(cross, float(np.sum(a * b))))


def test_drag_to_90_sets_the_clip_turn(carried: MonsterWorkspace) -> None:
    ws = carried
    ws.play_slot(1)
    t = ws.turn.target()
    assert t is not None and t.end == 0.0 and t.writes == "clip"
    assert drag_to(ws, 90.0) == [Gesture.ALL] * 4
    assert ws.manifest is not None and ws.manifest.clips["walk"].turn == 90.0
    assert ws.vp is not None and ws.vp.clip is not None
    ws.seek(ws.vp.clip.frames)
    assert facing(ws) == pytest.approx(90.0, abs=1.0)
    ws.seek(0.0)
    assert facing(ws) == pytest.approx(0.0, abs=1.0)
    assert ws.doc is not None
    ws.doc.undo()
    ws.refresh()
    assert ws.manifest.clips["walk"].turn is None and not ws.doc.can_undo(), "one step"


def test_the_handle_shows_its_number(carried: MonsterWorkspace) -> None:
    ws = carried
    ws.play_slot(1)
    a = ws.turn.handle(VIEW)
    assert a is not None
    ws.pointer(ev("press", a))
    ws.pointer(ev("move", ring(ws, -45.0)))
    rec = Recorder(VIEW)
    ws.paint(rec)
    assert "-45°" in rec.texts(), "the number follows the drag"
    assert ws.manifest is not None and ws.manifest.clips["walk"].turn is None, "nothing yet"
    assert ws.key(Key("Escape")) and ws.turn.dragged is None
    ws.pointer(ev("release", ring(ws, -45.0)))
    assert ws.manifest.clips["walk"].turn is None


def test_shift_snaps_and_past_half_a_turn(carried: MonsterWorkspace) -> None:
    ws = carried
    ws.play_slot(1)
    a = ws.turn.handle(VIEW)
    assert a is not None
    ws.pointer(ev("press", a, Mod.SHIFT))
    for deg in (60.0, 120.0, 170.0, 230.0):
        ws.pointer(ev("move", ring(ws, deg), Mod.SHIFT))
    ws.pointer(ev("release", ring(ws, 233.0), Mod.SHIFT))
    assert ws.manifest is not None and ws.manifest.clips["walk"].turn == 225.0


def test_an_unnamed_clip_is_named(carried: MonsterWorkspace) -> None:
    ws = carried
    ws.play_slot(2)
    drag_to(ws, -30.0)
    m = ws.manifest
    assert m is not None and m.clips["clip_02"].turn == -30.0


def test_a_fixed_steer_is_dragged(carried: MonsterWorkspace) -> None:
    ws = carried
    ws.play_slot(1)
    ws.new_move("stamp")
    ws.set_steer(turn="fixed")
    t = ws.turn.target()
    assert t is not None and t.writes == "steer" and t.end == 0.0
    drag_to(ws, 90.0)
    mv = ws.own_move()
    assert mv is not None and mv.steer == Steer("fixed", angle=90.0, frames=5)
    assert ws.manifest is not None and ws.manifest.clips["walk"].turn is None
    assert ws.vp is not None
    ws.seek(10.0)
    assert facing(ws) == pytest.approx(90.0, abs=1.0)


@pytest.mark.parametrize(
    ("turn", "end", "text"),
    [("still", 0.0, "still"), ("hunter", None, "toward the hunter"), ("away", None, "away")],
)
def test_other_steers_show_and_refuse(
    carried: MonsterWorkspace, turn: str, end: float | None, text: str
) -> None:
    ws = carried
    ws.play_slot(1)
    ws.new_move("stamp")
    ws.set_steer(turn=turn)
    t = ws.turn.target()
    assert t is not None and t.end == end and t.writes is None and text in t.text
    rec = Recorder(VIEW)
    ws.paint(rec)
    assert any(text in s for s in rec.texts())
    if end is not None:
        drag_to(ws, 40.0)
        assert "nothing to drag" in ws.message


def test_t_hides_it(carried: MonsterWorkspace) -> None:
    ws = carried
    ws.play_slot(1)
    assert ws.turn.target() is not None and "set the turn" in ws.hint()
    assert ws.key(Key("T")) and ws.turn.target() is None
    rec = Recorder(VIEW)
    ws.paint(rec)
    assert not rec.calls
    assert ws.key(Key("T")) and ws.turn.target() is not None


def test_zinogre_turn_left_by_90(gl: object, games: Data, zinogre_toml: Path) -> None:
    """The owner's case: turn_left carries no turn in its data; the gizmo gives it one."""
    ws = MonsterWorkspace(games)
    ws.intel_cache[75] = None
    ws.setup(gl)  # type: ignore[arg-type]
    try:
        ws.open(zinogre_toml)
        ws.play_source(9)
        ws.name_buf, ws.label_buf = "turn_left", "turning left in standing position"
        ws.label()
        assert ws.vp is not None and ws.vp.clip is not None
        ws.vp.camera.frame(ws.vp.bounds()).look("three")
        drag_to(ws, 90.0)
        assert ws.manifest is not None and ws.manifest.clips["turn_left"].turn == 90.0
        ws.seek(ws.vp.clip.frames)
        assert facing(ws) == pytest.approx(90.0, abs=1.0)
        assert ws.doc is not None
        assert manifest.load(ws.doc.save()).clips["turn_left"].turn == 90.0
    finally:
        ws.close()
