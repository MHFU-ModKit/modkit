# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import struct
from pathlib import Path

import pytest
from mhp_formats import FormatError
from mhp_formats.anim import Channel, Clip, Keyframe, Track
from mhp_formats.fu.anim import Anim as FuAnim
from mhp_formats.p3rd.anim import Anim
from mhp_formats.pac import Pac


def _clip(bones=3):
    tracks = [Track([Channel(0x40, [Keyframe(44, 0), Keyframe(50, 90)])])]
    tracks += [Track([Channel(b, [Keyframe(-0x8000, 0, 1, 2)]) for b in (0x20, 8)])] * (bones - 2)
    return Clip([Track(), *tracks], loop=1, loop_start=56.0)


def test_clip_layout():
    data = Anim([[_clip()]]).to_bytes()
    assert struct.unpack_from("<3If", data, 0x14) == (3, len(data) - 0x14, 1, 56.0)
    assert struct.unpack_from("<2H", data, 0x24) == (0, 4)  # an empty track
    assert struct.unpack_from("<2H2HI4h", data, 0x28) == (1, 28, 0x40, 2, 24, 44, 0, 0, 0)
    assert struct.unpack_from("<2H2HI", data, 0x44) == (2, 36, 0x20, 1, 16)
    assert len(data) == 0x68
    assert Anim.from_bytes(data) == Anim([[_clip()]])
    assert Anim.sniff(data)
    assert not FuAnim.sniff(data)


def test_channel_order_kept():
    clip = Clip([Track([Channel(b, [Keyframe(b, 0)]) for b in (0x40, 8, 0x800, 1)])])
    assert Anim.from_bytes(Anim([[clip]]).to_bytes()).streams[0][0] == clip


def test_slot_zero():
    # the first slot table starts right at the header's end; its slot 0 is a slot like any other
    pack = Anim([[_clip(), None, None, _clip(), None, _clip()]])
    data = pack.to_bytes()
    assert struct.unpack_from("<5I", data) == (6, 0x10, 0, 0x28, 0x28)
    assert [i for i, c in enumerate(Anim.from_bytes(data).streams[0]) if c] == [0, 3, 5]


def test_clip_sets():
    # MHP3rd's streams are separate clip sets, each over the whole rig: never one merged table
    idle, roar = _clip(), _clip(4)
    pack = Anim([[None, idle] + [None] * 5, [roar, None], []])
    data = pack.to_bytes()
    assert struct.unpack_from("<8I", data) == (7, 0x20, 2, 0x3C, 0, 0x44, 0, 0x44)
    back = Anim.from_bytes(data)
    assert [len(s) for s in back.streams] == [7, 2, 0]
    assert back.streams[0][1] == idle
    assert back.streams[1][0] == roar


def _data():
    return bytearray(Anim([[_clip()]]).to_bytes())


def _poke(fmt, offset, *values):
    data = _data()
    struct.pack_into(fmt, data, offset, *values)
    return bytes(data)


@pytest.mark.parametrize(
    "data",
    [
        _poke("<I", 0x14, 0xFFFFFFFF),  # a garbage track count stops at the clip's end
        _poke("<I", 0x18, 0x1000),  # clip size past the data
        _poke("<H", 0x26, 0),  # a track that would not advance
        _poke("<H", 0x2A, 0x10),  # track size cuts its channel
        _poke("<I", 0x30, 16),  # channel size disagrees with its keyframe count
        bytes(_data()[:0x40]),  # truncated
    ],
)
def test_rejects(data):
    with pytest.raises(FormatError):
        Anim.from_bytes(data)


@pytest.mark.parametrize(
    "clip",
    [
        Clip([Track([Channel(0x10000)])]),
        Clip([Track([Channel(8, [Keyframe(0, 0)] * 0x10000)])]),
        Clip([Track([Channel(8, [Keyframe(0, 0)] * 0x1000)] * 2)]),  # track over 64 KiB
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


def test_round_trip_mhp3rd(mhp3rd_data):
    checked = slot_zero = 0
    for name, data in _blobs(mhp3rd_data):
        if Anim.sniff(data):
            pack = Anim.from_bytes(data)
            assert pack.to_bytes() == data, name
            assert not FuAnim.sniff(data), name
            checked += 1
            slot_zero += pack.streams[0][:1] != [None]
    assert checked >= 115
    # a reading that starts the first table one word late loses these clips
    assert slot_zero == 4


def _pack(data_dir, number, entry=None):
    data = min(data_dir.glob(f"file_{number:05d}.*")).read_bytes()
    return Anim.from_bytes(data if entry is None else Pac.from_bytes(data).entries[entry])


def test_brute(mhp3rd_data):
    streams = _pack(mhp3rd_data, 5250).streams
    assert [len(s) for s in streams] == [70, 20, 0]
    moveset = [i for i, c in enumerate(streams[0]) if c]
    assert (len(moveset), moveset[0], moveset[-1]) == (58, 1, 65)
    assert {len(c.tracks) for s in streams for c in s if c} == {43}


def test_quest_packs(mhp3rd_data):
    pack = _pack(mhp3rd_data, 4016, 3)
    assert [len(s) for s in pack.streams] == [53, 48]
    assert [sum(c is not None for c in s) for s in pack.streams] == [12, 27]
    clips = filter(None, pack.streams[0])
    assert sum(len(ch.keyframes) for c in clips for t in c.tracks for ch in t.channels) > 500
    pack = _pack(mhp3rd_data, 4000, 3)
    assert [len(s) for s in pack.streams] == [5, 0]
    for clip in filter(None, pack.streams[0]):
        for channel in (ch for t in clip.tracks for ch in t.channels):
            frames = [k.frame for k in channel.keyframes]
            assert frames == sorted(set(frames))
