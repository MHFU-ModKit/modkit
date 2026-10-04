# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The engine's forward kinematics: where each joint of a skeleton sits, and where it takes the
vertices it carries.

A joint's local transform is its channels' rotation, Euler XYZ composed as Rz·Ry·Rx, absolute
(bind rotations are zero on both games' monsters), and its channels' location, each axis the
clip does not drive falling back to the bind offset. `world = parent_world @ local`; scale
channels are ignored (MHFU crashes on them). Channels interpolate as the engine's cubic `spline`
between keys, the left key's `ease_out` and the right key's `ease_in` the slopes in raw units per
frame, and hold outside their own first and last key. The bind pose is pure translation, so its
inverse is too. Arrays take any leading batch shape: `(..., joints, 3)` in, `(..., joints, 4, 4)`
out.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
from mhp_formats.anim import AnimPack, Clip, Track, channel_kind, dequantize
from mhp_formats.skeleton import Skeleton, Vec3
from numpy.typing import ArrayLike, NDArray

Floats = NDArray[np.float64]

WEIGHT_EPS = 1e-4
"""Influences at or below this weight are dropped before normalising: a palette pads unused
slots with weight 0, and a 0-weight slot still names a bone."""

FU_PART_STREAM = 2
"""MHFU keeps the clips of `Bone.stream` k in animation stream `FU_PART_STREAM * k`."""

_KINDS = ("rot", "loc")


def bind_world(parents: Sequence[int], local: Sequence[Vec3]) -> list[Vec3]:
    """Each joint's bind position in model space. Bind rotations are zero on both games'
    monsters, so this is the sum of the offsets up the chain; a joint in a parent cycle counts
    as a root."""
    out: list[Vec3] = [(0.0, 0.0, 0.0)] * len(parents)
    effective = effective_parents(parents)
    for level in _levels(effective):
        for i in level:
            x, y, z = local[i]
            p = effective[i]
            if p >= 0:
                px, py, pz = out[p]
                x, y, z = px + x, py + y, pz + z
            out[i] = (x, y, z)
    return out


def effective_parents(parents: Sequence[int]) -> NDArray[np.intp]:
    """The parents the FK walks: -1 for a root, for a parent outside the skeleton, and for the
    joint that closes a cycle when the joints are resolved in order."""
    n = len(parents)
    out: list[int | None] = [None] * n
    for i in range(n):
        chain: list[int] = []
        j = i
        while out[j] is None:
            chain.append(j)
            p = parents[j]
            if not 0 <= p < n or p in chain:
                out[j] = -1
                chain.pop()
                break
            j = p
        for k in chain:
            out[k] = parents[k]
    return np.array(out, dtype=np.intp)


def _levels(parents: NDArray[np.intp]) -> list[NDArray[np.intp]]:
    """Joints by depth, roots first; `parents` must be a forest."""
    depth = np.where(parents < 0, 0, -1)
    while (todo := depth < 0).any():
        ready = todo & (depth[parents] >= 0)
        if not ready.any():
            raise ValueError("the parents have a cycle")
        depth[ready] = depth[parents[ready]] + 1
    return [np.flatnonzero(depth == d) for d in range(int(depth.max(initial=-1)) + 1)]


def euler_xyz(rot: ArrayLike) -> Floats:
    """`(..., 3)` radians to `(..., 3, 3)` rotations, Rz·Ry·Rx."""
    r = np.asarray(rot, dtype=np.float64)
    cx, cy, cz = np.cos(r[..., 0]), np.cos(r[..., 1]), np.cos(r[..., 2])
    sx, sy, sz = np.sin(r[..., 0]), np.sin(r[..., 1]), np.sin(r[..., 2])
    m = np.empty((*r.shape[:-1], 3, 3))
    m[..., 0, 0] = cy * cz
    m[..., 0, 1] = cz * sx * sy - cx * sz
    m[..., 0, 2] = cx * cz * sy + sx * sz
    m[..., 1, 0] = cy * sz
    m[..., 1, 1] = cx * cz + sx * sy * sz
    m[..., 1, 2] = -cz * sx + cx * sy * sz
    m[..., 2, 0] = -sy
    m[..., 2, 1] = cy * sx
    m[..., 2, 2] = cy * cx
    return m


class Rig:
    """A skeleton arranged for the FK."""

    def __init__(self, parents: Sequence[int], bind_local: ArrayLike) -> None:
        self.parents = effective_parents(parents)
        self.bind_local: Floats = np.array(bind_local, dtype=np.float64).reshape(-1, 3)
        if len(self.bind_local) != len(self.parents):
            raise ValueError(f"{len(self.parents)} parents, {len(self.bind_local)} offsets")
        self.levels = _levels(self.parents)
        self.bind_world = self.world()
        self.bind_inverse = np.tile(np.eye(4), (self.n, 1, 1))
        self.bind_inverse[:, :3, 3] = -self.bind_world[:, :3, 3]

    @classmethod
    def from_skeleton(cls, skeleton: Skeleton) -> Rig:
        return cls([b.parent for b in skeleton.bones], [b.position for b in skeleton.bones])

    @property
    def n(self) -> int:
        return len(self.parents)

    @property
    def bind_joints(self) -> Floats:
        """`(joints, 3)` bind positions in model space."""
        return self.bind_world[:, :3, 3]

    def world(self, rot: ArrayLike | None = None, loc: ArrayLike | None = None) -> Floats:
        """World matrices; a missing rotation is zero, a missing location the bind offset."""
        r = np.zeros((self.n, 3)) if rot is None else np.asarray(rot, dtype=np.float64)
        t = self.bind_local if loc is None else np.asarray(loc, dtype=np.float64)
        r, t = np.broadcast_arrays(r, t)
        local = np.zeros((*r.shape[:-1], 4, 4))
        local[..., :3, :3] = euler_xyz(r)
        local[..., :3, 3] = t
        local[..., 3, 3] = 1.0
        world = np.empty_like(local)
        world[..., self.levels[0], :, :] = local[..., self.levels[0], :, :]
        for level in self.levels[1:]:
            world[..., level, :, :] = (
                world[..., self.parents[level], :, :] @ local[..., level, :, :]
            )
        return world

    def deform(self, world: ArrayLike) -> Floats:
        """`world @ bind⁻¹`: what a vertex's influences blend."""
        return np.asarray(world, dtype=np.float64) @ self.bind_inverse


def spline(
    t: ArrayLike,
    t0: ArrayLike,
    x0: ArrayLike,
    m0: ArrayLike,
    t1: ArrayLike,
    x1: ArrayLike,
    m1: ArrayLike,
) -> Floats:
    """The cubic through `(t0, x0)` and `(t1, x1)` with slopes `m0` and `m1` there; `x0` where
    `t1 <= t0`."""
    t, t0, x0, m0, t1, x1, m1 = (
        np.asarray(a, dtype=np.float64) for a in (t, t0, x0, m0, t1, x1, m1)
    )
    span = t1 - t0
    s = np.where(span > 0, (t - t0) / np.where(span > 0, span, 1.0), 0.0)
    d = s * span
    out: Floats = (
        x0 * (1 - 3 * s**2 + 2 * s**3)
        + x1 * (3 * s**2 - 2 * s**3)
        + m0 * d * (1 - s) ** 2
        + m1 * d * (s**2 - s)
    )
    return out


class Curves:
    """A clip's rotation and location channels, per joint, ready to sample at any frame.

    `tracks` maps joint to track; without it track i drives joint i (MHFU's clips; an MHP3rd
    moveset needs the map). A channel whose track maps to no joint of the rig is dropped.
    """

    def __init__(self, clip: Clip, rig: Rig, tracks: Mapping[int, int] | None = None) -> None:
        self.bind_local = rig.bind_local
        track_of = {j: j for j in range(min(rig.n, len(clip.tracks)))} if tracks is None else tracks
        rows: dict[tuple[int, int, int], list[tuple[float, ...]]] = {}
        for joint, t in sorted(track_of.items()):
            if not (0 <= joint < rig.n and 0 <= t < len(clip.tracks)):
                continue
            for channel in clip.tracks[t].channels:
                named = channel_kind(channel.bit)
                if named is None or named[0] not in _KINDS or not channel.keyframes:
                    continue
                kind, axis = named
                rows[_KINDS.index(kind), joint, axis] = sorted(
                    (
                        (k.frame, *(dequantize(kind, x) for x in (k.value, k.ease_in, k.ease_out)))
                        for k in channel.keyframes
                    ),
                    key=lambda key: key[0],
                )
        self._rows = np.array(list(rows), dtype=np.intp).reshape(-1, 3)
        width = max(map(len, rows.values()), default=1)
        self._frames = np.full((len(rows), width), np.inf)
        self._values = np.zeros((len(rows), width))
        self._ease_in = np.zeros((len(rows), width))
        self._ease_out = np.zeros((len(rows), width))
        self._lens = np.array([len(k) for k in rows.values()], dtype=np.intp)
        for i, keys in enumerate(rows.values()):
            frame, value, ease_in, ease_out = zip(*keys, strict=True)
            self._frames[i, : len(keys)] = frame
            self._values[i, : len(keys)] = value
            self._ease_in[i, : len(keys)] = ease_in
            self._ease_out[i, : len(keys)] = ease_out
        self.driven: tuple[int, ...] = tuple(sorted({j for _, j, _ in rows}))
        """Joints with at least one channel."""
        finite = self._frames[np.isfinite(self._frames)]
        self.last_frame = int(finite.max()) if finite.size else 0

    def keys(self) -> Floats:
        """Every frame some channel keys, and 0, sorted."""
        return np.union1d(self._frames[np.isfinite(self._frames)], [0.0])

    def at(self, frame: ArrayLike) -> tuple[Floats, Floats]:
        """`(rot, loc)`, each `(*frame.shape, joints, 3)`; frames may be fractional."""
        f = np.asarray(frame, dtype=np.float64)
        flat = f.reshape(-1, 1, 1)
        n = len(self.bind_local)
        rot = np.zeros((len(flat), n, 3))
        loc = np.broadcast_to(self.bind_local, (len(flat), n, 3)).copy()
        if len(self._lens):
            hi = (self._frames < flat).sum(axis=-1)
            lo = np.maximum(hi - 1, 0)
            hi = np.minimum(hi, self._lens - 1)
            row = np.arange(len(self._lens))
            value = spline(
                flat[..., 0],
                self._frames[row, lo],
                self._values[row, lo],
                self._ease_out[row, lo],
                self._frames[row, hi],
                self._values[row, hi],
                self._ease_in[row, hi],
            )
            kind, joint, axis = self._rows.T
            rot[:, joint[kind == 0], axis[kind == 0]] = value[:, kind == 0]
            loc[:, joint[kind == 1], axis[kind == 1]] = value[:, kind == 1]
        shape = (*f.shape, n, 3)
        return rot.reshape(shape), loc.reshape(shape)


def world_matrices(
    rig: Rig,
    clip: Clip | None = None,
    frame: ArrayLike = 0,
    tracks: Mapping[int, int] | None = None,
) -> Floats:
    """World matrices at `frame`; the bind pose without a clip."""
    if clip is None:
        return rig.bind_world.copy()
    return rig.world(*Curves(clip, rig, tracks).at(frame))


def deform_matrices(
    rig: Rig,
    clip: Clip | None = None,
    frame: ArrayLike = 0,
    tracks: Mapping[int, int] | None = None,
) -> Floats:
    """`world @ bind⁻¹` at `frame`."""
    return rig.deform(world_matrices(rig, clip, frame, tracks))


class Skin:
    """Vertices and the joints that carry them, weights normalised; a vertex with no influence
    left stays where it is."""

    def __init__(
        self,
        positions: ArrayLike,
        influences: Sequence[Sequence[tuple[int, float]]],
        joints: int,
    ) -> None:
        self.positions: Floats = np.array(positions, dtype=np.float64).reshape(-1, 3)
        if len(influences) != len(self.positions):
            raise ValueError(f"{len(self.positions)} vertices, {len(influences)} influence lists")
        kept = [[(b, w) for b, w in vi if w > WEIGHT_EPS and 0 <= b < joints] for vi in influences]
        width = max(map(len, kept), default=1) or 1
        self.joints = np.zeros((len(kept), width), dtype=np.intp)
        self.weights: Floats = np.zeros((len(kept), width))
        for v, vi in enumerate(kept):
            total = sum(w for _, w in vi)
            for k, (b, w) in enumerate(vi):
                self.joints[v, k] = b
                self.weights[v, k] = w / total
        self.rest: Floats = 1.0 - self.weights.sum(axis=1)
        """1 for a vertex that stays put, else 0 (up to rounding)."""

    def dominant(self) -> NDArray[np.intp]:
        """The heaviest joint of each vertex, -1 where none carries it."""
        heaviest = self.joints[np.arange(len(self.joints)), self.weights.argmax(axis=1)]
        return np.where(self.rest > 0.5, -1, heaviest)

    def apply(self, deform: ArrayLike) -> Floats:
        """Positions under `(..., joints, 4, 4)` deform matrices: `(..., vertices, 3)`."""
        d = np.asarray(deform, dtype=np.float64)
        blended: Floats = np.einsum("vk,...vkij->...vij", self.weights, d[..., self.joints, :3, :])
        out: Floats = np.einsum("...vij,vj->...vi", blended[..., :3], self.positions)
        return out + blended[..., 3] + self.positions * self.rest[:, None]

    def apply_normals(self, deform: ArrayLike, normals: ArrayLike) -> Floats:
        """Directions under the same blend, rotation only, renormalised."""
        d = np.asarray(deform, dtype=np.float64)
        n = np.asarray(normals, dtype=np.float64).reshape(-1, 3)
        if len(n) != len(self.positions):
            raise ValueError(f"{len(self.positions)} vertices, {len(n)} normals")
        blended: Floats = np.einsum("vk,...vkij->...vij", self.weights, d[..., self.joints, :3, :3])
        out: Floats = np.einsum("...vij,vj->...vi", blended, n) + n * self.rest[:, None]
        length = np.linalg.norm(out, axis=-1, keepdims=True)
        unit: Floats = np.divide(out, length, out=np.zeros_like(out), where=length > 1e-12)
        return unit


def part_joints(streams: Sequence[int]) -> dict[int, list[int]]:
    """Each skeleton part's joints, `streams[j]` being joint j's part (`Bone.stream`): part k's
    clips hold one track per joint of `part_joints(...)[k]`, in that order."""
    out: dict[int, list[int]] = {}
    for joint, part in enumerate(streams):
        out.setdefault(part, []).append(joint)
    return out


def part_clip(anim: AnimPack, part: int, slot: int) -> Clip | None:
    """The clip skeleton part `part` plays in `slot`; None where it has none."""
    s = FU_PART_STREAM * part
    stream = anim.streams[s] if 0 <= s < len(anim.streams) else []
    return stream[slot] if 0 <= slot < len(stream) else None


def rig_clip(anim: AnimPack, slot: int, skeleton: Skeleton) -> Clip | None:
    """An MHFU slot as one clip over the whole rig, track i driving joint i, with the loop of
    the part of the lowest joint; `motion.put` is the inverse. None when no part plays `slot`."""
    tracks = [Track() for _ in skeleton.bones]
    first: Clip | None = None
    for part, joints in part_joints([b.stream for b in skeleton.bones]).items():
        clip = part_clip(anim, part, slot)
        if clip is None:
            continue
        if first is None:
            first = clip
        for joint, track in zip(joints, clip.tracks, strict=False):
            tracks[joint] = track
    if first is None:
        return None
    return Clip(tracks, first.loop, first.loop_start)
