# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A clip as Blender F-curves and back, without bpy.

Bones rest at their bind joint with an identity orientation, so a pose bone's channels are the
engine's: `rotation_euler` (mode XYZ, the engine's Rz·Ry·Rx) is the rotation channel, `location`
the location channel less the bind offset. A key is Bezier with FREE handles a third of the span
out, which makes Blender's curve the engine's spline (`fk.spline`): a handle's slope is its ease.
Channels Blender does not pose (scale, the unnamed bits) ride on custom properties of the pose
bone, so they come back unchanged and move nothing.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
from mhp_formats.anim import Channel, Clip, Keyframe, Track, channel_kind, dequantize
from numpy.typing import NDArray

Floats = NDArray[np.float64]

ROTATION = "rotation_euler"
LOCATION = "location"
EXTRA = "mhfu_ch_{:03x}"
"""The pose-bone custom property carrying a channel Blender does not pose, by bit."""
LINEAR = "LINEAR"
_S16 = (-0x8000, 0x7FFF)
_UNNAMED_STEP = 1 / 16
"""The engine dequantizes every channel but rotation by 1/16."""
_PATH = re.compile(r'pose\.bones\["((?:[^"\\]|\\.)*)"\](?:\.(\w+)|\["((?:[^"\\]|\\.)*)"\])$')


@dataclass(frozen=True)
class Target:
    """Where a channel lives on its pose bone."""

    prop: str
    """`ROTATION`, `LOCATION` or a custom property name."""
    index: int
    step: float
    """What one raw unit is worth there."""

    @property
    def custom(self) -> bool:
        return self.prop not in (ROTATION, LOCATION)


def target(bit: int) -> Target:
    named = channel_kind(bit)
    if named is not None and named[0] != "scl":
        kind, axis = named
        return Target(ROTATION if kind == "rot" else LOCATION, axis, dequantize(kind, 1))
    step = _UNNAMED_STEP if named is None else dequantize("scl", 1)
    return Target(EXTRA.format(bit), 0, step)


def bit_of(prop: str, index: int) -> int | None:
    """The channel bit `target` puts on `prop[index]`; None for anything else."""
    if prop in (ROTATION, LOCATION):
        if not 0 <= index < 3:
            return None
        return (0x008 if prop == ROTATION else 0x040) << index
    if index == 0 and prop.startswith("mhfu_ch_"):
        try:
            bit = int(prop.removeprefix("mhfu_ch_"), 16)
        except ValueError:
            return None
        return bit if target(bit).prop == prop else None
    return None


def data_path(bone: str, prop: str) -> str:
    owner = f'pose.bones["{_escape(bone)}"]'
    return f"{owner}.{prop}" if prop in (ROTATION, LOCATION) else f'{owner}["{_escape(prop)}"]'


def parse_path(path: str) -> tuple[str, str] | None:
    """`(bone, prop)` of a pose-bone data path, None for any other."""
    m = _PATH.match(path)
    if m is None:
        return None
    return _unescape(m[1]), m[2] if m[2] is not None else _unescape(m[3])


@dataclass
class Curve:
    """One F-curve: key points `(frame, value)` and their handles, each `(keys, 2)`."""

    bone: str
    prop: str
    index: int
    co: Floats
    left: Floats
    right: Floats
    interpolation: Sequence[str] = field(default=())
    """Per key, Blender's names; empty means Bezier throughout."""

    @property
    def data_path(self) -> str:
        return data_path(self.bone, self.prop)


def points(keys: Sequence[Keyframe], step: float, offset: float = 0.0) -> tuple[Floats, ...]:
    """`(co, left, right)` for engine keys, in Blender's units (`raw * step - offset`).

    A handle sits a third of the way to the neighbouring key, or one frame out where there is
    none; its y comes from its x as Blender stores it, so the slope reads back exactly."""
    n = len(keys)
    frame = np.array([k.frame for k in keys], dtype=np.float64)
    value = np.array([k.value for k in keys], dtype=np.float64) * step - offset
    co = np.stack([frame, value], axis=-1).reshape(n, 2)
    gap = np.diff(frame)
    before = np.concatenate([[0.0], gap])
    after = np.concatenate([gap, [0.0]])
    out = []
    for span, ease, sign in ((before, "ease_in", -1.0), (after, "ease_out", 1.0)):
        reach = np.where(span > 0, span / 3, 1.0)
        x = (frame + sign * reach).astype(np.float32).astype(np.float64)
        slope = np.array([getattr(k, ease) for k in keys], dtype=np.float64) * step
        out.append(np.stack([x, value + slope * (x - frame)], axis=-1).reshape(n, 2))
    return co, out[0], out[1]


def keyframes(
    co: Floats,
    left: Floats,
    right: Floats,
    step: float,
    offset: float = 0.0,
    interpolation: Sequence[str] = (),
) -> list[Keyframe]:
    """Engine keys for Blender key points: `points`' inverse, which rounds to the format.

    A key's ease is its handle's slope; a Linear segment gives both its ends the chord's slope.
    Other interpolation modes are read as Bezier."""
    co, left, right = (np.asarray(a, dtype=np.float64).reshape(-1, 2) for a in (co, left, right))
    n = len(co)
    frame, value = co[:, 0], co[:, 1]
    ease_in = _slope(value - left[:, 1], frame - left[:, 0])
    ease_out = _slope(right[:, 1] - value, right[:, 0] - frame)
    linear = np.array([i < n - 1 and m == LINEAR for i, m in enumerate(interpolation)], dtype=bool)
    if linear.any():
        k = np.flatnonzero(linear)
        chord = _slope(value[k + 1] - value[k], frame[k + 1] - frame[k])
        ease_out[k] = chord
        ease_in[k + 1] = chord
    return [
        Keyframe(_raw(v + offset, step), _raw(f, 1.0), _raw(i, step), _raw(o, step))
        for f, v, i, o in zip(frame, value, ease_in, ease_out, strict=True)
    ]


def to_curves(
    clip: Clip,
    bones: Sequence[str],
    bind_local: Floats,
    joint_tracks: Mapping[int, int] | None = None,
) -> list[Curve]:
    """`clip`'s channels on the bones that play them; `bones[j]` names joint j.

    `joint_tracks` maps joint to track (a donor's records); without it track j drives joint j.
    Tracks no joint plays have no curve."""
    out = []
    for joint, t in _track_of(joint_tracks, len(bones), len(clip.tracks)).items():
        for channel in clip.tracks[t].channels:
            to = target(channel.bit)
            co, left, right = points(channel.keyframes, to.step, _offset(to, bind_local, joint))
            out.append(Curve(bones[joint], to.prop, to.index, co, left, right))
    return out


def to_clip(
    curves: Iterable[Curve],
    joints: Mapping[str, int],
    bind_local: Floats,
    loop: int,
    loop_start: float,
    base: Clip | None = None,
    joint_tracks: Mapping[int, int] | None = None,
) -> Clip:
    """`to_curves`' inverse: the clip a set of F-curves plays; `joints` maps bone name to joint.

    A track a joint plays holds that bone's curves, in `base`'s channel order where `base` has
    the channel, the rest by bit; a track no joint plays comes from `base`. Without `base` the
    clip has a track for every joint, or for every mapped track. Curves of other bones and
    properties are ignored."""
    n = len(bind_local)
    width = len(base.tracks) if base is not None else 0
    if joint_tracks is not None:
        width = max(width, max(joint_tracks.values(), default=-1) + 1)
    track_of = _track_of(joint_tracks, n, width or n)
    width = width or n
    by_track: dict[int, dict[int, list[Keyframe]]] = {t: {} for t in track_of.values()}
    for c in curves:
        joint = joints.get(c.bone)
        bit = bit_of(c.prop, c.index)
        if joint is None or bit is None or joint not in track_of:
            continue
        to = target(bit)
        offset = _offset(to, bind_local, joint)
        by_track[track_of[joint]][bit] = keyframes(
            c.co, c.left, c.right, to.step, offset, c.interpolation
        )
    native = [] if base is None else list(base.tracks)
    tracks = native + [Track() for _ in range(width - len(native))]
    for t, found in by_track.items():
        order = [ch.bit for ch in native[t].channels] if t < len(native) else []
        bits = [b for b in order if b in found] + sorted(set(found) - set(order))
        tracks[t] = Track([Channel(b, found[b]) for b in bits])
    return Clip(tracks, loop, loop_start)


def _offset(to: Target, bind_local: Floats, joint: int) -> float:
    """What a location curve leaves out: the joint's bind offset on that axis."""
    return float(bind_local[joint][to.index]) if to.prop == LOCATION else 0.0


def _track_of(joint_tracks: Mapping[int, int] | None, joints: int, tracks: int) -> dict[int, int]:
    if joint_tracks is None:
        return {j: j for j in range(min(joints, tracks))}
    return {j: t for j, t in sorted(joint_tracks.items()) if 0 <= j < joints and 0 <= t < tracks}


def _slope(dy: Floats, dx: Floats) -> Floats:
    out: Floats = np.divide(dy, dx, out=np.zeros_like(dy), where=dx > 0)
    return out


def _raw(x: float, step: float) -> int:
    v = x / step
    if not math.isfinite(v):
        return 0
    return max(_S16[0], min(_S16[1], round(v)))


def _escape(name: str) -> str:
    return name.replace("\\", "\\\\").replace('"', '\\"')


def _unescape(name: str) -> str:
    return re.sub(r"\\(.)", r"\1", name)
