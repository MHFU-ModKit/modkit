# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path

import numpy as np
import pytest
from mhfu_studio.monster.core.scene import Scene
from mhfu_studio.monster.render.playback import (
    GAME_HZ,
    Playback,
    leading_chain,
    pose_at,
    root_joints,
    root_travel,
    travel_joints,
    wall_clock,
)


def test_gate_before_the_step() -> None:
    pb = Playback(end=10.0, speed=3.0)
    seen = []
    for _ in range(5):
        pb.step(1)
        seen.append(pb.phase)
    assert seen == [3.0, 6.0, 9.0, 10.0, 10.0]


def test_one_shot_stops() -> None:
    pb = Playback(end=8.0, speed=2.0)
    pb.play()
    pb.advance(10.0)
    assert pb.phase == 8.0 and not pb.playing


def test_loop_keeps_the_overshoot() -> None:
    pb = Playback(end=10.0, speed=3.0, loop=True)
    pb.play()
    for _ in range(4):
        pb._one(1.0)
    assert abs(pb.phase - 2.0) < 1e-6 and pb.playing
    pb.step(-1)
    pb.step(-1)
    assert abs(pb.phase - 6.0) < 1e-6


def test_wall_clock() -> None:
    assert wall_clock(154, 2.0) == pytest.approx(154 / 2.0 / 30.0)
    assert wall_clock(154, 2.0) > wall_clock(154, 2.4)
    assert wall_clock(10, 0) == float("inf")
    assert Playback(end=60.0, speed=2.0).duration == pytest.approx(1.0)


@pytest.mark.parametrize("fps", [30.0, 60.0, 144.0, 240.0])
def test_thirty_hertz_at_any_fps(fps: float) -> None:
    pb = Playback(end=1e9, speed=1.0)
    pb.play()
    for _ in range(int(fps)):
        pb.advance(1.0 / fps)
    assert pb.phase == pytest.approx(GAME_HZ)


def test_a_stall_does_not_teleport() -> None:
    pb = Playback(end=1e9, speed=2.0)
    pb.play()
    pb.advance(30.0)
    assert pb.phase <= 0.25 * GAME_HZ * 2.0


def test_seek_and_step_clamp() -> None:
    pb = Playback(end=20.0, speed=2.0)
    pb.seek(999.0)
    assert pb.phase == 20.0
    pb.seek(-5.0)
    pb.step(-1)
    assert pb.phase == 0.0
    pb.playing = True
    pb.step(1)
    assert pb.phase == 2.0 and not pb.playing
    pb.set_clip(None)
    assert pb.end == 0.0 and not pb.loop


def test_leading_chain() -> None:
    assert leading_chain([-1, 0, 1, 2, 3, 2, 5]) == (0, 1, 2)
    assert leading_chain([]) == ()


def test_travel_is_on_the_hip_not_the_root(rig: Scene) -> None:
    assert root_joints(rig) == (0, 7)
    assert travel_joints(rig) == (0, 1, 2)
    walk = rig.clip(1)
    a, b = rig.curves(walk).at(0.0)[1], rig.curves(walk).at(20.0)[1]
    moved = np.abs(a - b).max(axis=1)
    assert moved[0] == 0 and moved[1] == pytest.approx(120.0)


def test_strip_keeps_frame_zero_and_drops_the_drift(rig: Scene) -> None:
    walk = rig.clip(1)
    assert np.allclose(
        pose_at(rig, walk, 0.0).joints, pose_at(rig, walk, 0.0, strip_root=True).joints
    )

    def drift(strip: bool) -> float:
        c = np.array([pose_at(rig, walk, f, strip_root=strip).joints[:7].mean(0) for f in (0, 40)])
        return float(np.linalg.norm(c[1] - c[0]))

    assert drift(False) > 200.0 and drift(True) < 1e-6
    assert pose_at(rig, None, 5.0).frame == 0.0


def test_root_travel(rig: Scene) -> None:
    net, peak = root_travel(rig, rig.clip(1))
    assert net == pytest.approx(240.0) and peak == pytest.approx(240.0)
    assert root_travel(rig, rig.clip(2)) == (0.0, 0.0)


def test_tigrex_travel(mhfu_data: Path) -> None:
    sc = Scene.from_path(mhfu_data / "file_06185.bin")
    assert root_joints(sc) == (0, 45) and travel_joints(sc) == (0, 1, 2)
    travels = {c.slot: root_travel(sc, c)[1] for c in sc.clips}
    assert any(t > 100 for t in travels.values()) and any(t < 1 for t in travels.values())
    best = max(sc.clips, key=lambda c: travels[c.slot])
    frames = np.linspace(0.0, best.frames, 20)

    def drift(strip: bool) -> float:
        c = np.array([pose_at(sc, best, f, strip_root=strip).joints.mean(0) for f in frames])
        return float(np.linalg.norm(c - c[0], axis=1).max())

    assert drift(True) < drift(False) * 0.25
