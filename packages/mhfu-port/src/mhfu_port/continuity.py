# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Which clip fits after which: how well clip B's first pose continues clip A's last.

A pose is each animated joint's local rotation as the engine's FK sees it (`fk`), with the
body's heading about y taken out (YAW owns it, `travel`); the root's travel is location, which
no rotation carries. Two poses are the mean angle between their joints, in degrees: smaller fits
better. It ranks poses, not intent: a clip that hands to the next through a blend, or through a
third clip, scores high.

A channel holds past its last key, so a clip's first and last poses are its first and last keys'
values: ranking a moveset takes two poses per clip, once (`Ends`).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping

import numpy as np
from mhp_formats.anim import Clip
from mhp_formats.skeleton import Skeleton

from . import build, fk, travel
from .data import Data
from .manifest import Manifest
from .motion import SOURCE_BANK

GOOD, FAIR = 10.0, 20.0
"""Degrees. An eighth to a fifth of a donor's clip pairs lie within GOOD (clips that start and
end on the same stance); the Zinogre's backflip parts meet at 14 to 15; past FAIR the poses do
not meet."""

Scores = list[tuple[int, float]]


def word(score: float) -> str:
    """`good`, `fair` or `poor`."""
    return "good" if score <= GOOD else "fair" if score <= FAIR else "poor"


class Ends:
    """The first and last pose of each clip: `(clips, joints, 3, 3)` rotations, the joints being
    those some pose turns."""

    def __init__(self, ids: tuple[int, ...], first: fk.Floats, last: fk.Floats) -> None:
        self.ids, self.first, self.last = ids, first, last

    def scores(self, a: int) -> fk.Floats:
        """Degrees from the end of clip `a` to the start of each clip, in `ids` order."""
        if a not in self.ids:
            raise KeyError(f"no clip {a}")
        if not self.first.shape[1]:
            return np.zeros(len(self.ids))
        trace = np.einsum("jik,njik->nj", self.last[self.ids.index(a)], self.first)
        angle: fk.Floats = np.degrees(np.arccos(np.clip((trace - 1) / 2, -1, 1)))
        mean: fk.Floats = angle.mean(axis=1)
        return mean


def ends(
    clips: Mapping[int, Clip | fk.Curves],
    skeleton: Skeleton,
    record_of: Mapping[int, int] | None = None,
) -> Ends:
    """The end poses of `clips` (by id) on `skeleton`. `record_of` maps a donor's bones to its
    clips' tracks (`build.record_map`); without it, track i drives joint i."""
    rig = fk.Rig.from_skeleton(skeleton)
    ids = tuple(sorted(clips))
    rot, loc = [], []
    for i in ids:
        c = clips[i]
        curves = c if isinstance(c, fk.Curves) else fk.Curves(c, rig, record_of)
        r, t = curves.at(np.array([0.0, float(curves.last_frame)]))
        rot.append(r)
        loc.append(t)
    if not ids:
        return Ends(ids, np.zeros((0, 0, 3, 3)), np.zeros((0, 0, 3, 3)))
    rotations, locations = np.stack(rot), np.stack(loc)
    pose = fk.euler_xyz(rotations)
    body = np.flatnonzero(rig.parents == travel.root(skeleton))
    pose[..., body, :, :] = _unturned(rig.world(rotations, locations)[..., body, :3, :3])
    moving = np.flatnonzero(np.abs(pose - np.eye(3)).max(axis=(0, 1, 3, 4)) > 1e-9)
    return Ends(ids, pose[:, 0, moving], pose[:, 1, moving])


def _unturned(body: fk.Floats) -> fk.Floats:
    """`(..., body, 3, 3)` rotations with the heading about y they share taken out."""
    heading = np.arctan2(
        (body[..., 0, 2] - body[..., 2, 0]).sum(axis=-1),
        (body[..., 0, 0] + body[..., 2, 2]).sum(axis=-1),
    )
    c, s = np.cos(heading), np.sin(heading)
    back = np.zeros((*heading.shape, 3, 3))  # a turn of -heading about y
    back[..., 0, 0] = back[..., 2, 2] = c
    back[..., 0, 2], back[..., 2, 0], back[..., 1, 1] = -s, s, 1.0
    out: fk.Floats = back[..., None, :, :] @ body
    return out


def donor_ends(m: Manifest, data: Data) -> Ends:
    """The end poses of the manifest's donor moveset, on the donor's own skeleton."""
    d = build.donor(m, data)
    return ends(d.clips, d.skeleton, build.record_map(d, m.build))


def fits_after(e: Ends, a: int, *, top: int | None = None, itself: bool = False) -> Scores:
    """Every clip but `a` (unless `itself`) as `(id, degrees)`, best first: how far its first
    pose is from the last of `a`. Equal scores go nearest the id of `a` first. `top` keeps that
    many."""
    rows = [(i, float(s)) for i, s in zip(e.ids, e.scores(a), strict=True) if itself or i != a]
    rows.sort(key=lambda r: (round(r[1], 3), abs(r[0] - a), r[0]))
    return rows if top is None else rows[:top]


def neighbours(a: int, ids: Collection[int]) -> list[int]:
    """`ids` but `a`, nearest first: its own stream by slot number, then the other streams by
    how far off they are."""
    stream, slot = divmod(a, SOURCE_BANK)

    def apart(i: int) -> tuple[int, int, int]:
        return abs(i // SOURCE_BANK - stream), abs(i % SOURCE_BANK - slot), i

    return sorted((i for i in ids if i != a), key=apart)
