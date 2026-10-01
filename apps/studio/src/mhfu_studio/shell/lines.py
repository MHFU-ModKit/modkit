# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Flat-coloured line and point batches, and the generic wire shapes that feed them."""

from __future__ import annotations

import math
from collections.abc import Sequence

import moderngl
import numpy as np
import numpy.typing as npt

from mhfu_studio.shell.camera import Bounds, Mat, gl_bytes
from mhfu_studio.shell.shaders import line_program, uniform

F32 = npt.NDArray[np.float32]
Geometry = tuple[F32, F32]
"""`(n, 3)` positions and `(n, 4)` RGBA colours."""

#: engine axis -> colour: X, Y up, Z
AXIS_COLORS = ((0.85, 0.28, 0.32), (0.42, 0.78, 0.36), (0.32, 0.52, 0.90))
BOX_COLOR = (0.55, 0.57, 0.62, 0.55)


class Lines:
    """A `GL_LINES` (or `GL_POINTS`) batch; `alpha` and `point_size` apply to the whole batch."""

    def __init__(self, ctx: moderngl.Context, mode: int | None = None) -> None:
        self.ctx = ctx
        self.prog = line_program(ctx)
        self.mode = ctx.LINES if mode is None else mode
        self.count = 0
        self.alpha = 1.0
        self.point_size = 1.0
        self._vbo: moderngl.Buffer | None = None
        self._vao: moderngl.VertexArray | None = None

    def set(self, positions: npt.ArrayLike, colors: npt.ArrayLike) -> None:
        """Uploads; `colors` is `(n, 4)` or one RGBA for all."""
        pos = np.ascontiguousarray(positions, dtype="f4").reshape(-1, 3)
        col = np.asarray(colors, dtype="f4")
        if col.ndim == 1:
            col = np.tile(col, (len(pos), 1))
        col = col.reshape(-1, 4)
        if len(col) != len(pos):
            raise ValueError(f"{len(pos)} positions but {len(col)} colours")
        data = np.concatenate([pos, col], axis=1).astype("f4")
        if self._vbo is None or self._vbo.size < data.nbytes:
            self.release()
            self._vbo = self.ctx.buffer(reserve=max(data.nbytes, 1), dynamic=True)
            self._vao = self.ctx.vertex_array(
                self.prog, [(self._vbo, "3f 4f", "in_pos", "in_color")]
            )
        if data.nbytes:
            self._vbo.write(data.tobytes())
        self.count = len(pos)

    def clear(self) -> None:
        self.count = 0

    def render(self, mvp: Mat) -> None:
        if not self.count or self._vao is None:
            return
        uniform(self.prog, "u_mvp").write(gl_bytes(mvp))
        uniform(self.prog, "u_alpha").value = float(self.alpha)
        uniform(self.prog, "u_point_size").value = float(self.point_size)
        self._vao.render(mode=self.mode, vertices=self.count)

    def release(self) -> None:
        for o in (self._vao, self._vbo):
            if o is not None:
                o.release()
        self._vao = self._vbo = None


def tinted(pos: npt.ArrayLike, rgba: Sequence[float]) -> Geometry:
    """Positions with one colour for all."""
    p = np.asarray(pos, dtype="f4").reshape(-1, 3)
    return p, np.tile(np.asarray(rgba, dtype="f4"), (len(p), 1))


def axes_geometry(length: float, origin: Sequence[float] = (0.0, 0.0, 0.0)) -> Geometry:
    """The three world axes from `origin`."""
    o = np.asarray(origin, dtype=np.float64)
    pos, col = [], []
    for a in range(3):
        d = np.zeros(3)
        d[a] = length
        pos += [o, o + d]
        col += [(*AXIS_COLORS[a], 1.0)] * 2
    return np.array(pos, dtype="f4"), np.array(col, dtype="f4")


def bounds_geometry(b: Bounds, rgba: Sequence[float] = BOX_COLOR) -> Geometry:
    """The twelve edges of a box."""
    (x0, y0, z0), (x1, y1, z1) = b.lo, b.hi
    c = np.array([[x, y, z] for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)])
    # corner index = 4*ix + 2*iy + iz
    edges = [(0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7)]
    edges += [(0, 4), (1, 5), (2, 6), (3, 7)]
    return tinted([c[i] for e in edges for i in e], rgba)


def circle_geometry(center: Sequence[float], radius: float, n: int = 32, axis: str = "y") -> F32:
    """A closed ring of `n` segments around `axis`, as `(2n, 3)` line endpoints."""
    c = np.asarray(center, dtype=np.float64)
    t = np.linspace(0, 2 * math.pi, n, endpoint=False)
    a, b, flat = radius * np.cos(t), radius * np.sin(t), np.zeros(n)
    x, y, z = {"y": (a, flat, b), "x": (flat, a, b), "z": (a, b, flat)}[axis]
    ring = np.stack([c[0] + x, c[1] + y, c[2] + z], 1)
    return np.stack([ring, np.roll(ring, -1, axis=0)], 1).reshape(-1, 3).astype("f4")


def concat(*parts: Geometry | None) -> Geometry:
    """Several geometries as one batch."""
    real = [p for p in parts if p is not None and len(p[0])]
    if not real:
        return np.zeros((0, 3), "f4"), np.zeros((0, 4), "f4")
    return (
        np.concatenate([p[0] for p in real]).astype("f4"),
        np.concatenate([p[1] for p in real]).astype("f4"),
    )
