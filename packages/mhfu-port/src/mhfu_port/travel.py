# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""How far each executor entry moves a big monster in the game, and the travel it leaves behind.

The engine moves a monster by its ROOT joint, joint 0's first child (`root`): each AI frame it
adds the root's location change over the frames the clip advanced, turned by YAW and scaled by
the monster's size, to its position, and the floor then sets y. The FK draws the root with no
translation of its own and joint 0 with all of it, so travel keyed on joint 0 shows the model
running off and snapping back while the monster stands still. No channel turns the monster;
YAW is the AI's. `carry` moves a donor's travel to where the engine takes it.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
from mhfu.entries import PART_STREAMS
from mhp_formats import fu
from mhp_formats.anim import CHANNEL_BITS, Channel, Clip, Keyframe, Track
from mhp_formats.skeleton import Skeleton
from numpy.typing import ArrayLike

from . import fk, motion

AI_HZ = 30
"""AI frames a second."""
SPEED = 2.0
"""Clip frames per AI frame: CLIP_BLOCK.SPEED as the engine sets it each frame, before a move's
own percentage (the Tigrex charge plays at 2.4)."""

_LOC = {axis: bit for bit, (kind, axis) in CHANNEL_BITS.items() if kind == "loc"}
_ROT = {bit for bit, (kind, _) in CHANNEL_BITS.items() if kind == "rot"}
X, Y, Z = 0, 1, 2

Vec2 = tuple[float, float]


@dataclass(frozen=True)
class Travel:
    """One entry's clip, in model units before the monster's size: z is forward."""

    entry: int
    frames: int
    loop: bool
    carried: Vec2
    """(x, z) the root moves over the clip: what the monster travels."""
    drawn: Vec2
    """(x, z) the joints above the root move: drawn, then dropped when the clip ends."""

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
        _, loc = fk.Curves(clip, rig).at(np.array([0.0, n]))
        moved = loc[1] - loc[0]
        drawn = moved[above].sum(axis=0)
        out.append(
            Travel(
                e,
                n,
                bool(clip.loop),
                (float(moved[r, X]), float(moved[r, Z])),
                (float(drawn[X]), float(drawn[Z])),
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
    """`anim` with joint 0's x and z travel on the root, where the engine carries it, and the
    root's x and z sway plus both joints' height on joint 0, where the FK draws them.

    The FK pose is unchanged: both joints only translate, so their locations add. Raises
    ValueError where joint 0 rotates, is not the root's parent or sits off the origin."""
    r = root(skeleton)
    bones = skeleton.bones
    if bones[r].parent != 0 or any(bones[j].position != (0.0, 0.0, 0.0) for j in (0, r)):
        raise ValueError("carry needs joint 0 at the origin as the root's only parent")
    part = bones[r].stream
    if bones[0].stream != part:
        raise ValueError(f"joint 0 plays part {bones[0].stream}, the root part {part}")
    joints = fk.part_joints([b.stream for b in bones])[part]
    t0, tr = joints.index(0), joints.index(r)
    done: dict[int, Clip] = {}
    out = [list(s) for s in anim.streams]
    for si in range(PART_STREAMS * part, min(PART_STREAMS * (part + 1), len(out))):
        for slot, clip in enumerate(out[si]):
            if clip is None or max(t0, tr) >= len(clip.tracks):
                continue
            if id(clip) not in done:
                done[id(clip)] = _carried(clip, t0, tr)
            out[si][slot] = done[id(clip)]
    return fu.Anim(out, anim.tail)


def _carried(clip: Clip, t0: int, tr: int) -> Clip:
    base, top = clip.tracks[t0], clip.tracks[tr]
    if any(c.bit in _ROT and any(k.value for k in c.keyframes) for c in base.channels):
        raise ValueError("joint 0 rotates, so its travel cannot move to the root")
    b, t = _locs(base), _locs(top)
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
        v = _at(a.keyframes, f) + _at(b.keyframes, f)
        ease_in = _slope(a.keyframes, f, left=True) + _slope(b.keyframes, f, left=True)
        ease_out = _slope(a.keyframes, f, left=False) + _slope(b.keyframes, f, left=False)
        keys.append(Keyframe(*(_s16(x) for x in (v, f, ease_in, ease_out))))
    return Channel(a.bit, keys)


def _s16(x: float) -> int:
    v = round(x)
    if not -0x8000 <= v <= 0x7FFF:
        raise ValueError(f"{x:.0f} does not fit a keyframe")
    return v


def _segment(keys: list[Keyframe], f: float, left: bool) -> tuple[Keyframe, Keyframe] | None:
    """The keys around `f` (sorted keys), the one ending at `f` when `left`; None outside them,
    where the channel holds."""
    ks = sorted(keys, key=lambda k: k.frame)
    for k0, k1 in zip(ks, ks[1:], strict=False):
        inside = k0.frame < f <= k1.frame if left else k0.frame <= f < k1.frame
        if inside and k1.frame > k0.frame:
            return k0, k1
    return None


def _at(keys: list[Keyframe], f: float) -> float:
    """`fk.spline`'s value: held before the first key and after the last."""
    ks = sorted(keys, key=lambda k: k.frame)
    if f <= ks[0].frame:
        return float(ks[0].value)
    if f >= ks[-1].frame:
        return float(ks[-1].value)
    seg = _segment(ks, f, left=False)
    assert seg is not None
    k0, k1 = seg
    return float(fk.spline(f, k0.frame, k0.value, k0.ease_out, k1.frame, k1.value, k1.ease_in))


def _slope(keys: list[Keyframe], f: float, *, left: bool) -> float:
    """The spline's slope at `f` from the left or the right: a key's own ease there, 0 where
    the channel holds."""
    seg = _segment(keys, f, left)
    if seg is None:
        return 0.0
    k0, k1 = seg
    if f == k0.frame:
        return float(k0.ease_out)
    if f == k1.frame:
        return float(k1.ease_in)
    span = k1.frame - k0.frame
    s = (f - k0.frame) / span
    return (
        (k0.value * (6 * s * s - 6 * s) + k1.value * (6 * s - 6 * s * s)) / span
        + k0.ease_out * (3 * s * s - 4 * s + 1)
        + k1.ease_in * (3 * s * s - 2 * s)
    )


def _above(skeleton: Skeleton, joint: int) -> list[int]:
    """`joint`'s ancestors."""
    out = []
    p = skeleton.bones[joint].parent
    while 0 <= p < len(skeleton.bones) and p not in out:
        out.append(p)
        p = skeleton.bones[p].parent
    return out
