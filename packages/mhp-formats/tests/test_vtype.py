# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import itertools
import random
import struct

import pytest
from mhp_formats import FormatError
from mhp_formats.psp.vtype import (
    BITS8,
    BITS16,
    COLOR_4444,
    COLOR_5551,
    COLOR_5650,
    COLOR_8888,
    FLOAT,
    NONE,
    VertexType,
    Vertices,
    pack_color,
    unpack_color,
)

VT = VertexType


def _offsets(vt):
    lay = vt.layout
    return tuple(
        None if f is None else (f.offset, f.codes)
        for f in (lay.weight, lay.texture, lay.color, lay.normal, lay.position)
    ) + (lay.size,)


# (vertex type, (weight, texture, colour, normal, position) as (offset, codes), vertex size)
LAYOUTS = [
    (VT(position=BITS8), (None, None, None, None, (0, "bbb"), 3)),
    (VT(position=BITS16), (None, None, None, None, (0, "hhh"), 6)),
    (VT(position=FLOAT), (None, None, None, None, (0, "fff"), 12)),
    (VT(position=NONE), (None, None, None, None, (0, "BBB"), 3)),
    (VT(texture=BITS8, position=BITS16), (None, (0, "BB"), None, None, (2, "hhh"), 8)),
    (
        VT(texture=BITS8, normal=BITS8, position=BITS8),
        (None, (0, "BB"), None, (2, "bbb"), (5, "bbb"), 8),
    ),
    (
        VT(weight=BITS8, weight_count=3, texture=BITS16, normal=BITS8, position=BITS16),
        ((0, "BBB"), (4, "HH"), None, (8, "bbb"), (12, "hhh"), 18),
    ),
    (
        VT(weight=BITS16, texture=FLOAT, color=COLOR_8888, normal=BITS16, position=FLOAT),
        ((0, "H"), (4, "ff"), (12, "I"), (16, "hhh"), (24, "fff"), 36),
    ),
    (VT(color=COLOR_5650, position=BITS8), (None, None, (0, "H"), None, (2, "bbb"), 6)),
    (VT(color=COLOR_8888, position=BITS8), (None, None, (0, "I"), None, (4, "bbb"), 8)),
    (
        VT(weight=BITS8, weight_count=8, position=FLOAT),
        ((0, "B" * 8), None, None, None, (8, "fff"), 20),
    ),
    (
        VT(weight=FLOAT, weight_count=2, position=BITS8),
        ((0, "ff"), None, None, None, (8, "bbb"), 12),
    ),
    (
        VT(texture=BITS16, color=COLOR_4444, position=BITS16),
        (None, (0, "HH"), (4, "H"), None, (6, "hhh"), 12),
    ),
    (VT(normal=BITS8, position=BITS16), (None, None, None, (0, "bbb"), (4, "hhh"), 10)),
    (VT(color=2, position=BITS16), (None, None, None, None, (0, "hhh"), 6)),
    (VT(position=BITS16, through=True), (None, None, None, None, (0, "hhH"), 6)),
    (VT(position=BITS8, through=True), (None, None, None, None, (0, "bbB"), 3)),
]


@pytest.mark.parametrize(("vt", "expected"), LAYOUTS)
def test_layout(vt, expected):
    assert _offsets(vt) == expected
    assert VertexType.from_word(vt.to_word()) == vt


def test_morph_stride():
    vt = VT(normal=BITS8, position=BITS16, morph_count=3)
    assert (vt.layout.size, vt.stride) == (10, 30)


@pytest.mark.parametrize(
    ("index", "code", "size"), [(NONE, "", 0), (BITS8, "B", 1), (BITS16, "H", 2), (FLOAT, "I", 4)]
)
def test_index(index, code, size):
    vt = VT(index=index)
    assert (vt.index_code, vt.index_size) == (code, size)


def test_word_fields():
    word = 2 | 6 << 2 | 1 << 5 | 2 << 7 | 1 << 9 | 2 << 11 | 3 << 14 | 1 << 18 | 1 << 23 | 1 << 17
    vt = VertexType.from_word(word)
    assert (vt.texture, vt.color, vt.normal, vt.position, vt.weight, vt.index) == (2, 6, 1, 2, 1, 2)
    assert (vt.weight_count, vt.morph_count, vt.through, vt.spare) == (4, 2, True, 1 << 17)
    assert vt.to_word() == word


def test_word_round_trip():
    rnd = random.Random(0)
    for word in [0, 0xFFFFFF, *(rnd.randrange(1 << 24) for _ in range(2000))]:
        assert VertexType.from_word(word).to_word() == word


@pytest.mark.parametrize("word", [-1, 1 << 24])
def test_word_rejects(word):
    with pytest.raises(FormatError):
        VertexType.from_word(word)


@pytest.mark.parametrize(
    "kwargs",
    [{"texture": 4}, {"color": 8}, {"weight_count": 0}, {"morph_count": 9}, {"spare": 1}],
)
def test_field_rejects(kwargs):
    with pytest.raises(ValueError):
        VT(**kwargs)


_RANGES = {"b": (-128, 127), "B": (0, 255), "h": (-32768, 32767), "H": (0, 65535)}


def _random_row(rnd, codes):
    row = []
    for code in codes:
        if code == "f":
            row.append(struct.unpack("<f", struct.pack("<f", rnd.uniform(-4, 4)))[0])
        elif code == "I":
            row.append(rnd.randrange(1 << 32))
        else:
            row.append(rnd.randint(*_RANGES[code]))
    return tuple(row)


def _types():
    for tc, col, nrm, pos, wt, thr in itertools.product(
        range(4), (0, 4, 5, 6, 7), range(4), range(4), range(4), (False, True)
    ):
        yield VT(tc, col, nrm, pos, wt, 0, 3 if wt else 1, 1, thr)


def test_vertices_round_trip():
    rnd = random.Random(1)
    for vt in _types():
        lay = vt.layout
        v = Vertices(vt)
        for name in ("weight", "texture", "normal", "position"):
            fld = getattr(lay, name)
            if fld is not None:
                setattr(v, name, [_random_row(rnd, fld.codes) for _ in range(5)])
        if lay.color is not None:
            v.color = [_random_row(rnd, lay.color.codes)[0] for _ in range(5)]
        data = v.to_bytes()
        assert len(data) == 5 * vt.stride
        assert Vertices.from_bytes(b"xx" + data, vt, 5, 2) == v


def test_raw_bytes_round_trip():
    rnd = random.Random(2)
    for vt in _types():
        if FLOAT in (vt.texture, vt.normal, vt.position, vt.weight):
            continue  # random bytes may be signalling NaNs
        data = rnd.randbytes(7 * vt.stride)
        assert Vertices.from_bytes(data, vt, 7).to_bytes() == data


def test_padding():
    vt = VT(weight=BITS8, position=BITS16)  # one alignment byte after the weight
    zero = bytes([9, 0, 1, 0, 2, 0, 3, 0])
    assert Vertices.from_bytes(zero, vt, 1).padding == b""
    dirty = bytes([9, 7, 1, 0, 2, 0, 3, 0]) * 2
    v = Vertices.from_bytes(dirty, vt, 2)
    assert v.padding == b"\x07\x07"
    assert v.to_bytes() == dirty
    v.padding = b"\x07"
    with pytest.raises(ValueError):
        v.to_bytes()


def test_morph():
    vt = VT(position=BITS8, morph_count=2)
    data = bytes(range(18))
    v = Vertices.from_bytes(data, vt, 3)
    assert len(v) == 3
    assert v.position[1] == (3, 4, 5)
    assert v.to_bytes() == data


def test_signalling_nan():
    vt = VT(position=FLOAT)
    data = struct.pack("<3I", 0x7F800001, 0, 0)
    with pytest.raises(FormatError):
        Vertices.from_bytes(data, vt, 1)


def test_rejects():
    with pytest.raises(FormatError):
        Vertices.from_bytes(bytes(11), VT(position=BITS16), 2)
    v = Vertices(VT(position=BITS16, normal=BITS8), position=[(0, 0, 0)])
    with pytest.raises(ValueError):
        v.to_bytes()
    v = Vertices(VT(position=BITS8), position=[(0, 0, 999)])
    with pytest.raises(ValueError):
        v.to_bytes()


def test_views():
    vt = VT(weight=BITS8, weight_count=2, texture=BITS16, normal=BITS8, position=BITS16)
    v = Vertices(vt, [(128, 64)], [(32768, 16384)], [], [(-128, 64, 0)], [(16384, -32768, 0)])
    assert v.positions((2.0, 4.0, 8.0)) == [(1.0, -4.0, 0.0)]
    assert v.normals() == [(-1.0, 0.5, 0.0)]
    assert v.uvs() == [(1.0, 0.5)]
    assert v.weights() == [(1.0, 0.5)]
    v8 = Vertices(VT(texture=BITS8, position=BITS8), texture=[(64, 255)], position=[(64, 0, -128)])
    assert v8.positions() == [(0.5, 0.0, -1.0)]
    assert v8.uvs() == [(0.5, 255 / 128)]
    vf = Vertices(VT(weight=FLOAT, texture=FLOAT, normal=FLOAT, position=FLOAT), [(1.5,)])
    vf.texture, vf.normal, vf.position = [(0.25, 3.0)], [(0, 1, 0)], [(1, 2, 3)]
    assert vf.positions((2, 2, 2)) == [(2, 4, 6)]
    assert (vf.weights(), vf.uvs(), vf.normals()) == ([(1.5,)], [(0.25, 3.0)], [(0, 1, 0)])
    assert Vertices(VT(position=BITS16)).normals() == []
    with pytest.raises(FormatError):
        Vertices(VT(position=NONE), position=[(0, 0, 0)]).positions()


def test_through_views():
    t16 = Vertices(VT(texture=BITS16, position=BITS16, through=True))
    t16.texture, t16.position = [(300, 7)], [(-5, 480, 65535)]
    assert t16.uvs() == [(300.0, 7.0)]
    assert t16.positions() == [(-5, 480, 65535)]
    assert Vertices.from_bytes(t16.to_bytes(), t16.vtype, 1) == t16
    t8 = Vertices(VT(texture=BITS8, position=BITS8, through=True))
    t8.texture, t8.position = [(64, 128)], [(5, -5, 200)]
    assert t8.positions() == [(0.0, 0.0, 0.0)]
    assert t8.uvs() == [(0.5, 1.0)]
    tf = Vertices(VT(position=FLOAT, through=True), position=[(1.5, 2.0, -3.0), (0, 0, 7e4)])
    assert tf.positions() == [(1.5, 2.0, 0.0), (0, 0, 65535.0)]


@pytest.mark.parametrize(
    ("fmt", "value", "rgba"),
    [
        (COLOR_5650, 0xFFFF, (255, 255, 255, 255)),
        (COLOR_5650, 0x001F, (255, 0, 0, 255)),
        (COLOR_5650, 0x07E0, (0, 255, 0, 255)),
        (COLOR_5650, 0x0821, (8, 4, 8, 255)),
        (COLOR_5551, 0x8000, (0, 0, 0, 255)),
        (COLOR_5551, 0x7C00, (0, 0, 255, 0)),
        (COLOR_4444, 0x1234, (0x44, 0x33, 0x22, 0x11)),
        (COLOR_8888, 0x11223344, (0x44, 0x33, 0x22, 0x11)),
    ],
)
def test_color(fmt, value, rgba):
    assert unpack_color(value, fmt) == rgba
    assert pack_color(rgba, fmt) == value


@pytest.mark.parametrize("fmt", [COLOR_5650, COLOR_5551, COLOR_4444])
def test_color_round_trip(fmt):
    for value in range(1 << 16):
        assert pack_color(unpack_color(value, fmt), fmt) == value


def test_colors_view():
    v = Vertices(VT(color=COLOR_4444, position=BITS8), color=[0xF00F], position=[(0, 0, 0)])
    assert v.colors() == [(255, 0, 0, 255)]
    with pytest.raises(ValueError):
        unpack_color(0, 3)
    with pytest.raises(ValueError):
        pack_color((0, 0, 0, 0), 1)
