# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A move in miniature: its clips once each in turn, the body inside the picture, the camera
following it."""

from collections.abc import Callable
from typing import Any

import numpy as np
from mhfu_studio.monster.core.scene import Scene
from mhfu_studio.monster.render.preview import MovePreview

Lit = Callable[[Any, Any], int]
STEP = 1 / 30


def test_each_clip_once_in_turn(gl: Any, rig: Scene) -> None:
    pv = MovePreview(gl, (80, 60))
    try:
        pv.show(rig, [rig.clip(1), rig.clip(2)])
        seen = [pv.at]
        for _ in range(120):
            pv.advance(STEP)
            if pv.at != seen[-1]:
                seen.append(pv.at)
        assert seen[:4] == [0, 1, 0, 1], "the looping walk plays once too"
    finally:
        pv.release()


def test_the_body_fills_the_picture_and_stays_in_it(gl: Any, rig: Scene) -> None:
    pv = MovePreview(gl, (160, 110))
    try:
        pv.vp.show_ground = False
        pv.show(rig, [rig.clip(1), rig.clip(2)])
        bg = np.round(np.asarray(pv.vp.background[:3]) * 255)
        for _ in range(60):
            pv.advance(STEP)
            on = (np.abs(pv.picture()[..., :3].astype(int) - bg).max(axis=2) > 12).nonzero()
            ys, xs = on
            assert ys.min() > 0 and xs.min() > 0 and ys.max() < 109 and xs.max() < 159
            assert max((xs.max() - xs.min()) / 160, (ys.max() - ys.min()) / 110) > 0.5
    finally:
        pv.release()


def test_the_camera_follows_the_walk(gl: Any, rig: Scene, lit: Lit) -> None:
    pv = MovePreview(gl, (160, 110))
    try:
        pv.show(rig, [rig.clip(1)])
        start, first = pv.vp.camera.target.copy(), pv.picture()
        for _ in range(8):
            pv.advance(STEP)
        moved = pv.vp.camera.target - start
        assert moved[2] > 50 and abs(moved[1]) < 1e-9, "along +Z, at one height"
        assert lit(first, pv.vp.background) > 0 and not np.array_equal(first, pv.picture())
    finally:
        pv.release()
