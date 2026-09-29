# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Binding the donor's vertices to the output joints, three ways: the donor's own weights, a guess
from the bind pose, or the weights of the host's own model where the two overlap."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Collection, Mapping, Sequence

import numpy as np
import numpy.typing as npt
from mhp_formats import pmo
from mhp_formats.pmo import Influence, Vec3

from .mesh import PALETTE, Part, Skinned

Array = npt.NDArray[np.float64]
Rows = list[list[Influence]]
Corners = tuple[list[Influence], list[Influence], list[Influence]]

REGION_HOPS = 3
"""How far along the tree `auto` lets a part reach from its dominant joint: a whole limb or tail,
never across the spine's fork into another."""
WELD_EPS = 1.5
"""Model units within which `weld` counts vertices as one."""
_CELL = 2.0
"""`weld`'s grid; at least `WELD_EPS`, so a vertex's neighbours are in the 27 cells around it."""
_FAINT = 0.01
"""A weight `weld` does not count towards the shared joint."""
_SOFT = 1e-3
"""Model units added to `auto`'s distances, so a vertex on a joint gets a finite weight."""
_CHUNK = 32
"""Vertices `transfer` measures against the host's surface at once."""


def source(parts: Sequence[Part], joint_of: Mapping[int, int]) -> list[Skinned]:
    """The donor's own weights: each donor bone through `joint_of` (a bone missing there drops
    and its weight goes to the vertex's others), each group capped at the palette's most
    weighted joints."""
    out = []
    for part in parts:
        rows = []
        for row in part.influences:
            acc: dict[int, float] = {}
            for bone, w in row:
                joint = joint_of.get(bone, -1) if bone >= 0 and w else -1
                if joint >= 0:
                    acc[joint] = acc.get(joint, 0.0) + w
            rows.append(_normalised(acc))
        out.append(Skinned(part, _cap(rows)))
    return out


def auto(
    parts: Sequence[Part],
    bind: Sequence[Vec3],
    parents: Sequence[int],
    *,
    exclude: Collection[int] = (),
    nb: int = 3,
) -> list[Skinned]:
    """A guess from the bind pose: each vertex blends its `nb` nearest joint segments by
    inverse distance, among the joints near its part's dominant one, never an `exclude` one.
    `bind` and `parents` are per output joint, `bind` in model units."""
    joints = np.asarray(bind, dtype=np.float64)
    n = len(joints)
    live = np.array([j for j in range(n) if j not in exclude], dtype=np.int64)
    ends = np.array([joints[p] if 0 <= p < n else joints[j] for j, p in enumerate(parents)])
    region_of = _neighborhoods(parents, REGION_HOPS)
    out = []
    for part in parts:
        if not part.positions:
            out.append(Skinned(part, []))
            continue
        d2 = _seg_dist2(np.asarray(part.positions, dtype=np.float64), joints[live], ends[live])
        order = np.argsort(d2, axis=1, kind="stable")
        nearest = [int(j) for j in live[order[:, 0]]]
        region = region_of[Counter(nearest).most_common(1)[0][0]]
        rows = []
        for dist, rank in zip(d2, order, strict=True):
            ranked = [(float(dist[r]), int(live[r])) for r in rank]
            near = [(d, j) for d, j in ranked if j in region][:nb] or ranked[:1]
            ws = [(j, 1.0 / (math.sqrt(d) + _SOFT)) for d, j in near]
            total = sum(w for _, w in ws) or 1.0
            rows.append([(j, w / total) for j, w in ws])
        out.append(Skinned(part, _cap(rows)))
    return out


def weld(
    skinned: Sequence[Skinned],
    bind: Sequence[Vec3],
    *,
    min_bonedist: float = 150.0,
    max_bonedist: float = 300.0,
) -> int:
    """Close the tears `auto` leaves: vertices that coincide across groups but ride joints
    `min_bonedist`..`max_bonedist` apart all go to one shared joint. In place; returns how many
    clusters it welded.

    The spread is between the vertices' dominant joints; past `max_bonedist` it is a membrane
    that stretches without tearing (a wing root), and welding it would stiffen it.
    """
    items = [(g, v, p) for g, s in enumerate(skinned) for v, p in enumerate(s.part.positions)]
    used = [{j for row in s.influences for j, w in row if w and j >= 0} for s in skinned]
    grid: dict[tuple[int, int, int], list[int]] = {}
    for k, (_, _, p) in enumerate(items):
        grid.setdefault(_cell(p), []).append(k)
    visited: set[int] = set()
    welded = 0
    for k, (_, _, p) in enumerate(items):
        if k in visited:
            continue
        cx, cy, cz = _cell(p)
        cluster = [k] + [
            j
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            for dz in (-1, 0, 1)
            for j in grid.get((cx + dx, cy + dy, cz + dz), [])
            if j != k and j not in visited and _dist(items[j][2], p) <= WELD_EPS
        ]
        groups = {items[c][0] for c in cluster}
        if len(groups) < 2:
            continue
        weight: dict[int, float] = {}
        dominant = []
        for c in cluster:
            row = skinned[items[c][0]].influences[items[c][1]]
            for j, w in row:
                if w > _FAINT and 0 <= j < len(bind):
                    weight[j] = weight.get(j, 0.0) + w
            if row:
                top = max(row, key=lambda jw: jw[1])[0]
                if 0 <= top < len(bind):
                    dominant.append(top)
        if len(weight) < 2 or len(set(dominant)) < 2:
            continue
        spread = max(_dist(bind[a], bind[b]) for a in dominant for b in dominant)
        if not min_bonedist <= spread <= max_bonedist:
            continue
        shared = next(
            (
                j
                for j, _ in sorted(weight.items(), key=lambda jw: -jw[1])
                if all(j in used[g] or len(used[g]) < PALETTE for g in groups)
            ),
            None,
        )
        if shared is None:
            continue
        for c in cluster:
            g, v, _ = items[c]
            skinned[g].influences[v] = [(shared, 1.0)]
            used[g].add(shared)
            visited.add(c)
        welded += 1
    return welded


def transfer(
    parts: Sequence[Part],
    host: pmo.Pmo,
    parents: Sequence[int],
    *,
    dead: Collection[int] = (),
) -> list[Skinned]:
    """The host model's own weights, sampled at the closest point of its surface. A `dead`
    joint (one the animation leaves at rest) hands its weight to its nearest live ancestor."""
    a, b, c, corners = _surface(host)
    dead = set(dead)
    out = []
    for part in parts:
        rows = []
        points = np.asarray(part.positions, dtype=np.float64).reshape(-1, 3)
        for lo in range(0, len(points), _CHUNK):
            p = points[lo : lo + _CHUNK]
            bary = _closest_bary(p, a, b, c)
            at = bary[..., 0:1] * a + bary[..., 1:2] * b + bary[..., 2:3] * c
            nearest = np.argmin(((p[:, None, :] - at) ** 2).sum(-1), axis=1)
            for i, t in enumerate(nearest):
                acc: dict[int, float] = {}
                for row, share in zip(corners[t], bary[i, t], strict=True):
                    if share <= 0:
                        continue
                    for joint, w in row:
                        if w and joint >= 0:
                            j = _live_ancestor(joint, parents, dead) if dead else joint
                            acc[j] = acc.get(j, 0.0) + float(share) * w
                rows.append(_normalised(acc))
        out.append(Skinned(part, _cap(rows)))
    return out


def _normalised(acc: dict[int, float]) -> list[Influence]:
    """The weights summing to one; weight 1 on joint 0 when there are none."""
    acc = acc or {0: 1.0}
    total = sum(acc.values()) or 1.0
    return [(j, w / total) for j, w in acc.items()]


def _cap(rows: Rows) -> Rows:
    """Keep the group's `PALETTE` most weighted joints and renormalise each vertex over them; a
    vertex left with none goes whole to the heaviest."""
    total: dict[int, float] = {}
    for row in rows:
        for j, w in row:
            total[j] = total.get(j, 0.0) + w
    kept = [j for j, _ in sorted(total.items(), key=lambda jw: -jw[1])[:PALETTE]]
    out = []
    for row in rows:
        inside = [(j, w) for j, w in row if j in kept] or [(kept[0], 1.0)]
        s = sum(w for _, w in inside) or 1.0
        out.append([(j, w / s) for j, w in inside])
    return out


def _neighborhoods(parents: Sequence[int], hops: int) -> list[set[int]]:
    """Per joint, the joints within `hops` edges of the tree, itself included."""
    n = len(parents)
    adjacent: list[set[int]] = [set() for _ in range(n)]
    for i, p in enumerate(parents):
        if 0 <= p < n:
            adjacent[i].add(p)
            adjacent[p].add(i)
    out = []
    for i in range(n):
        seen, frontier = {i}, {i}
        for _ in range(hops):
            frontier = {j for f in frontier for j in adjacent[f]} - seen
            seen |= frontier
        out.append(seen)
    return out


def _seg_dist2(p: Array, a: Array, b: Array) -> Array:
    """Squared distance of each point (n, 3) to each segment a-b (m, 3): (n, m). A segment of no
    length is its point a."""
    d = b - a
    length2 = d[:, 0] * d[:, 0] + d[:, 1] * d[:, 1] + d[:, 2] * d[:, 2]
    ap = p[:, None, :] - a[None, :, :]
    along = ap[..., 0] * d[:, 0] + ap[..., 1] * d[:, 1] + ap[..., 2] * d[:, 2]
    point = length2 < 1e-9
    t = np.clip(along / np.where(point, 1.0, length2), 0.0, 1.0)
    t = np.where(point, 0.0, t)
    closest = a[None, :, :] + t[..., None] * d[None, :, :]
    off = p[:, None, :] - closest
    squared: Array = off[..., 0] ** 2 + off[..., 1] ** 2 + off[..., 2] ** 2
    return squared


def _cell(p: Vec3) -> tuple[int, int, int]:
    return round(p[0] / _CELL), round(p[1] / _CELL), round(p[2] / _CELL)


def _dist(a: Vec3, b: Vec3) -> float:
    return float(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5)


def _surface(host: pmo.Pmo) -> tuple[Array, Array, Array, list[Corners]]:
    """The host's triangles as corner arrays (t, 3) and each corner's influences; triangles of
    no area are left out."""
    corners_at: list[tuple[Vec3, Vec3, Vec3]] = []
    corners: list[Corners] = []
    for g in range(len(host.groups())):
        positions, influences = host.positions(g), host.influences(g)
        for tri in host.triangles(g):
            corners_at.append((positions[tri[0]], positions[tri[1]], positions[tri[2]]))
            corners.append((influences[tri[0]], influences[tri[1]], influences[tri[2]]))
    abc = np.asarray(corners_at, dtype=np.float64).reshape(-1, 3, 3)
    e1, e2 = abc[:, 1] - abc[:, 0], abc[:, 2] - abc[:, 0]
    cross = np.stack(
        [
            e1[:, 1] * e2[:, 2] - e1[:, 2] * e2[:, 1],
            e1[:, 2] * e2[:, 0] - e1[:, 0] * e2[:, 2],
            e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0],
        ],
        axis=1,
    )
    area = (cross[:, 0] ** 2 + cross[:, 1] ** 2 + cross[:, 2] ** 2) >= 1e-6
    if not area.any():
        raise ValueError("the host model has no triangle with an area")
    keep = np.flatnonzero(area)
    return abc[keep, 0], abc[keep, 1], abc[keep, 2], [corners[t] for t in keep]


def _closest_bary(p: Array, a: Array, b: Array, c: Array) -> Array:
    """Barycentric coordinates (n, t, 3) of the point of each triangle closest to each point:
    Ericson's ClosestPtPointTriangle (Real-Time Collision Detection 5.1.5), vectorised."""
    ab, ac = b - a, c - a
    ap, bp, cp = p[:, None] - a, p[:, None] - b, p[:, None] - c
    d1, d2 = (ab * ap).sum(-1), (ac * ap).sum(-1)
    d3, d4 = (ab * bp).sum(-1), (ac * bp).sum(-1)
    d5, d6 = (ab * cp).sum(-1), (ac * cp).sum(-1)
    va, vb, vc = d3 * d6 - d5 * d4, d5 * d2 - d1 * d6, d1 * d4 - d3 * d2
    denom = va + vb + vc
    denom = np.where(np.abs(denom) > 1e-20, denom, 1.0)
    fv, fw = vb / denom, vc / denom
    bary = np.stack([1.0 - fv - fw, fv, fw], axis=-1)

    def region(bary: Array, mask: npt.NDArray[np.bool_], *uvw: Array | float) -> Array:
        corner = np.stack(np.broadcast_arrays(*uvw, va)[:3], axis=-1)
        out: Array = np.where(mask[..., None], corner, bary)
        return out

    # lowest priority first, so the highest wins
    near_b, near_c = d4 - d3, d5 - d6
    w = near_b / np.where(near_b + near_c != 0, near_b + near_c, 1.0)
    bary = region(bary, (va <= 0) & (near_b >= 0) & (near_c >= 0), 0.0, 1.0 - w, w)
    w = d2 / np.where(d2 - d6 != 0, d2 - d6, 1.0)
    bary = region(bary, (vb <= 0) & (d2 >= 0) & (d6 <= 0), 1.0 - w, 0.0, w)
    bary = region(bary, (d6 >= 0) & (d5 <= d6), 0.0, 0.0, 1.0)
    v = d1 / np.where(d1 - d3 != 0, d1 - d3, 1.0)
    bary = region(bary, (vc <= 0) & (d1 >= 0) & (d3 <= 0), 1.0 - v, v, 0.0)
    bary = region(bary, (d3 >= 0) & (d4 <= d3), 0.0, 1.0, 0.0)
    return region(bary, (d1 <= 0) & (d2 <= 0), 1.0, 0.0, 0.0)


def _live_ancestor(joint: int, parents: Sequence[int], dead: Collection[int]) -> int:
    """The first joint up the chain that is not dead; `joint` itself when there is none."""
    seen = set()
    j = joint
    while 0 <= j < len(parents) and j in dead and j not in seen:
        seen.add(j)
        j = parents[j]
    return j if 0 <= j < len(parents) and j not in dead else joint
