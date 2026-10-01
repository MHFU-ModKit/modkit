# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A synthetic scene drawn with shell pieces only: the harness's own golden, no game data.

Opaque solids at two depths, a translucent ground, lines and points, so a render exercises
depth, blending, MSAA, point size, the camera and the read-back.
"""

from __future__ import annotations

import moderngl
import numpy as np

from mhfu_studio.harness.render import Shots, offscreen, views
from mhfu_studio.shell.camera import Bounds, Mat
from mhfu_studio.shell.lines import (
    Geometry,
    Lines,
    axes_geometry,
    bounds_geometry,
    circle_geometry,
    concat,
    tinted,
)
from mhfu_studio.shell.viewport import Viewport, depth_write_off

VIEWS = ("three", "front", "side", "top")
BOUNDS = Bounds(np.array([-1.5, 0.0, -1.5]), np.array([1.5, 1.6, 1.5]))
#: face order: -x +x -y +y -z +z
FACE_COLORS = (
    (0.85, 0.30, 0.28),
    (0.95, 0.62, 0.25),
    (0.40, 0.40, 0.45),
    (0.92, 0.88, 0.40),
    (0.30, 0.55, 0.90),
    (0.35, 0.80, 0.50),
)


def box(lo: tuple[float, float, float], hi: tuple[float, float, float]) -> Geometry:
    """A solid box: 12 triangles, one colour per face."""
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    quads = [
        ((x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)),
        ((x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)),
        ((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)),
        ((x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)),
        ((x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0)),
        ((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)),
    ]
    pos = [q[i] for q in quads for i in (0, 1, 2, 0, 2, 3)]
    col = [(*FACE_COLORS[f], 1.0) for f in range(6) for _ in range(6)]
    return np.array(pos, dtype="f4"), np.array(col, dtype="f4")


def geometry() -> dict[str, Geometry]:
    """The scene's batches: `solid` and `glass` are triangles, `wire` lines, `dots` points."""
    solid = concat(box((-0.5, 0.0, -0.5), (0.5, 1.0, 0.5)), box((0.7, 0.0, -1.1), (1.1, 1.4, -0.7)))
    e = float(BOUNDS.hi[0])
    quad = [(-e, 0, -e), (e, 0, -e), (e, 0, e), (-e, 0, -e), (e, 0, e), (-e, 0, e)]
    ticks = np.linspace(-e, e, 13)
    grid = [((t, 0.002, -e), (t, 0.002, e)) for t in ticks]
    grid += [((-e, 0.002, t), (e, 0.002, t)) for t in ticks]
    wire = concat(
        tinted(grid, (0.7, 0.72, 0.8, 0.6)),
        axes_geometry(1.2, (0.0, 0.003, 0.0)),
        bounds_geometry(BOUNDS),
        tinted(circle_geometry((0.0, 0.5, 0.0), 1.25, 48), (0.95, 0.45, 0.85, 1.0)),
    )
    g = np.linspace(-1.0, 1.0, 5)
    return {
        "solid": solid,
        "glass": tinted(quad, (0.55, 0.65, 0.85, 0.35)),
        "wire": wire,
        "dots": tinted([(x, 1.5, z) for x in g for z in g], (1.0, 1.0, 1.0, 1.0)),
    }


class Selftest(Viewport):
    """The synthetic scene, framed on `BOUNDS`."""

    def __init__(self, ctx: moderngl.Context, size: tuple[int, int] = (480, 320), samples: int = 4):
        super().__init__(ctx, size, samples)
        geo = geometry()
        self.solid = Lines(ctx, ctx.TRIANGLES)
        self.glass = Lines(ctx, ctx.TRIANGLES)
        self.wire = Lines(ctx)
        self.dots = Lines(ctx, ctx.POINTS)
        self.dots.point_size = 5.0
        for name in ("solid", "glass", "wire", "dots"):
            getattr(self, name).set(*geo[name])
        self.frame_all()

    def bounds(self) -> Bounds:
        return BOUNDS

    def draw_scene(self, mvp: Mat) -> None:
        self.solid.render(mvp)
        self.wire.render(mvp)
        self.dots.render(mvp)
        with depth_write_off(self.ctx):  # translucent last
            self.glass.render(mvp)

    def release(self) -> None:
        for b in (self.solid, self.glass, self.wire, self.dots):
            b.release()
        super().release()


def render(
    ctx: moderngl.Context | None = None, size: tuple[int, int] = (480, 320), samples: int = 4
) -> Shots:
    """Every view in `VIEWS`."""
    with offscreen(lambda c: Selftest(c, size, samples), ctx) as vp:
        return views(vp, VIEWS)
