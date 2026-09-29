# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import hashlib
from collections.abc import Sequence

import pytest
from mhfu_port import motion
from mhfu_port.data import Data
from mhfu_port.fk import rig_clip
from mhp_formats import fu
from mhp_formats.anim import Channel, Clip, Keyframe, Track
from mhp_formats.pac import Pac
from mhp_formats.skeleton import Bone, Skeleton

ROT = (0x008, 0x010, 0x020)
LOC_Y = 0x080
SCALE = (0x200, 0x400, 0x800)
BODY = (0, 2, 4)


def track(*bits: int, value: int = 1, frames: tuple[int, ...] = (0, 10)) -> Track:
    return Track([Channel(bit, [Keyframe(value, f) for f in frames]) for bit in bits])


def clip(n: int, frames: tuple[int, ...] = (0, 10), loop_start: float = 0.0) -> Clip:
    """`n` tracks, track `i` keyed at value `i` so it can be followed."""
    return Clip([track(*ROT, value=i, frames=frames) for i in range(n)], 1, loop_start)


def host(slots: dict[int, set[int]], count: int = 8) -> fu.Anim:
    """Six streams of `count` slots; `slots` fills a stream's slots with a placeholder."""
    streams: list[list[Clip | None]] = [[None] * count for _ in range(6)]
    for si, filled in slots.items():
        for slot in filled:
            streams[si][slot] = Clip()
    return fu.Anim(streams)


def whole(anim: fu.Anim, parts: Sequence[int], slot: int = 0) -> Clip:
    """The slot's clip over a rig whose parts have these widths."""
    skeleton = Skeleton([Bone(stream=k) for k, width in enumerate(parts) for _ in range(width)])
    out = rig_clip(anim, slot, skeleton)
    assert out is not None
    return out


def test_split():
    out = motion.build([clip(4)], host({si: {0} for si in BODY}), [2, 1, 1])
    assert [len(s[0].tracks) if s[0] else None for s in out.streams] == [2, None, 1, None, 1, None]
    assert whole(out, [2, 1, 1]) == Clip(clip(4).tracks, 1, 0.0)


def test_map_and_rest():
    donor = clip(3, frames=(0, 7, 40))
    out = motion.build([donor], host({si: {0} for si in BODY}), [2, 2, 1], {1: 2, 2: 0, 3: None})
    tracks = whole(out, [2, 2, 1]).tracks
    assert [t.channels[0].keyframes[0].value for t in tracks] == [0, 2, 0, 0, 0]
    assert tracks[1] == donor.tracks[2] and tracks[2] == donor.tracks[0]
    assert tracks[0] == tracks[3] == tracks[4] == motion.rest(40)
    assert tracks[4].mask == sum(ROT)


def test_positional_pads():
    tracks = whole(motion.build([clip(2)], host({0: {0}}), [3]), [3]).tracks
    assert tracks[2] == motion.rest(10)


def test_rest_span_without_keys():
    empty = Clip([Track()])
    tracks = whole(motion.build([empty], host({0: {0}}), [2]), [2]).tracks
    assert tracks == [Track(), motion.rest(2)]


def test_channels():
    donor = Clip(
        [Track([Channel(b, [Keyframe(0, 5)]) for b in (0x100, *SCALE, 0x040, 0x008, 0x001)])]
    )
    tracks = whole(motion.build([donor], host({0: {0}}), [1]), [1]).tracks
    assert [c.bit for c in tracks[0].channels] == [0x001, 0x008, 0x040, 0x100]


def test_slots():
    a, b, c = clip(1), clip(1), clip(1)
    out = motion.build([None, a, b, None, None, c], host({0: {0, 2, 3}}), [1])
    s = out.streams[0]
    assert s[0] is s[3] and s[0] is not None and s[0].tracks == a.tracks
    assert s[2] is not None and s[2].tracks == b.tracks
    assert s[1] is s[5] is None
    assert len(s) == 8 and len(out.streams) == 6


def test_filler_shares_one_copy():
    one = motion.build([clip(1)], host({0: {0}}), [1]).to_bytes()
    three = motion.build([clip(1)], host({0: {0, 1, 2}}), [1]).to_bytes()
    assert len(three) == len(one)


def test_host_slot_counts():
    streams: list[list[Clip | None]] = [[Clip()] * 3, [], [None] * 5]
    out = motion.build([clip(1)], fu.Anim(streams), [1])
    assert [len(s) for s in out.streams] == [3, 0, 5]


def test_loop_start():
    out = motion.build([clip(2, loop_start=12.5)], host({si: {0} for si in BODY}), [1, 1, 0])
    assert [s[0].loop_start for s in out.streams if s[0]] == [12.5, 12.5]
    assert all(s[0] is None for s in out.streams[4:])
    again = fu.Anim.from_bytes(out.to_bytes())
    assert whole(again, [1, 1]) == Clip(clip(2).tracks, 1, 12.5)


def test_errors():
    with pytest.raises(ValueError, match="part 2 plays stream 4"):
        motion.build([clip(3)], host({0: {0}, 2: {0}}), [1, 1, 1])
    with pytest.raises(ValueError, match="stream 6"):
        motion.build([clip(4)], host({si: {0} for si in BODY}), [1, 1, 1, 1])
    with pytest.raises(ValueError, match="no clips"):
        motion.build([None], host({0: {0}}), [1])


def test_pelvis():
    def body(heights: list[int]) -> Clip:
        return Clip([track(LOC_Y, value=h) if h else track(*ROT) for h in heights])

    clips = [None, body([0, 50, 300]), body([0, 40, 310])]
    assert motion.pelvis(clips) == 2
    assert motion.pelvis(clips, {1: 1, 2: 2}, [-1, 0, 1]) == 1
    assert motion.pelvis(clips, {2: 1}, [-1, 0]) == 2
    assert motion.pelvis([body([0, 0])]) is None


def test_lift():
    donor = Clip([track(*ROT), track(0x040, LOC_Y, value=100), track(LOC_Y, value=0x7FF0)])
    lifted = motion.lift([donor, None, donor], 2.5, 1)
    assert lifted[0] is lifted[2] and lifted[1] is None
    assert lifted[0] is not None
    moved = lifted[0].tracks[1].channels
    assert [k.value for k in moved[1].keyframes] == [140, 140]
    assert moved[0] == donor.tracks[1].channels[0]
    assert donor.tracks[1].channels[1].keyframes[0].value == 100
    top = motion.lift([donor], 2.5, 2)[0]
    assert top is not None and top.tracks[2].channels[0].keyframes[0].value == 0x7FFF
    assert motion.lift([donor], 1.0, 9) == [donor]


def _host(data: Data) -> fu.Anim:
    return fu.Anim.from_bytes(Pac.from_bytes(data.fu.read(6185)).entries[3])


@pytest.mark.parametrize(
    ("anim", "split", "loop_starts", "fillers", "digest"),
    [
        (5250, [31, 9, 4], 8, 20, "e5a62bc890f31ab2"),
        (5341, [33, 6, 7], 5, 30, "f2603e8b88020c56"),
    ],
)
def test_donor(data, anim, split, loop_starts, fillers, digest):
    h = _host(data)
    clips = motion.moveset(data.p3rd.read(anim))
    out = motion.build(clips, h, split)
    blob = out.to_bytes()
    assert [i for i, st in enumerate(out.streams) if any(c is not None for c in st)] == list(BODY)
    for si, width in zip(BODY, split, strict=True):
        filled = [c for c in out.streams[si] if c is not None]
        assert len(filled) == sum(c is not None for c in h.streams[si])
        assert {len(c.tracks) for c in filled} == {width}
        assert not any(ch.bit in SCALE for c in filled for t in c.tracks for ch in t.channels)
        assert len({id(c) for c in filled if c.loop_start}) == loop_starts
    s0 = out.streams[0]
    lowest = next(i for i, c in enumerate(clips) if c is not None)
    filler = [i for i, c in enumerate(s0) if c and (i >= len(clips) or clips[i] is None)]
    assert len(filler) == fillers and all(s0[i] is s0[lowest] for i in filler)
    again = fu.Anim.from_bytes(blob)
    skeleton = Skeleton([Bone(stream=k) for k, width in enumerate(split) for _ in range(width)])
    flat = [rig_clip(again, slot, skeleton) for slot in range(len(again.streams[0]))]
    assert sum(c is not None for c in flat) == 64
    assert {len(c.tracks) for c in flat if c} == {sum(split)}
    assert hashlib.sha256(blob).hexdigest()[:16] == digest


def test_zinogre_lift(data):
    clips = motion.moveset(data.p3rd.read(5341))
    track = motion.pelvis(clips)
    assert track is not None
    lifted = motion.lift(clips, 165.3, track)
    for a, b in zip(clips, lifted, strict=True):
        if a is None or b is None:
            continue
        for ca, cb in zip(a.tracks[track].channels, b.tracks[track].channels, strict=True):
            step = 2645 if ca.bit == LOC_Y else 0
            moved = {kb.value - ka.value for ka, kb in zip(ca.keyframes, cb.keyframes, strict=True)}
            assert moved == {step}


def test_donor_slot():
    c = Clip([])
    assert [motion.donor_slot([None, c, None, c], s) for s in range(5)] == [1, 1, 1, 3, 1]
    with pytest.raises(ValueError):
        motion.donor_slot([None], 0)
