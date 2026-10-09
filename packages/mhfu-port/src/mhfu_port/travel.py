# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""How far each executor entry moves a big monster in the game, which way it turns it, and the
motion a port leaves where the engine does not take it.

The engine moves a monster by its ROOT joint, joint 0's first child (`root`): each AI frame it
adds the root's location change over the frames the clip advanced, turned by YAW and scaled by
the monster's size, to its position, and the floor then sets y. The FK draws the root's rotation
but none of its translation, and joint 0 with both, so travel keyed on joint 0 shows the model
running off and snapping back while the monster stands still. Only YAW turns the monster, so a
body that ends a clip turned snaps back with the next one. `carry` puts a donor's travel on the
root and takes its turn out of the clip; `turns` is the curve YAW follows instead.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

import numpy as np
from mhfu.entries import PART_STREAMS
from mhp_formats import fu
from mhp_formats.anim import CHANNEL_BITS, Channel, Clip, Keyframe, Kind, Track, dequantize
from mhp_formats.skeleton import Skeleton
from numpy.typing import ArrayLike

from . import fk, motion
from .motion import s16, slope_at, value_at

AI_HZ = 30
"""AI frames a second."""
SPEED = 2.0
"""Clip frames per AI frame: CLIP_BLOCK.SPEED as the engine sets it each frame, before a move's
own percentage (the Tigrex charge plays at 2.4)."""

_LOC = {axis: bit for bit, (kind, axis) in CHANNEL_BITS.items() if kind == "loc"}
_ROT = {bit for bit, (kind, _) in CHANNEL_BITS.items() if kind == "rot"}
X, Y, Z = 0, 1, 2
TURN = 0x10000
"""YAW units in a full turn."""
TURN_MIN = math.radians(2.0)
"""A body that ends its clip turned less than this stays in the clip: the snap is not seen."""
FAST_TURN = math.radians(10.0)
"""A turn between two keys past which joint 0's sway is baked on every frame: turned back
fast, it bends between keys."""
KEY_STEP = 2
"""Clip frames between the keys `carry` bakes and `Turn` holds: the engine's clip speed, so
its cursor lands on them."""
_ROT_Y = next(bit for bit, kind in CHANNEL_BITS.items() if kind == ("rot", Y))

Vec2 = tuple[float, float]


@dataclass(frozen=True)
class Travel:
    """One entry's clip, in model units before the monster's size: z is forward."""

    entry: int
    frames: int
    loop: bool
    carried: Vec2
    """(x, z) the monster travels over the clip, in the frame YAW has at its start: the root's
    moves, each turned by the root's own turn (`carry`'s), which YAW follows."""
    drawn: Vec2
    """(x, z) the joints above the root move: drawn, then dropped when the clip ends."""
    turn: int = 0
    """YAW units the body (the root's children) ends the clip turned (`body_turn`): it snaps
    back with the next clip unless YAW turns as much."""

    @property
    def distance(self) -> float:
        return math.hypot(*self.carried)

    def seconds(self, speed: float = SPEED) -> float:
        return self.frames / speed / AI_HZ


def root(skeleton: Skeleton) -> int:
    """The joint whose motion the engine carries: joint 0's first child."""
    child = skeleton.bones[0].child if skeleton.bones else -1
    if not 0 < child < len(skeleton.bones):
        raise ValueError("joint 0 has no child, so the monster has no root to move by")
    return child


def of(anim: fu.Anim, skeleton: Skeleton, entries: Iterable[int] | None = None) -> list[Travel]:
    """Each entry's travel (every filled entry by default)."""
    r = root(skeleton)
    rig = fk.Rig.from_skeleton(skeleton)
    above = _above(skeleton, r)
    out = []
    for e in motion.filled(anim) if entries is None else entries:
        clip = fk.rig_clip(anim, e, skeleton)
        if clip is None:
            continue
        n = motion.frames(clip)
        rot, loc = fk.Curves(clip, rig).at(_grid(n))
        moved = loc[-1] - loc[0]
        drawn = moved[above].sum(axis=0)
        heading = body_turn(rig, clip, skeleton, np.arange(n + 1.0))
        turn = heading[-1] - heading[0]
        x, z = _turn_xz(np.diff(loc[:, r], axis=0), -rot[1:, r, Y]).sum(axis=1).tolist()
        out.append(
            Travel(
                e,
                n,
                bool(clip.loop),
                (x, z),
                (float(drawn[X]), float(drawn[Z])),
                round(float(turn) / math.tau * TURN),
            )
        )
    return out


def path(anim: fu.Anim, skeleton: Skeleton, entry: int, frames: ArrayLike) -> fk.Floats:
    """`(len(frames), 2)`: the root's (x, z) at those clip frames, model units."""
    clip = fk.rig_clip(anim, entry, skeleton)
    if clip is None:
        raise ValueError(f"no clip in entry {entry}")
    r = root(skeleton)
    _, loc = fk.Curves(clip, fk.Rig.from_skeleton(skeleton)).at(np.asarray(frames, float))
    out: fk.Floats = loc[:, r][:, [X, Z]]
    return out


def carry(anim: fu.Anim, skeleton: Skeleton) -> fu.Anim:
    """`anim` as the engine plays it right: joint 0's x and z travel on the root, which the
    engine carries, the root's x and z sway and both joints' height on joint 0, which the FK
    draws; and where the body starts or ends a clip facing TURN_MIN or more off the model's
    forward, its facing taken out of it, so every clip starts and ends facing YAW.

    Travel: a clip whose root holds a height (a donor's hip, which the FK drops) has its x and
    z swapped with joint 0's and its height added to joint 0's; both joints only translate, so
    their locations add and the pose is unchanged. A native's or a retarget's root holds none,
    nor does a carried one. Turn: the root's rotation
    turns everything below it back by `body_turn`'s curve, and joint 0's sway and the root's
    travel turn with it (baked every KEY_STEP frames), so the clip played with YAW following
    `turns` draws the source's pose and path. Raises ValueError where joint 0 rotates, is not
    the root's parent or sits off the origin, or where the root already rotates."""
    r = root(skeleton)
    bones = skeleton.bones
    if bones[r].parent != 0 or any(bones[j].position != (0.0, 0.0, 0.0) for j in (0, r)):
        raise ValueError("carry needs joint 0 at the origin as the root's only parent")
    part = bones[r].stream
    if bones[0].stream != part:
        raise ValueError(f"joint 0 plays part {bones[0].stream}, the root part {part}")
    joints = fk.part_joints([b.stream for b in bones])[part]
    t0, tr = joints.index(0), joints.index(r)
    streams = range(PART_STREAMS * part, min(PART_STREAMS * (part + 1), len(anim.streams)))
    swapped = _each(anim, streams, lambda c: _carried(c, t0, tr) if len(c.tracks) > tr else c)
    rig = fk.Rig.from_skeleton(skeleton)
    turned: dict[int, Clip] = {}
    for e in motion.filled(swapped):
        si, slot = fk.entry_slot(part, e)
        own = swapped.streams[si][slot] if si < len(swapped.streams) else None
        whole = fk.rig_clip(swapped, e, skeleton)
        if own is None or whole is None or id(own) in turned or len(own.tracks) <= tr:
            continue
        turned[id(own)] = _turned(own, whole, rig, skeleton, t0, tr)
    return _each(swapped, streams, lambda c: turned.get(id(c), c))


def _each(anim: fu.Anim, streams: Iterable[int], fn: Callable[[Clip], Clip]) -> fu.Anim:
    """`anim` with `fn` of each clip in `streams`, once per stored clip, so shared stay shared."""
    done: dict[int, Clip] = {}
    out = [list(s) for s in anim.streams]
    for si in streams:
        for slot, clip in enumerate(out[si]):
            if clip is not None:
                if id(clip) not in done:
                    done[id(clip)] = fn(clip)
                out[si][slot] = done[id(clip)]
    return fu.Anim(out, anim.tail)


def body_turn(rig: fk.Rig, clip: Clip, skeleton: Skeleton, frames: ArrayLike) -> fk.Floats:
    """Radians the body (the root's rotating children, averaged) faces off the model's forward
    about y at each of `frames` (whole clip frames, one apart, for the unwrap): the twist of
    its rotation, from within half a turn at the first, ending within half a turn of the
    turn between its first and last pose."""
    f = np.asarray(frames, dtype=np.float64)
    r = root(skeleton)
    kids = [
        j
        for j, b in enumerate(skeleton.bones)
        if b.parent == r and j < len(clip.tracks)
        if any(c.bit in _ROT and c.keyframes for c in clip.tracks[j].channels)
    ]
    if not kids or len(f) < 2:
        return np.zeros(len(f))
    q = _quat(rig.world(*fk.Curves(clip, rig).at(f))[:, kids, :3, :3])
    flip = np.cumprod(np.where(np.einsum("tkq,tkq->tk", q[1:], q[:-1]) < 0, -1.0, 1.0), axis=0)
    q[1:] *= flip[..., None]
    w, y = q[..., 0].sum(axis=1), q[..., 2].sum(axis=1)
    twist = np.unwrap(_wrap(2 * np.arctan2(y, w)))
    net = _wrap(twist[-1] - twist[0])
    out: fk.Floats = twist - (twist[-1] - twist[0] - net) * (f - f[0]) / (f[-1] - f[0])
    return out


def _wrap(a: ArrayLike) -> fk.Floats:
    """Radians within half a turn."""
    out: fk.Floats = (np.asarray(a, dtype=np.float64) + math.pi) % math.tau - math.pi
    return out


def _quat(m: fk.Floats) -> fk.Floats:
    """`(..., 3, 3)` rotations to `(..., 4)` quaternions (w, x, y, z), w >= 0."""
    d = np.stack([m[..., 0, 0], m[..., 1, 1], m[..., 2, 2]], axis=-1)
    w = np.sqrt(np.maximum(0.0, 1 + d[..., 0] + d[..., 1] + d[..., 2])) / 2
    x = np.sqrt(np.maximum(0.0, 1 + d[..., 0] - d[..., 1] - d[..., 2])) / 2
    y = np.sqrt(np.maximum(0.0, 1 - d[..., 0] + d[..., 1] - d[..., 2])) / 2
    z = np.sqrt(np.maximum(0.0, 1 - d[..., 0] - d[..., 1] + d[..., 2])) / 2
    x = np.copysign(x, m[..., 2, 1] - m[..., 1, 2])
    y = np.copysign(y, m[..., 0, 2] - m[..., 2, 0])
    z = np.copysign(z, m[..., 1, 0] - m[..., 0, 1])
    out: fk.Floats = np.stack([w, x, y, z], axis=-1)
    return out


def _grid(frames: int) -> fk.Floats:
    """The key frames of a clip `frames` long: every KEY_STEP, and its end."""
    out: fk.Floats = np.unique(np.append(np.arange(0, frames, KEY_STEP), frames)).astype(float)
    return out


def _turned(own: Clip, whole: Clip, rig: fk.Rig, skeleton: Skeleton, t0: int, tr: int) -> Clip:
    """The root's part clip `own` with the body's turn over `whole` taken out (`carry`)."""
    n = motion.frames(whole)
    theta = body_turn(rig, whole, skeleton, np.arange(n + 1.0)) if n > 0 else np.zeros(1)
    if max(abs(theta[0]), abs(theta[-1])) < TURN_MIN:
        return own
    r = root(skeleton)
    top = own.tracks[tr]
    if any(c.bit in _ROT and any(k.value for k in c.keyframes) for c in top.channels):
        raise ValueError("the root rotates, so a turn cannot go on it")
    grid = _grid(n)
    th = theta[grid.astype(int)]
    turn = _curve(grid, th)
    curves = fk.Curves(whole, rig)

    def back(joint: int) -> Callable[[fk.Floats], fk.Floats]:
        """`joint`'s x and z at frames, turned back by the curve."""
        return lambda f: _turn_xz(curves.at(f)[1][:, joint], -turn(f))

    def bake(at: fk.Floats, values: fk.Floats, joint: int) -> list[Channel]:
        ins, outs = _ease(back(joint), at, -1), _ease(back(joint), at, 1)
        return [Channel(0, _keys(at, values[k], ins[k], outs[k], "loc")) for k in (0, 1)]

    base, travel = own.tracks[t0], Track([ch for ch in top.channels if ch.bit not in _ROT])
    tracks = list(own.tracks)
    sways, travels = _locs(base), _locs(top)
    if X in sways or Z in sways:
        fast = np.flatnonzero(np.abs(np.diff(th)) > FAST_TURN)
        dense = [f for k in fast for f in range(int(grid[k]), int(grid[k + 1]) + 1)]
        at = np.union1d(grid, [k.frame for c in sways.values() for k in c.keyframes] + dense)
        at = at[(at >= 0) & (at <= n)]
        x, z = bake(at, back(0)(at), 0)
        tracks[t0] = _track(base, {X: x, Y: sways.get(Y), Z: z}, n)
    if X in travels or Z in travels:
        loc = curves.at(grid)[1][:, r]
        steps = _turn_xz(np.diff(loc, axis=0), -th[1:])
        path = loc[0, [X, Z]][:, None] + np.hstack([[[0.0], [0.0]], np.cumsum(steps, axis=1)])
        x, z = bake(grid, path, r)
        travel = _track(travel, {X: x, Y: None, Z: z}, n)
    slope = -np.gradient(th, grid)
    rot = Channel(_ROT_Y, _keys(grid, -th, slope, slope, "rot"))
    tracks[tr] = Track(sorted([*travel.channels, rot], key=lambda ch: ch.bit))
    return Clip(tracks, own.loop, own.loop_start)


def _turn_xz(v: fk.Floats, th: fk.Floats) -> fk.Floats:
    """`(2, n)`: the x and z of `(n, 3)` vectors turned by `th` the way YAW grows."""
    c, s = np.cos(th), np.sin(th)
    out: fk.Floats = np.stack([v[:, X] * c + v[:, Z] * s, -v[:, X] * s + v[:, Z] * c])
    return out


_EPS = 1e-3
"""Clip frames a one-sided slope is measured over."""


def _ease(fn: Callable[[fk.Floats], fk.Floats], at: fk.Floats, side: int) -> fk.Floats:
    """The slope of `fn` at `at` from the left (`side` -1) or the right (1), per clip frame."""
    out: fk.Floats = (fn(at + side * _EPS) - fn(at)) / (side * _EPS)
    return out


def _curve(grid: fk.Floats, values: fk.Floats) -> Callable[[ArrayLike], fk.Floats]:
    """The curve `_keys` makes of `values` at `grid` with central-difference slopes, held
    outside it: what the root's turn and `Turn.at` follow."""
    slope = np.gradient(values, grid) if len(grid) > 1 else np.zeros(1)

    def at(frame: ArrayLike) -> fk.Floats:
        f = np.clip(np.asarray(frame, dtype=np.float64), grid[0], grid[-1])
        if len(grid) < 2:
            return np.full(f.shape, float(values[0]))
        hi = np.clip(np.searchsorted(grid, f, side="right"), 1, len(grid) - 1)
        lo = hi - 1
        return fk.spline(f, grid[lo], values[lo], slope[lo], grid[hi], values[hi], slope[hi])

    return at


def _keys(
    at: fk.Floats, values: fk.Floats, ins: fk.Floats, outs: fk.Floats, kind: Kind
) -> list[Keyframe]:
    """Keys through `values` at frames `at`, entering and leaving with slopes `ins`, `outs`."""
    q = dequantize(kind, 1)
    return [
        Keyframe(s16(v / q), int(f), s16(i / q), s16(o / q))
        for f, v, i, o in zip(at, values, ins, outs, strict=True)
    ]


@dataclass(frozen=True)
class Turn:
    """What YAW turns while an entry's clip plays: `keys[k]` YAW units at clip frame
    `k * KEY_STEP`, the last at the clip's end, the body's own facing (`body_turn`), which
    `carry` turned the clip back by. YAW reads its value at the clip's start plus the key at
    the cursor less the key there, as the root motion is applied; `total` is the turn."""

    frames: int
    keys: tuple[int, ...]
    data: int
    """YAW units the clip's own body turned from its first frame to its last."""
    authored: float | None
    """The manifest's `turn`, degrees."""

    @property
    def total(self) -> int:
        """YAW units turned over the clip."""
        return self.keys[-1] - self.keys[0]

    def at(self, frame: ArrayLike) -> fk.Floats:
        """YAW units at clip frame `frame`, on the curve the root's turn follows."""
        return _curve(_grid(self.frames), np.asarray(self.keys, dtype=np.float64))(frame)

    def lua(self) -> str:
        """The keys as 4 hex digits each, YAW's own 16 bits: the port's turns module."""
        return "".join(f"{k & 0xFFFF:04x}" for k in self.keys)


def turn_of(clip: Clip, skeleton: Skeleton, authored: float | None = None) -> Turn | None:
    """YAW's curve over a carried whole-rig `clip`: the turn `carry` put on its root, plus the
    difference to the manifest's `authored` degrees eased in (smoothstep), as a scale would blow
    a small turn's sway up. None where neither turns it."""
    r = root(skeleton)
    n = motion.frames(clip)
    turns = r < len(clip.tracks) and any(
        c.bit == _ROT_Y and c.keyframes for c in clip.tracks[r].channels
    )
    if n <= 0 or (not turns and authored is None):
        return None
    grid = _grid(n)
    rot, _ = fk.Curves(clip, fk.Rig.from_skeleton(skeleton)).at(grid)
    data = -rot[:, r, Y]
    net = float(data[-1] - data[0])
    theta = data
    if authored is not None:
        u = grid / n
        theta = data + (math.radians(authored) - net) * (3 * u**2 - 2 * u**3)
    keys = tuple(int(k) for k in np.round(theta / math.tau * TURN))
    return Turn(n, keys, round(net / math.tau * TURN), authored)


def turns(
    anim: fu.Anim, skeleton: Skeleton, authored: Mapping[int, float] | None = None
) -> dict[int, Turn]:
    """`turn_of` each filled entry of a carried `anim`, `authored` degrees by entry."""
    out = {}
    for e in motion.filled(anim):
        clip = fk.rig_clip(anim, e, skeleton)
        t = None if clip is None else turn_of(clip, skeleton, (authored or {}).get(e))
        if t is not None:
            out[e] = t
    return out


def _carried(clip: Clip, t0: int, tr: int) -> Clip:
    base, top = clip.tracks[t0], clip.tracks[tr]
    if any(c.bit in _ROT and any(k.value for k in c.keyframes) for c in base.channels):
        raise ValueError("joint 0 rotates, so its travel cannot move to the root")
    b, t = _locs(base), _locs(top)
    height = t.get(Y)
    if height is None or not any(k.value for k in height.keyframes):
        return clip
    span = motion.frames(clip) or 2
    new_base = _track(base, {X: t.get(X), Y: _sum(b.get(Y), t.get(Y)), Z: t.get(Z)}, span)
    new_top = _track(top, {X: b.get(X), Y: None, Z: b.get(Z)}, span)
    tracks = list(clip.tracks)
    tracks[t0], tracks[tr] = new_base, new_top
    return Clip(tracks, clip.loop, clip.loop_start)


def _locs(track: Track) -> dict[int, Channel]:
    return {a: c for a, bit in _LOC.items() for c in track.channels if c.bit == bit}


def _track(old: Track, loc: dict[int, Channel | None], span: int) -> Track:
    """`old` with its location channels replaced by `loc`'s; a track left empty that had
    channels rests instead of collapsing its joint."""
    kept = [c for c in old.channels if c.bit not in _LOC.values()]
    moved = [Channel(_LOC[a], list(c.keyframes)) for a, c in loc.items() if c is not None]
    channels = sorted(kept + moved, key=lambda c: c.bit)
    if not channels and old.channels:
        return motion.rest(span)
    return Track(channels)


def _sum(a: Channel | None, b: Channel | None) -> Channel | None:
    """The channel whose spline is `a`'s plus `b`'s: keyed on both key sets, each key's value and
    its two slopes summed, which is exact between keys (raw units, rounded)."""
    if a is None or b is None:
        return a or b
    frames = sorted({k.frame for k in a.keyframes} | {k.frame for k in b.keyframes})
    keys = []
    for f in frames:
        v = value_at(a.keyframes, f) + value_at(b.keyframes, f)
        ease_in = slope_at(a.keyframes, f, left=True) + slope_at(b.keyframes, f, left=True)
        ease_out = slope_at(a.keyframes, f, left=False) + slope_at(b.keyframes, f, left=False)
        keys.append(Keyframe(*(s16(x) for x in (v, f, ease_in, ease_out))))
    return Channel(a.bit, keys)


def _above(skeleton: Skeleton, joint: int) -> list[int]:
    """`joint`'s ancestors."""
    out = []
    p = skeleton.bones[joint].parent
    while 0 <= p < len(skeleton.bones) and p not in out:
        out.append(p)
        p = skeleton.bones[p].parent
    return out
