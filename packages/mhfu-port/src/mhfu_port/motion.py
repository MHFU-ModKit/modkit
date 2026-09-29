# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The port's animation: the donor's moveset rebuilt as the MHFU in-game animation the engine
plays on the port's joints.

MHFU splits one rig into parts by `Bone.stream` (the Tigrex: 31 body, 9 head, 5 tail joints),
each part's clips in its own animation stream (`fk.FU_PART_STREAM`), all playing the same slot
together. `fk.rig_clip` joins a slot's parts back into one clip.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from mhp_formats import fu, p3rd
from mhp_formats.anim import CHANNEL_BITS, Channel, Clip, Keyframe, Track, quantize

from .fk import FU_PART_STREAM

_BIT = {kind: bit for bit, kind in CHANNEL_BITS.items()}
_ROTATION = tuple(_BIT["rot", axis] for axis in range(3))
_LOC_Y = _BIT["loc", 1]
_PLAYABLE = 0x1FF
"""Rotation, location and the three unnamed bits below them, which native clips carry too. The
engine looks a channel up in a table indexed by its bit and crashes on the scale bits."""
_MIN_SPAN = 2
"""Frames a rest track spans on a clip without keyframes, so the interpolator has a span."""


def moveset(anim: bytes) -> list[Clip | None]:
    """A donor's clips by slot, from its MHP3rd animation file: stream 0, the main clip set."""
    return p3rd.Anim.from_bytes(anim).streams[0]


def rest(frames: int) -> Track:
    """A track holding its joint at the bind pose for `frames` frames.

    A joint with no channels keeps a zeroed matrix and collapses its geometry to the origin; zero
    rotation poses it at its bind offset from its parent."""
    keys = [Keyframe(0, 0), Keyframe(0, frames)]
    return Track([Channel(bit, list(keys)) for bit in _ROTATION])


def build(
    clips: Sequence[Clip | None],
    host: fu.Anim,
    streams: Sequence[int],
    track_of: Mapping[int, int | None] | None = None,
) -> fu.Anim:
    """The donor's `clips` (by slot) as an in-game animation on the host's slot layout.

    `streams` is the joint count of each skeleton part (`Bone.stream`), in joint order; joint `j`
    plays donor track `track_of[j]`, or track `j` without a map, and rests for the clip's own
    length where that is None or missing. Scale channels are dropped. A slot the host fills plays
    the donor's `donor_slot`; a donor clip in a slot the host leaves empty is dropped."""
    if any(width < 0 for width in streams):
        raise ValueError(f"negative stream width in {list(streams)}")
    for k, width in enumerate(streams):
        si = FU_PART_STREAM * k
        if width and not any(c is not None for c in _stream(host, si)):
            raise ValueError(f"part {k} plays stream {si}, where the host has no clip")
    donor = {slot: clip for slot, clip in enumerate(clips) if clip is not None}
    if not donor:
        raise ValueError("the donor has no clips")
    joints = sum(streams)
    split: dict[int, list[Clip]] = {}
    for clip in donor.values():
        if id(clip) not in split:
            split[id(clip)] = _split(_joint_tracks(clip, joints, track_of), clip, streams)
    out: list[list[Clip | None]] = [[None] * len(stream) for stream in host.streams]
    for k, width in enumerate(streams):
        si = FU_PART_STREAM * k
        for slot, native in enumerate(_stream(host, si) if width else []):
            if native is not None:
                out[si][slot] = split[id(donor[donor_slot(clips, slot)])][k]
    return fu.Anim(out)


def donor_slot(clips: Sequence[Clip | None], slot: int) -> int:
    """The donor slot whose clip `build` plays in host slot `slot`: its own, else the lowest."""
    filled = [s for s, clip in enumerate(clips) if clip is not None]
    if not filled:
        raise ValueError("the donor has no clips")
    return slot if slot < len(clips) and clips[slot] is not None else filled[0]


def pelvis(
    clips: Sequence[Clip | None],
    bone_of_track: Mapping[int, int] | None = None,
    parents: Sequence[int] = (),
) -> int | None:
    """The track a ground lift moves: the root-most bone with a location-Y channel, else the
    track with the largest mean location Y. None when no track has one.

    `bone_of_track` places tracks on the bones `parents` links. Root-most, not largest: a rig
    that forks into front and rear halves carries the body height below the fork on one side,
    and lifting that side stands the monster on its hind legs."""
    score: dict[int, float] = {}
    for clip in clips:
        if clip is None:
            continue
        for i, track in enumerate(clip.tracks):
            for channel in track.channels:
                if channel.bit == _LOC_Y and channel.keyframes:
                    mean = sum(abs(k.value) for k in channel.keyframes) / len(channel.keyframes)
                    score[i] = score.get(i, 0.0) + mean
    if not score:
        return None
    if bone_of_track and parents:
        placed = [t for t in score if t in bone_of_track]
        if placed:
            return min(placed, key=lambda t: (_depth(bone_of_track[t], parents), t))
    return max(score, key=score.__getitem__)


def lift(clips: Sequence[Clip | None], units: float, track: int) -> list[Clip | None]:
    """`clips` with `units` (world units) added to `track`'s location Y in every clip.

    The whole body rises with the pelvis and its bob is kept; this moves a donor rig authored
    against another game's floor onto MHFU's, where the rest pose stands on the origin."""
    raw = quantize("loc", units)
    done: dict[int, Clip] = {}
    out: list[Clip | None] = []
    for clip in clips:
        if clip is None or track >= len(clip.tracks):
            out.append(clip)
            continue
        if id(clip) not in done:
            tracks = list(clip.tracks)
            tracks[track] = Track([_lifted(c, raw) for c in tracks[track].channels])
            done[id(clip)] = Clip(tracks, clip.loop, clip.loop_start)
        out.append(done[id(clip)])
    return out


def _lifted(channel: Channel, raw: int) -> Channel:
    if channel.bit != _LOC_Y:
        return channel
    keys = [k._replace(value=max(-0x8000, min(0x7FFF, k.value + raw))) for k in channel.keyframes]
    return Channel(channel.bit, keys)


def _joint_tracks(
    clip: Clip, joints: int, track_of: Mapping[int, int | None] | None
) -> list[Track]:
    """One engine track per joint: the donor's playable channels in bit order, or a rest track
    spanning the clip."""
    span = max((k.frame for t in clip.tracks for c in t.channels for k in c.keyframes), default=0)
    span = span if span > 0 else _MIN_SPAN
    out = []
    for j in range(joints):
        t = j if track_of is None else track_of.get(j)
        if t is None or not 0 <= t < len(clip.tracks):
            out.append(rest(span))
            continue
        channels = [c for c in clip.tracks[t].channels if not c.bit & ~_PLAYABLE]
        out.append(Track([Channel(c.bit, list(c.keyframes)) for c in sorted(channels, key=_bit)]))
    return out


def _split(tracks: list[Track], clip: Clip, streams: Sequence[int]) -> list[Clip]:
    out = []
    start = 0
    for width in streams:
        out.append(Clip(tracks[start : start + width], clip.loop, clip.loop_start))
        start += width
    return out


def _bit(channel: Channel) -> int:
    return channel.bit


def _stream(anim: fu.Anim, index: int) -> list[Clip | None]:
    return anim.streams[index] if index < len(anim.streams) else []


def _depth(bone: int, parents: Sequence[int]) -> int:
    depth = 0
    while 0 <= bone < len(parents) and parents[bone] >= 0 and depth < len(parents):
        bone = parents[bone]
        depth += 1
    return depth
