# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Basic geometry for the Add panel, as the OBJ files a `pack` op names.

Every builder returns world-space vertices (v, 3), triangles (f, 3) and UVs (v, 2) in 0..1:
a plain unwrap that the op's `uv: obj` takes and `planar` ignores.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from .scene import Array, Key, MapScene, Point

Mesh = tuple[Array, Array, Array]
SHAPES = ("box", "plane", "ramp", "cylinder", "sphere")


def _finish(verts: Any, tris: Any, uvs: Any, at: Point, rotate_y: float = 0.0) -> Mesh:
    v = np.asarray(verts, np.float64).reshape(-1, 3)
    if rotate_y:
        a = math.radians(rotate_y)
        c, s = math.cos(a), math.sin(a)
        v = v @ np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]).T
    v = v + np.asarray(at, np.float64)
    return (
        v.astype(np.float32),
        np.asarray(tris, np.int32).reshape(-1, 3),
        np.asarray(uvs, np.float32).reshape(-1, 2),
    )


def box(size: Point = (400.0, 300.0, 400.0), at: Point = (0, 0, 0), rotate_y: float = 0.0) -> Mesh:
    """A closed box standing on `at`, 24 vertices so every face has its own UVs."""
    sx, sy, sz = (float(s) / 2.0 for s in size)
    h = 2 * sy
    quads: list[list[tuple[float, float, float]]] = [  # corners counter-clockwise from outside
        [(sx, 0, -sz), (sx, 0, sz), (sx, h, sz), (sx, h, -sz)],
        [(-sx, 0, sz), (-sx, 0, -sz), (-sx, h, -sz), (-sx, h, sz)],
        [(-sx, h, -sz), (sx, h, -sz), (sx, h, sz), (-sx, h, sz)],
        [(-sx, 0, sz), (sx, 0, sz), (sx, 0, -sz), (-sx, 0, -sz)],
        [(sx, 0, sz), (-sx, 0, sz), (-sx, h, sz), (sx, h, sz)],
        [(-sx, 0, -sz), (sx, 0, -sz), (sx, h, -sz), (-sx, h, -sz)],
    ]
    verts: list[tuple[float, float, float]] = []
    tris: list[tuple[int, int, int]] = []
    uvs: list[tuple[int, int]] = []
    for quad in quads:
        b = len(verts)
        verts += quad
        uvs += [(0, 1), (1, 1), (1, 0), (0, 0)]
        tris += [(b, b + 1, b + 2), (b, b + 2, b + 3)]
    return _finish(verts, tris, uvs, at, rotate_y)


def plane(
    size: Point = (1000.0, 1000.0), at: Point = (0, 0, 0), divisions: int = 1, rotate_y: float = 0.0
) -> Mesh:
    """A horizontal quad, or an n x n grid of them, centred on `at`."""
    n = max(1, int(divisions))
    sx, sz = float(size[0]), float(size[1])
    verts, uvs = [], []
    for j in range(n + 1):
        for i in range(n + 1):
            u, w = i / n, j / n
            verts.append((-sx / 2 + u * sx, 0.0, -sz / 2 + w * sz))
            uvs.append((u, w))
    tris = []
    for j in range(n):
        for i in range(n):
            a = j * (n + 1) + i
            b, c, d = a + 1, a + n + 1, a + n + 2
            tris += [(a, c, b), (b, c, d)]
    return _finish(verts, tris, uvs, at, rotate_y)


def ramp(size: Point = (400.0, 200.0, 800.0), at: Point = (0, 0, 0), rotate_y: float = 0.0) -> Mesh:
    """A wedge rising along +Z to `size[1]`, standing on `at`."""
    sx, h, sz = float(size[0]) / 2, float(size[1]), float(size[2]) / 2
    p = [(-sx, 0, -sz), (sx, 0, -sz), (sx, 0, sz), (-sx, 0, sz), (sx, h, sz), (-sx, h, sz)]
    verts: list[tuple[float, float, float]] = []
    tris: list[tuple[int, int, int]] = []
    uvs: list[tuple[float, float]] = []

    def quad(a: int, b: int, c: int, d: int) -> None:
        base = len(verts)
        verts.extend([p[a], p[b], p[c], p[d]])
        uvs.extend([(0, 1), (1, 1), (1, 0), (0, 0)])
        tris.extend([(base, base + 1, base + 2), (base, base + 2, base + 3)])

    def tri(a: int, b: int, c: int) -> None:
        base = len(verts)
        verts.extend([p[a], p[b], p[c]])
        uvs.extend([(0, 1), (1, 1), (0.5, 0)])
        tris.append((base, base + 1, base + 2))

    quad(0, 1, 4, 5)  # the slope
    quad(3, 2, 1, 0)  # the underside
    quad(2, 3, 5, 4)  # the tall back
    tri(1, 2, 4)
    tri(3, 0, 5)
    return _finish(verts, tris, uvs, at, rotate_y)


def cylinder(
    radius: float = 200.0,
    height: float = 400.0,
    at: Point = (0, 0, 0),
    segments: int = 8,
    closed: bool = True,
) -> Mesh:
    """A vertical cylinder standing on `at`."""
    n = max(3, int(segments))
    verts, uvs, tris = [], [], []
    for k in range(n + 1):
        a = 2 * math.pi * (k % n) / n
        x, z = radius * math.cos(a), radius * math.sin(a)
        verts += [(x, 0.0, z), (x, height, z)]
        uvs += [(k / n, 1.0), (k / n, 0.0)]
    for k in range(n):
        b = 2 * k
        tris += [(b, b + 2, b + 3), (b, b + 3, b + 1)]
    if closed:
        top, bot = len(verts), len(verts) + 1
        verts += [(0.0, height, 0.0), (0.0, 0.0, 0.0)]
        uvs += [(0.5, 0.5), (0.5, 0.5)]
        for k in range(n):
            tris += [(top, 2 * k + 3, 2 * k + 1), (bot, 2 * k, 2 * k + 2)]
    return _finish(verts, tris, uvs, at)


def sphere(radius: float = 200.0, at: Point = (0, 0, 0), rings: int = 6, segments: int = 8) -> Mesh:
    """A UV sphere centred on `at`."""
    r, n = max(2, int(rings)), max(3, int(segments))
    verts, uvs = [], []
    for j in range(r + 1):
        phi = math.pi * j / r
        for i in range(n + 1):
            th = 2 * math.pi * (i % n) / n
            s = radius * math.sin(phi)
            verts.append((s * math.cos(th), radius * math.cos(phi), s * math.sin(th)))
            uvs.append((i / n, j / r))
    tris = []
    for j in range(r):
        for i in range(n):
            a = j * (n + 1) + i
            b, c, d = a + 1, a + n + 1, a + n + 2
            if j:
                tris.append((a, b, c))
            if j < r - 1:
                tris.append((b, d, c))
    return _finish(verts, tris, uvs, at)


def make(kind: str, **kw: Any) -> Mesh:
    builders = {"box": box, "plane": plane, "ramp": ramp, "cylinder": cylinder, "sphere": sphere}
    out: Mesh = builders[kind](**kw)  # type: ignore[operator]
    return out


def triangle_count(kind: str, **kw: Any) -> int:
    """Without building it: what the budget readout shows while the user types."""
    if kind == "box":
        return 12
    if kind == "plane":
        n = max(1, int(kw.get("divisions", 1)))
        return 2 * n * n
    if kind == "ramp":
        return 8
    if kind == "cylinder":
        n = max(3, int(kw.get("segments", 8)))
        return 2 * n + (2 * n if kw.get("closed", True) else 0)
    if kind == "sphere":
        r, n = max(2, int(kw.get("rings", 6))), max(3, int(kw.get("segments", 8)))
        return 2 * r * n - 2 * n
    raise KeyError(kind)


def from_group(
    scene: MapScene,
    key: Key,
    vertex_ids: Array,
    at: Point | None = None,
    scale: float = 1.0,
    rotate_y: float = 0.0,
) -> Mesh:
    """A native object (the faces whose corners are all in `vertex_ids`) re-based so its
    bottom centre sits on `at`: a crate copied from another section, or duplicated here."""
    g = scene.group(*key)
    ids = np.asarray(vertex_ids, np.int64)
    found = np.nonzero(np.isin(g.triangles, ids).all(1))[0] if g.n_faces else np.zeros(0, int)
    used = np.unique(g.triangles[found]) if len(found) else ids
    remap = {int(v): i for i, v in enumerate(used)}
    v = g.positions[used].astype(np.float64)
    lo, hi = v.min(0), v.max(0)
    centre = np.array([(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2])
    v = (v - centre) * float(scale)
    tris = [[remap[int(i)] for i in t] for t in g.triangles[found]]
    uvs = g.uvs[used] if g.uvs is not None else np.zeros((len(used), 2), np.float32)
    return _finish(v, tris, uvs, centre if at is None else at, rotate_y)


def write_obj(
    path: Path, verts: Array, tris: Array, uvs: Array | None = None, name: str = "shape"
) -> Path:
    """`v`, `vt` and `f a/a b/b c/c`, 1-based: what `stage.obj.read_obj` reads."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# mhfu studio shape", f"o {name}"]
    lines += [f"v {x:.3f} {y:.3f} {z:.3f}" for x, y, z in np.asarray(verts, np.float64)]
    has_uv = uvs is not None and len(uvs) == len(verts)
    if has_uv and uvs is not None:
        lines += [f"vt {u:.5f} {w:.5f}" for u, w in np.asarray(uvs, np.float64)]
    for a, b, c in np.asarray(tris, np.int64).reshape(-1, 3) + 1:
        lines.append(f"f {a}/{a} {b}/{b} {c}/{c}" if has_uv else f"f {a} {b} {c}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def next_asset_name(directory: Path, stem: str, ext: str = ".obj") -> str:
    """`assets/<stem>_<n><ext>`, the first n not taken, relative to `directory`."""
    n = 1
    while (directory / "assets" / f"{stem}_{n}{ext}").exists():
        n += 1
    return f"assets/{stem}_{n}{ext}"
