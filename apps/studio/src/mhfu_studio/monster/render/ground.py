# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""What is in the viewport besides the monster: the ground plane and the bind-pose points."""

from __future__ import annotations

from collections.abc import Sequence

import moderngl
import numpy as np
import numpy.typing as npt

from mhfu_studio.shell.camera import Bounds, Mat, gl_bytes
from mhfu_studio.shell.lines import AXIS_COLORS, Geometry
from mhfu_studio.shell.shaders import program, uniform

GROUND_VS = """
#version 330 core
uniform mat4 u_mvp;
in vec3 in_pos;
out vec3 v_world;
void main() {
    v_world = in_pos;
    gl_Position = u_mvp * vec4(in_pos, 1.0);
}
"""

#: one quad shaded procedurally: the line width follows the screen derivative, so the grid
#: stays crisp near and fades rather than moires far
GROUND_FS = """
#version 330 core
uniform float u_cell;
uniform float u_major;
uniform float u_extent;
uniform vec3  u_minor_color;
uniform vec3  u_major_color;
uniform vec3  u_axis_x;
uniform vec3  u_axis_z;
in vec3 v_world;
out vec4 f_color;

vec2 line_px(vec2 p, float cell) {
    vec2 d = fwidth(p) / cell;
    return abs(fract(p / cell - 0.5) - 0.5) / max(d, vec2(1e-6));
}

void main() {
    vec2 p = v_world.xz;
    vec2 minor = line_px(p, u_cell);
    vec2 major = line_px(p, u_cell * u_major);
    float m_minor = 1.0 - min(min(minor.x, minor.y), 1.0);
    float m_major = 1.0 - min(min(major.x, major.y), 1.0);
    vec2 ax = abs(p) / max(fwidth(p), vec2(1e-6));
    float on_z = 1.0 - min(ax.x, 1.0);
    float on_x = 1.0 - min(ax.y, 1.0);
    vec3 rgb = u_minor_color;
    float a = m_minor * 0.30;
    if (m_major > a) { rgb = u_major_color; a = m_major * 0.50; }
    if (on_z * 0.75 > a) { rgb = u_axis_z; a = on_z * 0.75; }
    if (on_x * 0.75 > a) { rgb = u_axis_x; a = on_x * 0.75; }
    // circular fade; edge0 < edge1 or the grid vanishes at the origin instead
    a *= 1.0 - smoothstep(u_extent * 0.30, u_extent * 0.50, length(p));
    if (a < 0.004) discard;
    f_color = vec4(rgb, a);
}
"""


def nice_cell(radius: float) -> float:
    """A 1/2/5 x 10^n cell, about ten minor lines across the subject."""
    if radius <= 0:
        return 1.0
    raw = radius / 5.0
    mag = 10.0 ** np.floor(np.log10(raw))
    return float(mag * min((1, 2, 5, 10), key=lambda m: abs(raw / mag - m)))


class Ground:
    """A ground plane at y = 0, fitted once per scene so the world does not breathe."""

    def __init__(self, ctx: moderngl.Context) -> None:
        self.ctx = ctx
        self.prog = program(ctx, GROUND_VS, GROUND_FS)
        self.cell = 100.0
        self.extent = 10_000.0
        self._vbo = ctx.buffer(reserve=6 * 3 * 4, dynamic=True)
        self._vao = ctx.vertex_array(self.prog, [(self._vbo, "3f", "in_pos")])
        self._built: float | None = None

    def fit(self, bounds: Bounds) -> None:
        self.cell = nice_cell(bounds.radius)
        self.extent = max(bounds.radius * 12.0, self.cell * 40.0)
        self._build()

    def _build(self) -> None:
        if self._built == self.extent:
            return
        e = self.extent
        quad = [(-e, 0, -e), (e, 0, -e), (e, 0, e), (-e, 0, -e), (e, 0, e), (-e, 0, e)]
        self._vbo.write(np.array(quad, "f4").tobytes())
        self._built = self.extent

    def render(self, mvp: Mat) -> None:
        self._build()
        p = self.prog
        uniform(p, "u_mvp").write(gl_bytes(mvp))
        uniform(p, "u_cell").value = self.cell
        uniform(p, "u_major").value = 5.0
        uniform(p, "u_extent").value = self.extent
        uniform(p, "u_minor_color").value = (0.32, 0.34, 0.38)
        uniform(p, "u_major_color").value = (0.46, 0.48, 0.53)
        uniform(p, "u_axis_x").value = AXIS_COLORS[0]
        uniform(p, "u_axis_z").value = AXIS_COLORS[2]
        self._vao.render(mode=self.ctx.TRIANGLES)

    def release(self) -> None:
        self._vao.release()
        self._vbo.release()


def point_cloud_geometry(
    positions: npt.ArrayLike,
    low: Sequence[float] = (0.30, 0.55, 0.85),
    high: Sequence[float] = (0.95, 0.80, 0.45),
) -> Geometry:
    """Points tinted by height, so a silhouette reads as a shape."""
    p = np.ascontiguousarray(positions, "f4").reshape(-1, 3)
    if not len(p):
        return p, np.zeros((0, 4), "f4")
    h = p[:, 1]
    span = float(h.max() - h.min())
    t = ((h - h.min()) / span if span > 1e-6 else np.zeros(len(p)))[:, None]
    col = np.asarray(low, "f4") * (1 - t) + np.asarray(high, "f4") * t
    return p, np.concatenate([col, np.ones((len(p), 1), "f4")], axis=1).astype("f4")
