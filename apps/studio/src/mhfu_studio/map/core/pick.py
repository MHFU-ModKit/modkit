# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A ray against the section's faces, and a screen rectangle against its vertices.

A vectorised Moller-Trumbore over every drawn face is well under a millisecond on a section,
so there is no acceleration structure. Two-sided, as the game draws.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np

from .scene import Array, Key, MapScene


@dataclass
class Hit:
    key: Key
    face: int
    t: float
    point: Array


def ray_faces(
    origin: Sequence[float] | Array,
    direction: Sequence[float] | Array,
    verts: Array,
    tris: Array,
    eps: float = 1e-9,
) -> tuple[Array, Array]:
    """`t` per face (inf where missed) and the mask of hits."""
    o = np.asarray(origin, np.float64)
    d = np.asarray(direction, np.float64)
    v0 = verts[tris[:, 0]].astype(np.float64)
    e1 = verts[tris[:, 1]].astype(np.float64) - v0
    e2 = verts[tris[:, 2]].astype(np.float64) - v0
    p = np.cross(d, e2)
    det = (e1 * p).sum(1)
    ok = np.abs(det) > eps
    inv = np.zeros_like(det)
    inv[ok] = 1.0 / det[ok]
    s = o - v0
    u = (s * p).sum(1) * inv
    q = np.cross(s, e1)
    v = (d * q).sum(1) * inv
    t = (e2 * q).sum(1) * inv
    hit = ok & (u >= -1e-6) & (v >= -1e-6) & (u + v <= 1 + 1e-6) & (t > 1e-6)
    return np.where(hit, t, np.inf), hit


def pick(
    scene: MapScene,
    origin: Sequence[float] | Array,
    direction: Sequence[float] | Array,
    *,
    keys: Iterable[Key] | None = None,
    hidden: Iterable[Key] = (),
    backdrop: bool = False,
) -> Hit | None:
    """The nearest face under the ray among the groups that are drawn."""
    skip = set(hidden)
    want = None if keys is None else set(keys)
    best: Hit | None = None
    for g in scene.groups:
        if not g.n_faces or g.key in skip or (want is not None and g.key not in want):
            continue
        if g.backdrop and not backdrop:
            continue
        t, hit = ray_faces(origin, direction, g.positions, g.triangles)
        if not hit.any():
            continue
        i = int(np.argmin(t))
        if best is None or t[i] < best.t:
            point = np.asarray(origin, np.float64) + np.asarray(direction, np.float64) * t[i]
            best = Hit(g.key, i, float(t[i]), point)
    return best


def pick_collision(
    scene: MapScene,
    origin: Sequence[float] | Array,
    direction: Sequence[float] | Array,
    chunks: Sequence[int] | None = None,
) -> tuple[int, int, float] | None:
    """(chunk, triangle, t) of the nearest live collision triangle under the ray."""
    best = None
    for c in scene.collision:
        if (chunks is not None and c.index not in chunks) or not c.n_triangles:
            continue
        tris = np.arange(c.n_triangles * 3).reshape(-1, 3)
        t, hit = ray_faces(origin, direction, c.verts.reshape(-1, 3), tris)
        t = np.where(hit & c.alive, t, np.inf)
        if np.isfinite(t).any():
            i = int(np.argmin(t))
            if best is None or t[i] < best[2]:
                best = (c.index, i, float(t[i]))
    return best


def in_rect(projected: Array, x0: float, y0: float, x1: float, y1: float) -> Array:
    """Projected points (`camera.project` rows) inside a screen rectangle, in front of the eye."""
    lo_x, hi_x = min(x0, x1), max(x0, x1)
    lo_y, hi_y = min(y0, y1), max(y0, y1)
    p = projected
    out: Array = (
        (p[:, 0] >= lo_x)
        & (p[:, 0] <= hi_x)
        & (p[:, 1] >= lo_y)
        & (p[:, 1] <= hi_y)
        & (p[:, 2] >= 0.0)
        & (p[:, 2] <= 1.0)
    )
    return out
