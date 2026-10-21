# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A move in miniature: its clips one after another, each played once. The camera follows the
body at a distance that fits its every pose, so a charge stays in the picture and the ground shows
the travel. It draws on the 3D view's context, as `shots` does."""

from __future__ import annotations

import math
from collections.abc import Sequence
from itertools import product

import moderngl
import numpy as np
import numpy.typing as npt
from mhfu_port.model import Clip

from mhfu_studio.harness.render import Image8
from mhfu_studio.monster.core.scene import Scene
from mhfu_studio.shell.camera import Bounds
from mhfu_studio.shell.target import RGBA

from .playback import travel_joints
from .viewport import MonsterViewport

SIZE = (320, 220)
VIEW = "three"
FILL = 0.86
"""The share of the picture the body's poses fill, across or down."""
SAMPLES = 9
"""Poses a clip is framed on, its first and last among them."""


class MovePreview:
    """`show` the clips, then `advance` and `picture` once a frame."""

    def __init__(self, ctx: moderngl.Context, size: tuple[int, int] = SIZE) -> None:
        self.vp = MonsterViewport(ctx, size)
        self.vp.show_skeleton = False
        self.clips: tuple[Clip, ...] = ()
        #: the clip playing, an index of `clips`
        self.at = 0
        #: the camera's aim, from the body's place on the ground
        self._aim = np.zeros(3)

    def show(self, scene: Scene, clips: Sequence[Clip]) -> None:
        """Plays `clips` in turn from the first; the scene's model is made once per scene."""
        if self.vp.scene is not scene:
            self.vp.set_scene(scene, frame_camera=False)
        self.clips = tuple(c for c in clips if c.frames > 0)  # an empty one never ends
        if self.clips:
            self._start(0)

    def advance(self, dt: float) -> None:
        """`dt` real seconds; at a clip's end the next starts."""
        if not self.clips:
            return
        self.vp.tick(dt)
        if not self.vp.playback.playing:
            self._start((self.at + 1) % len(self.clips))
        self._follow()

    def picture(self, background: RGBA | None = None) -> Image8:
        """The frame now, `(h, w, 4)`, row 0 at the top."""
        if background is not None:
            self.vp.background = background
        self.vp.draw()
        return self.vp.target.read()

    def resize(self, size: tuple[int, int]) -> None:
        if self.vp.resize(size) and self.clips:
            self._start(self.at)

    def release(self) -> None:
        self.vp.release()

    def _start(self, k: int) -> None:
        self.at = k
        clip = self.clips[k]
        self._aim = fit(self.vp, clip)
        self.vp.play_clip(clip)
        self.vp.playback.loop = False  # once, then the next
        self.vp.playback.play()
        self._follow()

    def _follow(self) -> None:
        self.vp.camera.target = self._aim + ground(self.vp)


def ground(vp: MonsterViewport) -> npt.NDArray[np.float64]:
    """Where the body is on the ground now: its fork joint's x and z."""
    sc, sk = vp.scene, vp.skeleton
    if sc is None or sk is None:
        return np.zeros(3)
    x, _, z = sk.positions[travel_joints(sc)[-1]]
    return np.array([x, 0.0, z])


def fit(vp: MonsterViewport, clip: Clip, fill: float = FILL) -> npt.NDArray[np.float64]:
    """Sets `vp`'s camera so each of `clip`'s poses, taken where the body stands, fits `fill`
    of the picture; returns the aim from the body's place (`ground`). Each pose's own box, not
    one around them all, which a turn would make twice the animal."""
    boxes = []
    for k in range(SAMPLES):
        vp.set_pose(clip, clip.frames * k / (SAMPLES - 1))
        b, at = vp.bounds(), ground(vp)
        boxes.append(Bounds(b.lo - at, b.hi - at))
    cam = vp.camera
    cam.frame(Bounds.union(*boxes)).look(VIEW)
    corners = np.array([c for b in boxes for c in product(*zip(b.lo, b.hi, strict=True))])
    size = np.asarray(vp.target.size, dtype=np.float64)
    for _ in range(3):  # the picture shrinks about as the distance grows
        px = cam.project(corners, vp.target.size)[:, :2]
        lo, hi = px.min(axis=0), px.max(axis=0)
        off = (lo + hi - size) / 2.0  # pixels off centre, y down
        per_px = 2.0 * cam.distance * math.tan(math.radians(cam.fov) / 2.0) / size[1]
        cam.target = cam.target + (off[0] * cam.view[0, :3] - off[1] * cam.view[1, :3]) * per_px
        cam.distance *= max(float(((hi - lo) / size).max()) / fill, 1e-3)
    return np.asarray(cam.target, dtype=np.float64)
