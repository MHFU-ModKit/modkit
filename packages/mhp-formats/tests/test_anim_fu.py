# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import struct
from pathlib import Path

import pytest
from mhp_formats import FormatError
from mhp_formats.anim import EMPTY, Channel, Clip, Keyframe, Track
from mhp_formats.fu.anim import CHANNEL_TAG, CLIP_TAG, TRACK_TAG, Anim
from mhp_formats.p3rd.anim import Anim as P3rdAnim
from mhp_formats.pac import Pac


def _clip():
    keys = [Keyframe(-0x8000, 0, 3, -3), Keyframe(0x7FFF, 40)]
    rot = Track([Channel(bit, keys) for bit in (8, 16, 32)])
    loc = Track([Channel(0x40, [Keyframe(5, 0)]), Channel(0x100)])
    return Clip([Track(), rot, loc], loop=1, loop_start=12.5)


def test_clip_layout():
    data = Anim([[_clip()]]).to_bytes()
    words = struct.unpack_from("<4If", data, 0x14)
    assert words == (CLIP_TAG, 3, len(data) - 0x14, 1, 12.5)
    assert struct.unpack_from("<3I", data, 0x28) == (TRACK_TAG, 0, 12)
    assert struct.unpack_from("<3I", data, 0x34) == (TRACK_TAG | 0x38, 3, 12 + 3 * 28)
    assert struct.unpack_from("<3I4h", data, 0x40) == (CHANNEL_TAG | 8, 2, 28, -0x8000, 0, 3, -3)
    assert struct.unpack_from("<3I", data, 0x94) == (TRACK_TAG | 0x140, 2, 12 + 20 + 12)
    assert struct.unpack_from("<3I", data, 0xB4) == (CHANNEL_TAG | 0x100, 0, 12)
    assert len(data) == 0xC0
    assert Anim.from_bytes(data) == Anim([[_clip()]])


def test_every_bit():
    clip = Clip([Track([Channel(1 << i, [Keyframe(i, i)]) for i in range(12)])])
    data = Anim([[clip]]).to_bytes()
    assert Anim.from_bytes(data).streams[0][0] == clip
    assert struct.unpack_from("<I", data, 0x28)[0] == TRACK_TAG | 0xFFF


@pytest.mark.parametrize("counts", [[100, 100], [100, 100, 100, 100], [100, 105] * 3, [5] * 10])
def test_streams(counts):
    clip = _clip()
    pack = Anim([[clip if i % 3 == s else None for i in range(n)] for s, n in enumerate(counts)])
    data = pack.to_bytes()
    hsize = 8 * (len(counts) + 1)
    assert struct.unpack_from("<2I", data, 0) == (counts[0], hsize)
    assert struct.unpack_from("<I", data, hsize - 4)[0] == hsize + 4 * sum(counts)
    assert struct.unpack_from("<I", data, hsize)[0] == hsize + 4 * sum(counts)
    assert struct.unpack_from("<I", data, hsize + 4)[0] == EMPTY
    back = Anim.from_bytes(data)
    assert back == pack
    assert len({id(c) for s in back.streams for c in s if c}) == 1
    assert Anim.sniff(data)
    assert not P3rdAnim.sniff(data)


def _data():
    return bytearray(Anim([[_clip()]]).to_bytes())


def _poke(offset, value):
    data = _data()
    struct.pack_into("<I", data, offset, value)
    return bytes(data)


@pytest.mark.parametrize(
    "data",
    [
        _poke(0x14, 0x80000001),  # clip tag
        _poke(0x1C, 0x80),  # clip size short of its tracks
        _poke(0x1C, 0x1000),  # clip size past the data
        _poke(0x34, TRACK_TAG | 0x30),  # track tag disagrees with its channels
        _poke(0x3C, 0x10),  # track size cuts its first channel
        _poke(0x40, 0x80130008),  # channel tag
        _poke(0x48, 36),  # channel size disagrees with its keyframe count
        _poke(0x18, 9),  # more tracks than the clip holds
        bytes(_data()[:0x90]),  # truncated
    ],
)
def test_rejects(data):
    with pytest.raises(FormatError):
        Anim.from_bytes(data)


@pytest.mark.parametrize(
    "clip",
    [
        Clip([Track([Channel(0x1000)])]),
        Clip([Track([Channel(8, [Keyframe(0, 0x8000)])])]),
    ],
)
def test_unwritable(clip):
    with pytest.raises(ValueError):
        Anim([[clip]]).to_bytes()


def _walk(name, data):
    yield name, data
    if Pac.sniff(data):
        for i, entry in enumerate(Pac.from_bytes(data).entries):
            yield from _walk(f"{name}[{i}]", entry)


def _blobs(data_dir: Path):
    for path in sorted(data_dir.glob("file_*")):
        yield from _walk(path.name, path.read_bytes())


def test_round_trip_mhfu(mhfu_data):
    checked = 0
    for name, data in _blobs(mhfu_data):
        if Anim.sniff(data):
            assert Anim.from_bytes(data).to_bytes() == data, name
            assert not P3rdAnim.sniff(data), name
            checked += 1
    assert checked >= 120


def test_tigrex(mhfu_data):
    pac = Pac.from_bytes(min(mhfu_data.glob("file_06185.*")).read_bytes())
    streams = Anim.from_bytes(pac.entries[3]).streams
    assert [sum(c is not None for c in s) for s in streams] == [62, 0, 64, 0, 62, 0]
    assert [{len(c.tracks) for c in s if c} for s in streams[::2]] == [{31}, {9}, {5}]
    # body, head and tail play a slot together: body and tail hold the same slots
    empty = [{i for i, c in enumerate(s) if c is None} for s in streams[::2]]
    assert empty[0] == empty[2]
    assert len(empty[0]) == 38
    assert empty[0] ^ empty[1] == {24, 25}
