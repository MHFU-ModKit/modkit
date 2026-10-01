# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Checks across the game's stages that need their collision: where each exit lands, the
surface classes the triangles use, and the writers' self-checks."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import NamedTuple

import numpy as np
from mhfu import files
from mhfu import stage as S
from mhfu.files import Extracted
from mhp_formats.fu.stage import Hits, Stage
from mhp_formats.pmo import Pmo

from .collision import FLOOR
from .file import MESHES, StageFile

LANDING_TOLERANCE = 60.0
"""How far from a floor an exit's destination may be and still stand on it."""

Vec3 = tuple[float, float, float]


class Landing(NamedTuple):
    stage: int
    target: int
    dest: Vec3
    floor: float | None
    """The target's floor under the destination nearest its height; None for no floor."""
    fault: str
    """`mhfu.stage.exit_fault`'s, else why it does not land; "" when it does."""

    @property
    def lands(self) -> bool:
        return not self.fault


class _Floor:
    """A chunk's triangles as arrays, for many height queries."""

    def __init__(self, hits: Hits) -> None:
        tris = [t for t in hits.tris if abs(t.normal[1]) >= 1e-6]
        self.v = np.asarray([(t.v0, t.v1, t.v2) for t in tris], np.float64).reshape(-1, 3, 3)
        self.n = np.asarray([t.normal for t in tris], np.float64).reshape(-1, 3)
        self.d = np.asarray([t.plane_d for t in tris], np.float64)

    def heights(self, x: float, z: float) -> np.ndarray:
        """Every floor height under (x, z): the triangles whose xz projection holds it."""
        a, b, c = self.v[:, 0], self.v[:, 1], self.v[:, 2]

        def side(p: np.ndarray, q: np.ndarray) -> np.ndarray:
            out: np.ndarray = (x - q[:, 0]) * (p[:, 2] - q[:, 2]) - (p[:, 0] - q[:, 0]) * (
                z - q[:, 2]
            )
            return out

        s = np.stack([side(a, b), side(b, c), side(c, a)])
        inside = ~((s < 0).any(axis=0) & (s > 0).any(axis=0))
        n, d = self.n[inside], self.d[inside]
        ys: np.ndarray = -(n[:, 0] * x + n[:, 2] * z + d) / n[:, 1]
        return ys


def floor_at(hits: Hits, x: float, z: float, near: float) -> float | None:
    """The floor under (x, z) nearest height `near`, or None."""
    ys = _Floor(hits).heights(x, z)
    return float(ys[np.argmin(np.abs(ys - near))]) if len(ys) else None


def landings(game: Extracted, stages: Iterable[int] = files.STAGES) -> list[Landing]:
    """Every exit of `stages`, its destination checked against the target's floor chunk."""
    table = S.read_map_table(game)
    floors: dict[int, _Floor | None] = {}
    out = []
    for n in stages:
        for e in S.StageOverlay.read(game, n).exits():
            dest = e.dest
            fault = S.exit_fault(n, e.target, table)
            y = None
            if not fault:
                if e.target not in floors:
                    chunks = StageFile.read(game, e.target).chunks
                    floors[e.target] = _Floor(chunks[FLOOR]) if len(chunks) > FLOOR else None
                floor = floors[e.target]
                ys = floor.heights(dest[0], dest[2]) if floor else np.zeros(0)
                if len(ys):
                    y = float(ys[np.argmin(np.abs(ys - dest[1]))])
                    if abs(y - dest[1]) > LANDING_TOLERANCE:
                        fault = f"the floor there is {y:.0f}, {y - dest[1]:+.0f} off"
                else:
                    fault = "no floor under the destination"
            out.append(Landing(n, e.target, dest, y, fault))
    return out


@dataclass
class Census:
    triangles: int = 0
    stages: set[int] = field(default_factory=set)


def surface_ids(stage: Stage) -> Counter[tuple[int, int]]:
    """Triangles per (chunk, surface id)."""
    out: Counter[tuple[int, int]] = Counter()
    for ci, hits in enumerate(stage.collision.chunks if stage.collision else []):
        out.update((ci, t.flags.surface_id) for t in hits.tris)
    return out


def census(game: Extracted, stages: Iterable[int] = files.STAGES[1:]) -> dict[int | None, Census]:
    """Triangles and stages per surface mask (None: an id past the overlay's table)."""
    out: dict[int | None, Census] = {}
    for n in stages:
        table = S.StageOverlay.read(game, n).surface_table()
        st = StageFile.read(game, n).stage
        if table is None or st.collision is None:
            continue
        for (_, sid), count in surface_ids(st).items():
            entry = out.setdefault(table[sid] if sid < len(table) else None, Census())
            entry.triangles += count
            entry.stages.add(n)
    return out


class Planes(NamedTuple):
    stages: int
    failing: list[tuple[int, float]]
    """(stage, fraction of triangles whose plane checks out) under `PLANE_FLOOR`."""


PLANE_FLOOR = 0.98


def planes(game: Extracted, stages: Iterable[int] = files.STAGES[1:]) -> Planes:
    """The plane self-check (unit normal, v0 on the plane) of every stage with collision."""
    n, failing = 0, []
    for k in stages:
        st = StageFile.read(game, k).stage
        if st.collision is None:
            continue
        n += 1
        frac = st.verify().frac
        if frac < PLANE_FLOOR:
            failing.append((k, frac))
    return Planes(n, failing)


def inplace(game: Extracted, stages: Iterable[int] = files.STAGES[1:]) -> tuple[int, list[str]]:
    """Every stage PMO laid out over itself unedited, which the mesh writer relies on giving
    back the shipped bytes. Returns (PMOs checked, the ones that do not)."""
    n, bad = 0, []
    for k in stages:
        sf = StageFile.read(game, k)
        for sub in MESHES:
            data = sf.entry(sub)
            if not Pmo.sniff(data):
                continue
            n += 1
            if Pmo.from_bytes(data).to_bytes_inplace(data) != (data, []):
                bad.append(f"{sf.label} sub {sub}")
    return n, bad
