# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The collision half of an edit list, as a plan: triangles rewritten in place, triangles added,
and the cell lists that change.

The engine reads `HITS` out of the resident PAC on every query, through pointers the loader
fixed up, so the plan needs no room in the chunk: a moved triangle keeps its record, an added
one and every changed cell list go anywhere in RAM, and one grid word per cell links them in.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
from mhp_formats.fu.stage import Collision, Hits, Tri, TriFlags, cells_for_tri

from mhfu_studio.shell.findings import Finding, Level

from . import ops as O
from .file import StageFile
from .mesh import select, transform_matrix
from .obj import read_obj

Vec3 = tuple[float, float, float]
LIST_CAP = 124
"""The longest cell list retail ships; whether the engine minds a longer one is unknown."""
FLOOR = 1
"""The chunk an added triangle goes to when it faces up (|n.y| > 0.5); walls go to 0."""


class Ref(NamedTuple):
    """A cell-list entry: shipped triangle `number` of the chunk, or the plan's `added[number]`."""

    added: bool
    number: int


@dataclass
class Plan:
    """`cells` holds every changed list whole, per chunk; triangles past a chunk's shipped
    count are numbered in the order the list added them."""

    stage: int
    shipped: Collision
    moved: list[tuple[int, int, Tri]] = field(default_factory=list)
    added: list[tuple[int, Tri]] = field(default_factory=list)
    deleted: list[tuple[int, int]] = field(default_factory=list)
    cells: dict[int, dict[int, list[Ref]]] = field(default_factory=dict)
    log: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def chunks(self) -> list[Hits]:
        return self.shipped.chunks

    @property
    def empty(self) -> bool:
        return not (self.moved or self.added or any(self.cells.values()))

    def collision(self) -> Collision:
        """The edited collision as a file: added triangles appended, deleted ones unlisted."""
        chunks = []
        for ci, hits in enumerate(self.chunks):
            tris = list(hits.tris)
            for c, t, tri in self.moved:
                if c == ci:
                    tris[t] = tri
            at: dict[int, int] = {}
            for k, (c, tri) in enumerate(self.added):
                if c == ci:
                    at[k] = len(tris)
                    tris.append(tri)
            cells = [list(run) for run in hits.cells]
            for cell, refs in self.cells.get(ci, {}).items():
                cells[cell] = [at[r.number] if r.added else r.number for r in refs]
            chunks.append(replace(hits, cells=cells, tris=tris))
        return replace(self.shipped, chunks=chunks)


def box_tris(
    lo: Sequence[float], hi: Sequence[float], flags: TriFlags | None = None, inflate: float = 0.0
) -> list[Tri]:
    """A closed box as 12 triangles wound outward; a flat box leaves its zero-area faces out."""
    a = [lo[k] - inflate for k in range(3)]
    b = [hi[k] + inflate for k in range(3)]
    c = [
        (x, y, z)
        for y in (a[1], b[1])
        for x, z in ((a[0], a[2]), (b[0], a[2]), (b[0], b[2]), (a[0], b[2]))
    ]
    quads = ((4, 5, 6, 7), (1, 0, 3, 2), (0, 1, 5, 4), (2, 3, 7, 6), (3, 0, 4, 7), (1, 2, 6, 5))
    centre = np.add(a, b) / 2.0
    out = []
    for p, q, r, s in quads:
        for i, j, k in ((p, q, r), (p, r, s)):
            try:
                t = Tri.from_verts(c[i], c[j], c[k], flags)
            except ValueError:
                continue
            mid = (np.add(np.add(c[i], c[j]), c[k])) / 3.0
            if float(np.dot(mid - centre, t.normal)) < 0:
                t = Tri.from_verts(c[i], c[k], c[j], flags)
            out.append(t)
    return out


def flags_of(op: O.Op, base: TriFlags | None = None) -> TriFlags:
    """An op's `flags` over `base`: the fields it names change, the others stay."""
    fl = op.get("flags", {})
    if isinstance(fl, int):
        return TriFlags.from_word(fl)
    base = base or TriFlags()
    return TriFlags(
        fl.get("surface", base.surface_id),
        fl.get("material", base.material),
        fl.get("exclude", base.exclude),
    )


def plan(stage: StageFile, ops: Sequence[O.Op], base_dir: Path, pad: float = 0.0) -> Plan:
    """What the list does to the stage's collision, op by op; later ops see earlier ones."""
    return _Planner(stage, base_dir, pad).run(ops)


def _corners_inside(v: np.ndarray, kind: str, vol: Sequence[float]) -> np.ndarray:
    """Per triangle of `v` (n, 3, 3), how many corners lie in the sphere or box."""
    x, y, z = v[..., 0], v[..., 1], v[..., 2]
    if kind == "sphere":
        cx, cy, cz, r = vol
        inside = (x - cx) ** 2 + (y - cy) ** 2 + (z - cz) ** 2 <= r * r
    else:
        x0, y0, z0, x1, y1, z1 = vol
        inside = (x0 <= x) & (x <= x1) & (y0 <= y) & (y <= y1) & (z0 <= z) & (z <= z1)
    counts: np.ndarray = inside.sum(axis=1)
    return counts


def _volume(op: O.Op) -> tuple[str, Sequence[float]] | None:
    """The collision selection: the op's `collision` sphere or box, else its own."""
    for source in (op.get("collision") or {}, op):
        for kind in ("sphere", "box"):
            if kind in source:
                return kind, source[kind]
    return None


def _coords(tris: Sequence[Tri]) -> np.ndarray:
    return np.asarray([(t.v0, t.v1, t.v2) for t in tris], np.float64).reshape(-1, 3, 3)


def _bbox(points: Any) -> tuple[list[float], list[float]] | None:
    if not len(points):
        return None
    p = np.asarray(points, np.float64)
    return p.min(axis=0).tolist(), p.max(axis=0).tolist()


class _Planner:
    def __init__(self, stage: StageFile, base_dir: Path, pad: float) -> None:
        self.stage, self.base_dir, self.pad = stage, base_dir, pad
        self.out = Plan(stage.number, stage.stage.collision or Collision())
        self.chunks = self.out.chunks
        self.shipped = [len(c.tris) for c in self.chunks]
        self.live: dict[tuple[int, int], Tri] = {
            (ci, t): tri for ci, c in enumerate(self.chunks) for t, tri in enumerate(c.tris)
        }
        self.owner: list[dict[int, set[int]]] = []
        for c in self.chunks:
            owner: dict[int, set[int]] = {}
            for cell, run in enumerate(c.cells):
                for t in run:
                    owner.setdefault(t, set()).add(cell)
            self.owner.append(owner)
        self.added_at: dict[tuple[int, int], int] = {}
        self.coords = [_coords(c.tris) for c in self.chunks]
        """Per chunk, the corners of every triangle as it is now, row = triangle number."""
        self.dead = [np.zeros(len(c.tris), bool) for c in self.chunks]
        self.i = 0

    def run(self, ops: Sequence[O.Op]) -> Plan:
        for i, op in enumerate(ops):
            self.i = i
            if not O.touches_collision(op):
                continue
            if not self.chunks:
                self.say("error", "no-collision", f"{self.stage.label} has no collision")
                break
            kind = op["op"]
            try:
                if kind == "collision":
                    self.collision(op)
                elif kind == "move":
                    self.move(op)
                elif kind == "transform":
                    self.transform(op)
                elif kind in ("pack", "sculpt", "replace"):
                    self.from_obj(op)
                elif kind == "clear":
                    self.clear(op)
            except (ValueError, OSError) as e:
                self.say("error", "refused", f"{kind}: {e}")
        long = [
            (ci, cell, len(r))
            for ci, cells in self.out.cells.items()
            for cell, r in cells.items()
            if len(r) > LIST_CAP
        ]
        if long:
            self.say(
                "warning",
                "long-cell-list",
                f"{len(long)} cell lists pass the retail {LIST_CAP} entries: {long[:4]}",
            )
        return self.out

    def say(self, level: Level, code: str, message: str) -> None:
        where, target = f"{self.stage.label} op {self.i}", (self.stage.number, self.i)
        self.out.findings.append(Finding(level, code, message, where, target))

    def log(self, line: str) -> None:
        self.out.log.append(f"{self.stage.label} op {self.i}: {line}")

    # cell bookkeeping

    def cell(self, ci: int, cell: int) -> list[Ref]:
        cells = self.out.cells.setdefault(ci, {})
        if cell not in cells:
            cells[cell] = [Ref(False, t) for t in self.chunks[ci].cells[cell]]
        return cells[cell]

    def ref(self, ci: int, t: int) -> Ref:
        return Ref(True, self.added_at[(ci, t)]) if t >= self.shipped[ci] else Ref(False, t)

    def cells_of(self, ci: int, t: int) -> set[int]:
        """Where the triangle is listed now."""
        r = self.ref(ci, t)
        changed = self.out.cells.get(ci, {})
        now = {cell for cell, refs in changed.items() if r in refs}
        if not r.added:
            now |= {cell for cell in self.owner[ci].get(t, ()) if cell not in changed}
        return now

    def overlap(self, ci: int, tri: Tri) -> set[int]:
        c = self.chunks[ci]
        return set(cells_for_tri(tri, c.grid, c.cell, c.origin, self.pad))

    def relink(self, ci: int, t: int, now: set[int]) -> None:
        r, was = self.ref(ci, t), self.cells_of(ci, t)
        for cell in was - now:
            self.cell(ci, cell).remove(r)
        for cell in sorted(now - was):
            self.cell(ci, cell).append(r)

    def sort(self, op: O.Op, tri: Tri) -> int:
        ci = op.get("chunk")
        if ci is None:
            ci = FLOOR if abs(tri.normal[1]) > 0.5 else 0
        return ci if 0 <= ci < len(self.chunks) else 0

    def add(self, ci: int, tri: Tri) -> int:
        k = len(self.out.added)
        t = self.shipped[ci] + sum(c == ci for c, _ in self.out.added)
        self.out.added.append((ci, tri))
        self.added_at[(ci, t)] = k
        self.live[(ci, t)] = tri
        self.coords[ci] = np.concatenate([self.coords[ci], _coords([tri])])
        self.dead[ci] = np.append(self.dead[ci], False)
        for cell in sorted(self.overlap(ci, tri)):
            self.cell(ci, cell).append(Ref(True, k))
        return t

    def set(self, ci: int, t: int, tri: Tri) -> None:
        self.live[(ci, t)] = tri
        self.coords[ci][t] = (tri.v0, tri.v1, tri.v2)
        if t >= self.shipped[ci]:
            self.out.added[self.added_at[(ci, t)]] = (ci, tri)
        else:
            self.out.moved = [m for m in self.out.moved if (m[0], m[1]) != (ci, t)]
            self.out.moved.append((ci, t, tri))
        self.relink(ci, t, self.overlap(ci, tri))

    def unlink(self, ci: int, t: int) -> None:
        self.relink(ci, t, set())
        self.out.deleted.append((ci, t))
        self.dead[ci][t] = True

    def within(
        self, op: O.Op, kind: str, vol: Sequence[float]
    ) -> tuple[list[tuple[int, int, Tri]], int]:
        """The live triangles of the chunks the op names (all when it names none) wholly inside
        the volume, in the order the list knows them, and how many it cuts."""
        which = (op.get("collision") or {}).get("chunks", op.get("chunks"))
        whole: list[tuple[int, int]] = []
        cut = 0
        for ci, v in enumerate(self.coords):
            if which is not None and ci not in which:
                continue
            n = _corners_inside(v, kind, vol)
            live = ~self.dead[ci]
            cut += int((live & (n > 0) & (n < 3)).sum())
            whole += [(ci, int(t)) for t in np.flatnonzero(live & (n == 3))]
        whole.sort(key=self.order)
        return [(ci, t, self.live[(ci, t)]) for ci, t in whole], cut

    def order(self, key: tuple[int, int]) -> tuple[int, int, int]:
        """Shipped triangles chunk by chunk, then added ones as they were added."""
        ci, t = key
        return (1, self.added_at[key], 0) if t >= self.shipped[ci] else (0, ci, t)

    def add_box(self, op: O.Op, lo: Sequence[float], hi: Sequence[float]) -> None:
        for tri in box_tris(lo, hi, flags_of(op), op.get("inflate", 0.0)):
            self.add(self.sort(op, tri), tri)
        size = " x ".join(f"{hi[k] - lo[k]:.0f}" for k in range(3))
        self.log(f"collision box {size}")

    # the ops

    def collision(self, op: O.Op) -> None:
        n_set = n_del = n_add = 0
        if "solid_box" in op:
            box = op["solid_box"]
            self.add_box(op, box[:3], box[3:])
            n_add += 12
        for corners in op.get("add", []):
            tri = self._tri(corners, flags_of(op))
            if tri is not None:
                self.add(self.sort(op, tri), tri)
                n_add += 1
        ci = op.get("chunk", FLOOR)
        for t in ([op["tri"]] if "tri" in op else []) + list(op.get("tris", [])):
            if ci is None or (ci, t) not in self.live:
                self.say("error", "no-triangle", f"chunk {ci} has no triangle {t}")
                continue
            if op.get("delete"):
                self.unlink(ci, t)
                n_del += 1
                continue
            old = self.live[(ci, t)]
            tri = self._tri(op.get("verts") or (old.v0, old.v1, old.v2), flags_of(op, old.flags))
            if tri is not None:
                self.set(ci, t, tri)
                n_set += 1
        self.log(f"collision {n_set} set, {n_del} deleted, {n_add} added")

    def _tri(self, corners: Any, flags: TriFlags) -> Tri | None:
        """A record over three corners (any 3 x 3 of numbers); None, said, when degenerate."""
        a, b, c = ((float(v[0]), float(v[1]), float(v[2])) for v in corners)
        try:
            return Tri.from_verts(a, b, c, flags)
        except ValueError as e:
            self.say("warning", "degenerate", str(e))
            return None

    def move(self, op: O.Op) -> None:
        vol = _volume(op)
        if vol is None:
            return
        by = np.asarray(op["by"], np.float64)
        n = 0
        whole, cut = self.within(op, *vol)
        for ci, t, tri in whole:
            if new := self._tri(np.add([tri.v0, tri.v1, tri.v2], by), tri.flags):
                self.set(ci, t, new)
                n += 1
        if op.get("solid"):
            box = _bbox(self.visual(op))
            if box:
                self.add_box(
                    op, (np.asarray(box[0]) + by).tolist(), (np.asarray(box[1]) + by).tolist()
                )
        self.log(f"collision move {n} triangles by {op['by']}, {cut} straddling (left alone)")

    def transform(self, op: O.Op) -> None:
        m = transform_matrix(op)
        pts = self.visual(op)
        box = _bbox(pts)
        if box is None:
            return
        margin = op.get("margin", 2.0)
        vol = [*(v - margin for v in box[0]), *(v + margin for v in box[1])]
        n = 0
        whole, cut = self.within(op, "box", vol)
        for ci, t, tri in whole:
            new = self._tri(
                np.asarray([tri.v0, tri.v1, tri.v2]) @ m[:3, :3].T + m[:3, 3], tri.flags
            )
            if new:
                self.set(ci, t, new)
                n += 1
        if op.get("solid"):
            self.add_box(op, *_bbox(np.asarray(pts) @ m[:3, :3].T + m[:3, 3]) or ([], []))
        self.log(f"collision transform {n} triangles, {cut} straddling (left alone)")

    def visual(self, op: O.Op) -> list[Vec3]:
        """The shipped positions of the visible vertices the op selects."""
        g = op.get("group")
        if g is None:
            return []
        positions = self.stage.positions(op.get("sub", 0), g)
        return [positions[i] for i in select(op, positions)]

    def from_obj(self, op: O.Op) -> None:
        name = op.get("collision_obj") or op["obj"]
        mesh = read_obj(self.base_dir / name)
        corners = mesh.corners()
        if op["solid"] == "box":
            # the broadphase needs no silhouette; a box keeps the cell lists retail-short
            box = _bbox([p for c in corners for p in c])
            if box:
                self.add_box(op, *box)
            return
        n = 0
        for c in corners:
            tri = self._tri(c, flags_of(op))
            if tri is not None:
                self.add(self.sort(op, tri), tri)
                n += 1
        self.log(f"collision +{n} triangles from {name}")

    def clear(self, op: O.Op) -> None:
        if not op.get("solid"):
            return
        vol = _volume(op)
        whole = self.within(op, *vol)[0] if vol else []
        for ci, t, _ in whole:
            self.unlink(ci, t)
        self.log(f"collision clear {len(whole)} triangles unlinked")


def serialise(p: Plan) -> dict[str, Any]:
    """The plan as JSON: what an exported map carries for a loader to apply."""

    def tri(t: Tri) -> dict[str, Any]:
        return {
            "flags": t.flags.word,
            "v0": list(t.v0),
            "v1": list(t.v1),
            "v2": list(t.v2),
            "normal": list(t.normal),
            "plane_d": t.plane_d,
        }

    return {
        "moved": [{"chunk": c, "tri": t, "record": tri(r)} for c, t, r in p.moved],
        "added": [{"chunk": c, "record": tri(r)} for c, r in p.added],
        "deleted": [{"chunk": c, "tri": t} for c, t in p.deleted],
        "cells": {
            str(c): {
                str(cell): [[int(r.added), r.number] for r in refs] for cell, refs in cells.items()
            }
            for c, cells in p.cells.items()
        },
    }
