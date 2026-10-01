# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Wavefront OBJ an op names: world-space positions, optional UVs, faces fanned."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

Vec3 = tuple[float, float, float]
Triangle = tuple[int, int, int]


@dataclass
class ObjMesh:
    """One vertex per distinct (position, uv) corner, so a seam keeps both UVs."""

    positions: list[Vec3]
    uvs: list[tuple[float, float]] | None
    """Per vertex, (0, 0) where a corner has none; None when no face corner has one."""
    triangles: list[Triangle]

    def corners(self) -> list[tuple[Vec3, Vec3, Vec3]]:
        return [
            (self.positions[a], self.positions[b], self.positions[c]) for a, b, c in self.triangles
        ]


def read_obj(path: Path) -> ObjMesh:
    """Only `v`, `vt` and `f` are read; a polygon is fanned from its first corner."""
    pos: list[Vec3] = []
    uv: list[tuple[float, float]] = []
    corners: dict[tuple[int, int | None], int] = {}
    order: list[tuple[int, int | None]] = []
    tris: list[Triangle] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        w = line.split()
        if not w:
            continue
        if w[0] == "v":
            pos.append((float(w[1]), float(w[2]), float(w[3])))
        elif w[0] == "vt":
            uv.append((float(w[1]), float(w[2])))
        elif w[0] == "f":
            face = []
            for token in w[1:]:
                parts = token.split("/")
                vi = _index(int(parts[0]), len(pos))
                ti = _index(int(parts[1]), len(uv)) if len(parts) > 1 and parts[1] else None
                key = (vi, ti)
                if key not in corners:
                    corners[key] = len(order)
                    order.append(key)
                face.append(corners[key])
            tris += [(face[0], face[k], face[k + 1]) for k in range(1, len(face) - 1)]
    has_uv = any(ti is not None for _, ti in order)
    return ObjMesh(
        [pos[vi] for vi, _ in order],
        [uv[ti] if ti is not None else (0.0, 0.0) for _, ti in order] if has_uv else None,
        tris,
    )


def _index(i: int, n: int) -> int:
    """OBJ indices count from 1; a negative one counts back from the last seen."""
    k = i - 1 if i > 0 else n + i
    if not 0 <= k < n:
        raise ValueError(f"an OBJ face names element {i} of {n}")
    return k
