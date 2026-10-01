# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The bone overlay: joints, bones, the body fork and the leading chain, and joint picking.

Amber is the fork (`mhfu_port.records.body_fork`), green the leading chain above it (where a
location channel may land), red the selection, dim grey a joint the clip does not drive.
"""

from __future__ import annotations

from collections.abc import Iterable

import moderngl
import numpy as np
import numpy.typing as npt
from mhfu_port import records

from mhfu_studio.monster.core.scene import Scene
from mhfu_studio.monster.render.playback import leading_chain
from mhfu_studio.shell.camera import Mat, OrbitCamera
from mhfu_studio.shell.lines import Lines

C_BONE_ALPHA = 0.90
C_JOINT = (0.62, 0.72, 0.86, 1.00)
C_FORK = (0.98, 0.70, 0.20, 1.00)
C_LEAD = (0.36, 0.82, 0.42, 1.00)
C_SELECTED = (0.95, 0.20, 0.22, 1.00)
C_DRIVEN = (0.55, 0.62, 0.78, 1.00)
C_UNDRIVEN = (0.40, 0.40, 0.44, 0.75)
#: past this fraction outside the image a projected joint is off screen
MARGIN = 0.1


def undriven_geometry(scene: Scene) -> dict[int, int]:
    """`{joint: vertices}` for joints that carry geometry no clip drives: it stays at bind
    (the native Tigrex's 150 vertices on 46/47, a second root chain no stream covers)."""
    if not scene.clips:
        return {}
    ever = {j for c in scene.clips for j in c.driven}
    return {j: n for j, n in sorted(vertex_counts(scene).items()) if j not in ever}


def vertex_counts(scene: Scene) -> dict[int, int]:
    """Vertices each joint dominates."""
    js, ns = np.unique(scene.merged.dominant(), return_counts=True)
    return {int(j): int(n) for j, n in zip(js, ns, strict=True) if j >= 0}


def project(
    camera: OrbitCamera, points: npt.ArrayLike, size: tuple[int, int]
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.bool_]]:
    """`(n, 2)` pixels (row 0 at the top) and whether each is in front and near the image."""
    p = camera.project(points, size)
    w, h = size
    ok = (p[:, 2] < 2.0) & (p[:, 0] >= -MARGIN * w) & (p[:, 0] <= (1 + MARGIN) * w)
    ok &= (p[:, 1] >= -MARGIN * h) & (p[:, 1] <= (1 + MARGIN) * h)
    return p[:, :2], ok


def nearest(
    camera: OrbitCamera,
    points: npt.ArrayLike,
    size: tuple[int, int],
    x: float,
    y: float,
    radius: float,
) -> int | None:
    """The index of the point nearest pixel `(x, y)`, or None past `radius`; points behind the
    camera never win."""
    pts = np.asarray(points, np.float64).reshape(-1, 3)
    if not len(pts):
        return None
    xy, ok = project(camera, pts, size)
    d = np.hypot(xy[:, 0] - x, xy[:, 1] - y)
    d[~ok] = np.inf
    i = int(np.argmin(d))
    return i if d[i] <= radius else None


class SkeletonOverlay:
    """Joints as points, bones as segments; a bone takes its child's colour."""

    def __init__(self, ctx: moderngl.Context, scene: Scene) -> None:
        self.parents = np.asarray(scene.rig.parents, np.int32)
        self.fork = records.body_fork(self.parents.tolist())
        self.lead = leading_chain(self.parents)
        self.selected: int | None = None
        #: joints the playing clip drives; the rest draw dimmed. None: all alike
        self.driven: tuple[int, ...] | None = None
        self.joint_size = 6.0
        self._bones = Lines(ctx)
        self._joints = Lines(ctx, ctx.POINTS)
        n = len(self.parents)
        self._pairs = np.array(
            [(int(p), i) for i, p in enumerate(self.parents) if 0 <= p < n], np.int32
        ).reshape(-1, 2)
        self._positions = scene.rig.bind_joints.copy()
        self._rebuild()

    @property
    def positions(self) -> npt.NDArray[np.float64]:
        return self._positions

    def set_positions(self, joints: npt.ArrayLike) -> None:
        self._positions = np.asarray(joints, np.float64).reshape(-1, 3)
        self._rebuild()

    def set_selected(self, joint: int | None) -> None:
        if joint != self.selected:
            self.selected = joint
            self._rebuild()

    def set_driven(self, driven: Iterable[int] | None) -> None:
        d = None if driven is None else tuple(sorted(driven))
        if d != self.driven:
            self.driven = d
            self._rebuild()

    def joint_colors(self) -> npt.NDArray[np.float32]:
        n = len(self._positions)
        col = np.tile(np.array(C_JOINT, "f4"), (n, 1))
        if self.driven is not None:
            col[:] = C_UNDRIVEN
            idx = [j for j in self.driven if 0 <= j < n]
            col[idx] = C_DRIVEN
        col[[j for j in self.lead if 0 <= j < n]] = C_LEAD
        if 0 <= self.fork < n:
            col[self.fork] = C_FORK
        if self.selected is not None and 0 <= self.selected < n:
            col[self.selected] = C_SELECTED
        return col

    def _rebuild(self) -> None:
        jc = self.joint_colors()
        if len(self._pairs):
            seg = self._positions[self._pairs.reshape(-1)]
            cols = np.repeat(jc[self._pairs[:, 1]], 2, axis=0) * 0.85
            cols[:, 3] = C_BONE_ALPHA
            self._bones.set(seg, cols)
        self._joints.set(self._positions, jc)

    def render(self, mvp: Mat, *, alpha: float = 1.0) -> None:
        self._bones.alpha = self._joints.alpha = alpha
        self._joints.point_size = self.joint_size
        self._bones.render(mvp)
        self._joints.render(mvp)

    def pick(
        self, camera: OrbitCamera, size: tuple[int, int], x: float, y: float, radius: float = 14.0
    ) -> int | None:
        """The joint nearest pixel `(x, y)`, row 0 at the top."""
        return nearest(camera, self._positions, size, x, y, radius)

    def release(self) -> None:
        self._bones.release()
        self._joints.release()

    def __repr__(self) -> str:
        return (
            f"<SkeletonOverlay {len(self._positions)} joints, fork={self.fork}, "
            f"lead={list(self.lead)}, selected={self.selected}>"
        )
