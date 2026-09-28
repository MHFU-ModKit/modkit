# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import math
import struct

import pytest
from mhp_formats import FormatError
from mhp_formats.anim import (
    CHANNEL_BITS,
    EMPTY,
    AnimPack,
    Channel,
    Clip,
    Keyframe,
    Track,
    channel_kind,
    dequantize,
    quantize,
    read_keyframes,
    write_keyframes,
)
from mhp_formats.fu.anim import Anim as FuAnim
from mhp_formats.p3rd.anim import Anim as P3rdAnim


class _Word(AnimPack):
    """A clip that is just its loop word, to test the container alone."""

    @classmethod
    def _read_clip(cls, data, off):
        return Clip(loop=int.from_bytes(data[off : off + 4], "little")), off + 4

    @staticmethod
    def _write_clip(clip, out):
        out += clip.loop.to_bytes(4, "little")


def _words(data):
    return list(struct.unpack(f"<{len(data) // 4}I", data))


def test_units():
    assert dequantize("rot", 4096) == pytest.approx(math.pi / 2)
    assert dequantize("rot", -8192) == pytest.approx(-math.pi)
    assert dequantize("loc", 16) == 1.0
    assert dequantize("scl", 256) == 1.0
    assert quantize("rot", math.pi / 4) == 2048
    assert quantize("loc", -2.5) == -40
    assert quantize("scl", 0.5) == 128


@pytest.mark.parametrize("kind", ["rot", "loc", "scl"])
def test_quantize_inverts(kind):
    assert all(quantize(kind, dequantize(kind, raw)) == raw for raw in range(-0x8000, 0x8000))


@pytest.mark.parametrize(
    ("kind", "value", "raw"),
    [
        ("loc", 2047.9375, 0x7FFF),
        ("loc", 2047.96875, 0x7FFF),
        ("loc", 5000.0, 0x7FFF),
        ("loc", -2048.0, -0x8000),
        ("loc", -2048.03125, -0x8000),
        ("scl", -1e9, -0x8000),
        ("rot", 4 * math.pi, 0x7FFF),
        ("rot", -math.pi, -0x2000),
        ("loc", 0.03125, 0),
        ("loc", 0.09375, 2),
    ],
)
def test_quantize_edges(kind, value, raw):
    assert quantize(kind, value) == raw


def test_channel_kind():
    assert [channel_kind(1 << i) for i in range(3, 12)] == list(CHANNEL_BITS.values())
    assert channel_kind(0x10) == ("rot", 1)
    assert channel_kind(0x100) == ("loc", 2)
    assert channel_kind(0x1) is None
    assert channel_kind(0x18) is None


def test_mask():
    assert Track().mask == 0
    assert Track([Channel(0x8), Channel(0x40), Channel(0x100)]).mask == 0x148


def test_keyframes():
    keys = [Keyframe(-0x8000, 0, 1, -1), Keyframe(0x7FFF, 90)]
    data = write_keyframes(keys)
    assert data == struct.pack("<8h", -0x8000, 0, 1, -1, 0x7FFF, 90, 0, 0)
    assert read_keyframes(memoryview(data)) == keys
    assert write_keyframes([]) == b""
    with pytest.raises(ValueError):
        write_keyframes([Keyframe(0x8000, 0)])


def test_layout():
    shared, other = Clip(loop=7), Clip(loop=9)
    pack = _Word([[shared, None, shared], [], [None, other]], tail=b"pad")
    data = pack.to_bytes()
    words = _words(data[:-3])
    assert words[:8] == [3, 0x20, 0, 0x2C, 2, 0x2C, 0, 0x34]
    assert words[8:] == [0x34, EMPTY, 0x34, EMPTY, 0x38, 7, 9]
    assert data.endswith(b"pad")
    back = _Word.from_bytes(data)
    assert back == pack
    assert back.streams[0][0] is back.streams[0][2]


def test_equal_clips_stay_apart():
    data = _Word([[Clip(loop=1), Clip(loop=1)]]).to_bytes()
    assert _words(data) == [2, 0x10, 0, 0x18, 0x18, 0x1C, 1, 1]


def test_empty_pack():
    pack = _Word([[None] * 4])
    assert _Word.from_bytes(pack.to_bytes()) == pack
    assert FuAnim.sniff(pack.to_bytes())
    assert P3rdAnim.sniff(pack.to_bytes())
    with pytest.raises(ValueError):
        _Word([]).to_bytes()


def _pack(*words):
    return struct.pack(f"<{len(words)}I", *words)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        _pack(1, 0x10, 0, 0x14),  # table runs past the data
        _pack(1, 0x10, 0, 0x18, EMPTY),  # end of tables is wrong
        _pack(1, 0x10, 1, 0x14, EMPTY),  # the word before it is not 0
        _pack(1, 0x0C, 0, 0x10),  # header size not a multiple of 8
        _pack(1, 0x18, 1, 0x20, 0, 0x20, EMPTY, EMPTY),  # second table does not follow the first
    ],
)
def test_rejects_header(data):
    assert not FuAnim.sniff(data)
    assert not P3rdAnim.sniff(data)
    with pytest.raises(FormatError):
        _Word.from_bytes(data)


@pytest.mark.parametrize(
    "data",
    [
        _pack(1, 0x10, 0, 0x14, 0x18, 5, 6),  # a gap before the clip
        _pack(2, 0x10, 0, 0x18, 0x1C, 0x18, 5, 6),  # clips out of slot order
        _pack(1, 0x10, 0, 0x14, 0x40, 5),  # pointer past the data
    ],
)
def test_rejects_placement(data):
    with pytest.raises(FormatError):
        _Word.from_bytes(data)
