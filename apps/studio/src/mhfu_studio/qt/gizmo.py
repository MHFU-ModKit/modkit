# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A translate gizmo drawn with QPainter: three axis arrows, dragged along their axis.

Qt has no ImGuizmo; this is the bespoke part a Qt shell would own. Rotate and scale would be
rings and boxes on the same math.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from mhfu_studio.shell.camera import OrbitCamera

Vec = npt.NDArray[np.float64]
AXES = np.eye(3)
COLORS = ((230, 80, 80), (110, 210, 90), (80, 140, 240))
LENGTH_PX = 90.0
GRAB_PX = 8.0


@dataclass(frozen=True)
class Handle:
    axis: int
    a: tuple[float, float]
    b: tuple[float, float]


def world_per_px(cam: OrbitCamera, at: Vec, height: int) -> float:
    depth = float(np.dot(at - cam.eye, cam.forward))
    return 2.0 * max(depth, 1e-6) * math.tan(math.radians(cam.fov) * 0.5) / max(height, 1)


def handles(cam: OrbitCamera, pivot: Vec, size: tuple[int, int]) -> list[Handle]:
    """The arrows in widget points; an axis pointing at the camera is left out."""
    length = LENGTH_PX * world_per_px(cam, pivot, size[1])
    pts = cam.project(np.vstack([pivot, pivot + AXES * length]), size)
    if pts[0, 2] > 1.0:
        return []
    a = (float(pts[0, 0]), float(pts[0, 1]))
    out = []
    for i in range(3):
        b = (float(pts[i + 1, 0]), float(pts[i + 1, 1]))
        if math.dist(a, b) > 12.0 and pts[i + 1, 2] <= 1.0:
            out.append(Handle(i, a, b))
    return out


def hit(hs: list[Handle], x: float, y: float) -> int | None:
    """The axis whose arrow is within GRAB_PX of the point."""
    best, axis = GRAB_PX, None
    for h in hs:
        d = _segment_distance((x, y), h.a, h.b)
        if d <= best:
            best, axis = d, h.axis
    return axis


def along(pivot: Vec, axis: Vec, origin: Vec, direction: Vec) -> float:
    """Where the mouse ray passes closest to the axis line, as a distance along it."""
    w0 = pivot - origin
    b = float(np.dot(axis, direction))
    denom = 1.0 - b * b
    if denom < 1e-9:
        return 0.0
    return (b * float(np.dot(direction, w0)) - float(np.dot(axis, w0))) / denom


def _segment_distance(
    p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> float:
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    n = dx * dx + dy * dy
    t = 0.0 if n == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / n))
    return math.dist(p, (ax + t * dx, ay + t * dy))


class Drag:
    """One drag of one axis: `offset` is the world translation so far."""

    def __init__(self, pivot: Vec, axis: int, ray: tuple[Vec, Vec]) -> None:
        self.pivot, self.axis = pivot.copy(), axis
        self.start = along(self.pivot, AXES[axis], *ray)

    def offset(self, ray: tuple[Vec, Vec], snap: float | None = None) -> Vec:
        t = along(self.pivot, AXES[self.axis], *ray) - self.start
        if snap:
            t = round(t / snap) * snap
        out: Vec = AXES[self.axis] * t
        return out
