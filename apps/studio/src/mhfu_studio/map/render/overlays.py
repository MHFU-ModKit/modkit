# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Everything drawn that is not the section's mesh: the collision by class, and the geometry
of the lattice, exits, arrivals and overlay spheres.

Colours read against snow and jungle alike: saturated, never terrain green or brown for the
classes that matter (climbable is gold).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

import moderngl
import numpy as np

from mhfu_studio.shell.camera import Mat, gl_bytes
from mhfu_studio.shell.lines import Geometry, Lines, circle_geometry, tinted
from mhfu_studio.shell.shaders import uniform

from ..core.scene import (
    CLASSES,
    CLIMB,
    FLOOR,
    SINK,
    WADE,
    WALL,
    Array,
    CollisionChunk,
    ExitRecord,
    MapScene,
    Sphere,
)
from .shaders import flat_program

Pair = tuple[int, int]
RGBA = tuple[float, float, float, float]

CLASS_COLORS: dict[str, tuple[float, float, float]] = {
    FLOOR: (0.25, 0.80, 0.45),
    WALL: (0.90, 0.35, 0.30),
    CLIMB: (1.00, 0.82, 0.10),
    SINK: (0.30, 0.50, 0.95),
    WADE: (0.30, 0.85, 0.90),
}
EXIT_COLOR: RGBA = (1.00, 0.55, 0.15, 1.0)
ARRIVAL_COLOR: RGBA = (0.35, 0.95, 0.55, 1.0)
SPHERE_COLOR: RGBA = (0.80, 0.45, 0.95, 1.0)
LATTICE_COLOR: RGBA = (0.55, 0.58, 0.66, 0.35)
LATTICE_MAJOR: RGBA = (0.70, 0.72, 0.80, 0.55)
SELECTED = (1.0, 1.0, 1.0)
DEPTH_BIAS = (-1.0, -2.0)
"""Pulls the overlay toward the eye, so the drawn floor does not z-fight a collision floor."""


class CollisionOverlay:
    """The `HITS` chunks as translucent triangles and edges, coloured by class."""

    def __init__(self, ctx: moderngl.Context, scene: MapScene) -> None:
        self.ctx = ctx
        self.scene = scene
        self.prog = flat_program(ctx)
        self.fill_alpha = 0.28
        self.edge_alpha = 0.55
        self.show_chunk = {c.index: True for c in scene.collision}
        self.show_class = dict.fromkeys(CLASSES, True)
        self.selected: set[Pair] = set()
        self.hovered: Pair | None = None
        self._fill: dict[int, tuple[moderngl.Buffer, moderngl.VertexArray, int]] = {}
        self._edges: dict[int, Lines] = {}
        self._colours: dict[int, Array] = {}
        self.rebuild()

    def rebuild(self) -> None:
        """Every chunk from the scene again: at load, and after the collision was re-derived."""
        self._release()
        for c in self.scene.collision:
            if not c.n_triangles:
                continue
            col = np.zeros((c.n_triangles, 4), np.float32)
            for k in CLASSES:
                col[c.klass == k, :3] = CLASS_COLORS[k]
            col[:, 3] = 1.0
            self._colours[c.index] = col
            vbo = self.ctx.buffer(reserve=c.n_triangles * 3 * 7 * 4, dynamic=True)
            vao = self.ctx.vertex_array(self.prog, [(vbo, "3f 4f", "in_pos", "in_color")])
            self._fill[c.index] = (vbo, vao, c.n_triangles * 3)
            self._edges[c.index] = Lines(self.ctx)
            self._refresh(c.index)

    def _refresh(self, chunk: int) -> None:
        """Colour and alpha per triangle (class toggles, deleted ones, selection, hover) and
        the positions (a preview moves them)."""
        c = self.scene.chunk(chunk)
        if len(self._colours.get(chunk, ())) != c.n_triangles:
            self.rebuild()
            return
        col = self._colours[chunk].copy()
        visible = np.isin(c.klass, [k for k in CLASSES if self.show_class.get(k, True)])
        col[~(visible & c.alive), 3] = 0.0
        for ch, t in self.selected:
            if ch == chunk and 0 <= t < c.n_triangles:
                col[t] = (*SELECTED, 1.0)
        if self.hovered is not None and self.hovered[0] == chunk:
            t = self.hovered[1]
            if 0 <= t < c.n_triangles:
                col[t, :3] = np.minimum(col[t, :3] + 0.45, 1.0)
        pos = c.verts.reshape(-1, 3).astype("f4")
        self._fill[chunk][0].write(
            np.concatenate([pos, np.repeat(col, 3, axis=0)], 1).astype("f4").tobytes()
        )
        self._edges[chunk].set(tri_edges(c.verts), np.repeat(col, 6, axis=0))

    def refresh_all(self) -> None:
        for chunk in list(self._fill):
            self._refresh(chunk)

    def set_class(self, klass: str, on: bool) -> None:
        if self.show_class.get(klass) != on:
            self.show_class[klass] = on
            self.refresh_all()

    def select(self, tris: Iterable[Pair] | None) -> None:
        new = {(int(c), int(t)) for c, t in tris or ()}
        if new != self.selected:
            touched = {c for c, _ in self.selected | new}
            self.selected = new
            for c in touched & set(self._fill):
                self._refresh(c)

    def hover(self, pair: Pair | None) -> None:
        if pair != self.hovered:
            old, self.hovered = self.hovered, pair
            for c in {x[0] for x in (old, pair) if x is not None} & set(self._fill):
                self._refresh(c)

    def render(self, mvp: Mat, *, fill: bool = True, edges: bool = True) -> None:
        ctx = self.ctx
        uniform(self.prog, "u_mvp").write(gl_bytes(mvp))
        ctx.polygon_offset = DEPTH_BIAS
        try:
            for chunk, (_, vao, n) in self._fill.items():
                if not self.show_chunk.get(chunk, True):
                    continue
                if fill:
                    uniform(self.prog, "u_alpha").value = float(self.fill_alpha)
                    vao.render(mode=ctx.TRIANGLES, vertices=n)
                if edges:
                    e = self._edges[chunk]
                    e.alpha = self.edge_alpha
                    e.render(mvp)
        finally:
            ctx.polygon_offset = (0.0, 0.0)

    def _release(self) -> None:
        for vbo, vao, _ in self._fill.values():
            vao.release()
            vbo.release()
        for e in self._edges.values():
            e.release()
        self._fill, self._edges, self._colours = {}, {}, {}

    def release(self) -> None:
        self._release()


def tri_edges(verts: Array) -> Array:
    v = np.asarray(verts, "f4").reshape(-1, 3, 3)
    out: Array = np.stack([v[:, 0], v[:, 1], v[:, 1], v[:, 2], v[:, 2], v[:, 0]], 1).reshape(-1, 3)
    return out


def lattice_geometry(chunk: CollisionChunk, y: float = 0.0, major: int = 5) -> Geometry:
    """The broadphase grid on the plane `y`, every `major`th line brighter."""
    (nx, nz), (cx, cz), (ox, oz) = chunk.grid, chunk.cell, chunk.origin
    pos: list[tuple[float, float, float]] = []
    col: list[RGBA] = []
    for i in range(nx + 1):
        x = ox + i * cx
        pos += [(x, y, oz), (x, y, oz + nz * cz)]
        col += [LATTICE_MAJOR if i % major == 0 else LATTICE_COLOR] * 2
    for j in range(nz + 1):
        z = oz + j * cz
        pos += [(ox, y, z), (ox + nx * cx, y, z)]
        col += [LATTICE_MAJOR if j % major == 0 else LATTICE_COLOR] * 2
    return np.array(pos, "f4").reshape(-1, 3), np.array(col, "f4").reshape(-1, 4)


def exit_geometry(e: ExitRecord, color: RGBA = EXIT_COLOR) -> Geometry:
    """The trigger cylinder: two rings, four uprights, a cross on the floor."""
    cx, cy, cz = e.trigger
    r, h = e.radius, e.height
    parts = [circle_geometry((cx, cy, cz), r), circle_geometry((cx, cy + h, cz), r)]
    for a in (0, 90, 180, 270):
        x, z = cx + r * math.cos(math.radians(a)), cz + r * math.sin(math.radians(a))
        parts.append(np.array([(x, cy, z), (x, cy + h, z)], "f4"))
    cross = [(cx - r, cy, cz), (cx + r, cy, cz), (cx, cy, cz - r), (cx, cy, cz + r)]
    parts.append(np.array(cross, "f4"))
    return tinted(np.concatenate(parts), color)


def sphere_geometry(s: Sphere, color: RGBA = SPHERE_COLOR) -> Geometry:
    """Three great circles."""
    return tinted(np.concatenate([circle_geometry(s.pos, s.radius, axis=a) for a in "xyz"]), color)


def arrival_geometry(
    dest: Sequence[float], yaw_deg: float, color: RGBA = ARRIVAL_COLOR, size: float = 250.0
) -> Geometry:
    """Where the hunter lands and which way he faces: a ring, a pole, an arrow."""
    x, y, z = dest
    r = math.radians(yaw_deg)
    dx, dz = math.sin(r), math.cos(r)
    lx, lz = math.cos(r), -math.sin(r)
    tip = (x + dx * size * 1.6, y + 20, z + dz * size * 1.6)
    back = (x + dx * size * 1.1, y + 20, z + dz * size * 1.1)
    wing = size * 0.3
    arrow = [
        (x, y + 20, z),
        tip,
        tip,
        (back[0] + lx * wing, back[1], back[2] + lz * wing),
        tip,
        (back[0] - lx * wing, back[1], back[2] - lz * wing),
    ]
    parts = [
        circle_geometry((x, y + 20, z), size * 0.6),
        np.array([(x, y, z), (x, y + size * 0.8, z)], "f4"),
        np.array(arrow, "f4"),
    ]
    return tinted(np.concatenate(parts), color)
