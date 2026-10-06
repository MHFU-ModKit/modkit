# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""MHFU in-game animation: the donor's moveset rebuilt as the clips the engine plays on the port's
joints, and an edited whole-rig clip written back into an executor entry.

MHFU splits one rig into parts by `Bone.stream` (the Tigrex: 31 body, 9 head, 5 tail joints),
each part's clips in its own animation streams (`mhfu.entries.PART_STREAMS`), all playing the same
executor entry together (`fk.entry_slot`). `fk.rig_clip` joins an entry's parts into one clip;
`split` and `put` undo it. MHP3rd keeps each clip whole, in independent streams.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from mhfu.entries import PART_STREAMS
from mhp_formats import fu, p3rd
from mhp_formats.anim import CHANNEL_BITS, AnimPack, Channel, Clip, Keyframe, Track, quantize
from mhp_formats.skeleton import Skeleton

from .fk import entry_of, entry_slot, part_clip, part_joints, rig_clip

_BIT = {kind: bit for bit, kind in CHANNEL_BITS.items()}
_ROTATION = tuple(_BIT["rot", axis] for axis in range(3))
_LOC_Y = _BIT["loc", 1]
_PLAYABLE = 0x1FF
"""Rotation, location and the three unnamed bits below them, which native clips carry too. The
engine looks a channel up in a table indexed by its bit and crashes on the scale bits."""
_MIN_SPAN = 2
"""Frames a rest track spans on a clip without keyframes, so the interpolator has a span."""
SOURCE_BANK = 100
"""MHP3rd motion ids per stream: a clip's id is `stream * SOURCE_BANK + slot`."""


def clip_id(stream: int, slot: int) -> int:
    """MHP3rd's motion id of `slot` in `stream`."""
    if not 0 <= slot < SOURCE_BANK:
        raise ValueError(f"slot {slot} of stream {stream} has no motion id")
    return stream * SOURCE_BANK + slot


def moveset(anim: bytes) -> dict[int, Clip]:
    """A donor's clips by MHP3rd motion id (`clip_id`), every stream of its animation file."""
    out = {}
    for s, stream in enumerate(p3rd.Anim.from_bytes(anim).streams):
        for slot, clip in enumerate(stream):
            if clip is not None:
                out[clip_id(s, slot)] = clip
    return out


def frames(clip: Clip) -> int:
    """The last keyframe: a clip's length."""
    return max((k.frame for t in clip.tracks for c in t.channels for k in c.keyframes), default=0)


def filled(anim: AnimPack) -> list[int]:
    """Every executor entry some stream fills."""
    return sorted(
        {
            entry_of(si, i)
            for si, s in enumerate(anim.streams)
            for i, c in enumerate(s)
            if c is not None
        }
    )


def split(clip: Clip, streams: Sequence[int]) -> dict[int, Clip]:
    """A whole-rig `clip` (track j drives joint j) dealt to the skeleton's parts, `streams[j]`
    being joint j's part (`Bone.stream`): each part's tracks in joint order, `clip`'s loop."""
    if len(clip.tracks) != len(streams):
        raise ValueError(f"{len(clip.tracks)} tracks for {len(streams)} joints")
    return {
        part: Clip([clip.tracks[j] for j in joints], clip.loop, clip.loop_start)
        for part, joints in part_joints(streams).items()
    }


def put(anim: fu.Anim, slot: int, clip: Clip, skeleton: Skeleton) -> fu.Anim:
    """`anim` with the whole-rig `clip` (as `fk.rig_clip` reads it) written back into executor
    entry `slot`.

    A part whose tracks and loop come out unchanged keeps its own clip object, so the slots
    sharing it still share one copy in the file. A part keeps its own `loop` and `loop_start`
    unless `clip` changes the slot's (`rig_clip` reports the first part's), and its tracks past
    its joints. A track `clip` leaves empty where the part's own is keyed plays `rest` instead:
    an empty track collapses its joint's geometry. Raises ValueError where `clip` keys a joint
    whose part does not play `slot`."""
    streams = [b.stream for b in skeleton.bones]
    whole = rig_clip(anim, slot, skeleton)
    span = frames(clip) or _MIN_SPAN
    retimed = whole is not None and (clip.loop, clip.loop_start) != (whole.loop, whole.loop_start)
    out = [list(s) for s in anim.streams]
    joints = part_joints(streams)
    for part, new in split(clip, streams).items():
        native = part_clip(anim, part, slot)
        if native is None:
            keyed = [j for j, t in zip(joints[part], new.tracks, strict=True) if t.channels]
            if keyed:
                raise ValueError(
                    f"slot {slot}: joints {keyed} are keyed, but their part does not play it"
                )
            continue
        tracks = list(native.tracks)
        for i, track in enumerate(new.tracks):
            if i < len(tracks):
                emptied = not track.channels and tracks[i].channels
                tracks[i] = rest(span) if emptied else track
            elif track.channels:
                raise ValueError(
                    f"slot {slot}: joint {joints[part][i]} is keyed, but its part's clip has "
                    f"{len(tracks)} tracks"
                )
        loop = (clip.loop, clip.loop_start) if retimed else (native.loop, native.loop_start)
        if tracks != native.tracks or loop != (native.loop, native.loop_start):
            si, i = entry_slot(part, slot)
            out[si][i] = Clip(tracks, *loop)
    return fu.Anim(out, anim.tail)


def rest(frames: int) -> Track:
    """A track holding its joint at the bind pose for `frames` frames.

    A joint with no channels keeps a zeroed matrix and collapses its geometry to the origin; zero
    rotation poses it at its bind offset from its parent."""
    keys = [Keyframe(0, 0), Keyframe(0, frames)]
    return Track([Channel(bit, list(keys)) for bit in _ROTATION])


def build(
    clips: Mapping[int, Clip],
    layout: Mapping[int, int],
    host: fu.Anim,
    streams: Sequence[int],
    track_of: Mapping[int, int | None] | None = None,
    keep_host: bool = False,
) -> fu.Anim:
    """The donor's `clips` (by id) as an in-game animation on the host's stream layout, each in
    the executor entry `layout` (entry -> id) gives it.

    `streams` is the joint count of each skeleton part (`Bone.stream`), in joint order; joint `j`
    plays donor track `track_of[j]`, or track `j` without a map, and rests for the clip's own
    length where that is None or missing. Scale channels are dropped. An entry the layout leaves
    keeps the host's clips with `keep_host` (they fit the host's rig only), else stays empty."""
    if any(width < 0 for width in streams):
        raise ValueError(f"negative stream width in {list(streams)}")
    for k, width in enumerate(streams):
        si = PART_STREAMS * k
        if width and not any(c is not None for c in _stream(host, si)):
            raise ValueError(f"part {k} plays stream {si}, where the host has no clip")
    if not layout:
        raise ValueError("the layout places no clip")
    part_of = [k for k, width in enumerate(streams) for _ in range(width)]
    parts: dict[int, dict[int, Clip]] = {}
    out: list[list[Clip | None]] = [
        list(s) if keep_host else [None for _ in s] for s in host.streams
    ]
    for entry, cid in sorted(layout.items()):
        clip = clips.get(cid)
        if clip is None:
            raise ValueError(f"entry {entry}: the donor has no clip {cid}")
        if id(clip) not in parts:
            tracks = _joint_tracks(clip, len(part_of), track_of)
            parts[id(clip)] = split(Clip(tracks, clip.loop, clip.loop_start), part_of)
        for k, width in enumerate(streams):
            si, slot = entry_slot(k, entry)
            if not width:
                continue
            if slot >= len(_stream(host, si)):
                raise ValueError(f"entry {entry}: stream {si} has {len(_stream(host, si))} slots")
            out[si][slot] = parts[id(clip)][k]
    return fu.Anim(out)


def pelvis(
    clips: Iterable[Clip | None],
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


def lift(clips: Mapping[int, Clip], units: float, track: int) -> dict[int, Clip]:
    """`clips` with `units` (world units) added to `track`'s location Y in every clip.

    The whole body rises with the pelvis and its bob is kept; this moves a donor rig authored
    against another game's floor onto MHFU's, where the rest pose stands on the origin."""
    raw = quantize("loc", units)
    done: dict[int, Clip] = {}
    out: dict[int, Clip] = {}
    for key, clip in clips.items():
        if track >= len(clip.tracks):
            out[key] = clip
            continue
        if id(clip) not in done:
            tracks = list(clip.tracks)
            tracks[track] = Track([_lifted(c, raw) for c in tracks[track].channels])
            done[id(clip)] = Clip(tracks, clip.loop, clip.loop_start)
        out[key] = done[id(clip)]
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
    span = frames(clip)
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
