# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import struct

import pytest
from mhp_formats import FormatError
from mhp_formats.psp.ge import Command, DisplayList, Op, Prim, triangles
from mhp_formats.psp.vtype import BITS8, BITS16, FLOAT, NONE, VertexType

C = Command
STRIP, LIST, FAN = Prim.TRIANGLE_STRIP, Prim.TRIANGLES, Prim.TRIANGLE_FAN


def _vtype(index=NONE, **kw):
    return C(Op.VTYPE, VertexType(texture=BITS16, position=BITS16, index=index, **kw).to_word())


def _mem(commands, at=0x40, tail=b""):
    """A buffer with the list at `at`, junk before it, and `tail` after it."""
    body = DisplayList(commands).to_bytes()
    return b"\xee" * at + body + tail, at + len(body)


def test_command():
    c = Command.from_word(0x04040005)
    assert c == (Op.PRIM, 0x040005) == Command.prim(STRIP, 5)
    assert c.word == 0x04040005
    for bad in (Command(0x100), Command(1, 1 << 24), Command(-1)):
        with pytest.raises(ValueError):
            bad.word  # noqa: B018


def test_round_trip():
    cmds = [C(Op.ORIGIN), C(Op.BASE), C(Op.VADDR, 0x30), _vtype(), C(Op.FFACE)]
    cmds += [Command.prim(STRIP, 4), C(0x42, 0xABCDEF), C(Op.OFFSETADDR), C(Op.RET)]
    data, end = _mem(cmds, tail=struct.pack("<I", 0x04040004) * 3)
    dl = DisplayList.from_bytes(data, 0x40)
    assert dl.commands == cmds
    assert dl.to_bytes() == data[0x40:end]


def test_end_terminates():
    data, _ = _mem([C(Op.NOP), C(Op.FINISH), C(Op.END)], tail=b"\0\0\0\x0b")
    assert len(DisplayList.from_bytes(data, 0x40).commands) == 3


@pytest.mark.parametrize("data", [b"", bytes(8), b"\0\0\0\x0b"[:3], bytes(4) + b"\0\0\0"])
def test_no_terminator(data):
    with pytest.raises(FormatError):
        DisplayList.from_bytes(data)


def test_bad_offset():
    with pytest.raises(FormatError):
        DisplayList.from_bytes(bytes(8), 12)


@pytest.mark.parametrize(("index", "code"), [(BITS8, "B"), (BITS16, "H"), (FLOAT, "I")])
def test_indexed(index, code):
    cmds = [C(Op.ORIGIN), C(Op.BASE), C(Op.IADDR, 0x100), C(Op.VADDR, 0x80), _vtype(index)]
    cmds += [C(Op.FFACE), Command.prim(STRIP, 4), C(Op.FFACE, 1), Command.prim(LIST, 3)]
    cmds += [C(Op.OFFSETADDR), C(Op.RET)]
    data, end = _mem(cmds)
    data = bytearray(data.ljust(0x40 + 0x100, b"\0"))
    data += struct.pack(f"<7{code}", 0, 1, 2, 3, 3, 2, 4)
    draws = list(DisplayList.from_bytes(data, 0x40).draws(bytes(data), 0x40))
    assert [(d.command, d.prim, d.count, d.face_order) for d in draws] == [
        (6, STRIP, 4, 0),
        (8, LIST, 3, 1),
    ]
    size = struct.calcsize(code)
    assert [d.vertex_addr for d in draws] == [0xC0, 0xC0]
    assert [d.index_addr for d in draws] == [0x140, 0x140 + 4 * size]
    assert [d.indices for d in draws] == [(0, 1, 2, 3), (3, 2, 4)]
    assert [d.vertex_count for d in draws] == [4, 5]
    assert draws[1].triangles() == [(2, 3, 4)]


def test_unindexed_advances():
    vt = VertexType(normal=BITS8, position=BITS16, morph_count=2)
    cmds = [C(Op.ORIGIN), C(Op.VADDR, 0x20), C(Op.VTYPE, vt.to_word())]
    cmds += [Command.prim(STRIP, 4), Command.prim(STRIP, 3), C(Op.VADDR, 0x400)]
    cmds += [Command.prim(LIST, 3), C(Op.RET)]
    data, _ = _mem(cmds, at=0)
    draws = list(DisplayList.from_bytes(data).draws(data, 0))
    assert [d.vertex_addr for d in draws] == [0x20, 0x20 + 4 * 20, 0x400]
    assert all(d.index_addr is None for d in draws)
    assert draws[1].indices == (0, 1, 2)


def test_addressing():
    cmds = [C(Op.VADDR, 0x10), _vtype(), Command.prim(STRIP, 3)]  # no ORIGIN: absolute
    cmds += [C(Op.NOP), C(Op.ORIGIN), C(Op.VADDR, 0x10), Command.prim(STRIP, 3)]
    cmds += [C(Op.OFFSETADDR, 0x12), C(Op.VADDR, 0x10), Command.prim(STRIP, 3)]
    cmds += [C(Op.BASE, 0x050000), C(Op.VADDR, 0x10), Command.prim(STRIP, 3)]
    cmds += [C(Op.OFFSETADDR, 0xFFFFFF), C(Op.BASE, 0x0F0000), C(Op.VADDR, 0xFFFFFF)]
    cmds += [Command.prim(STRIP, 3), C(Op.RET)]
    data, _ = _mem(cmds, at=0x40)
    draws = list(DisplayList.from_bytes(data, 0x40).draws(data, 0x40))
    assert [d.vertex_addr for d in draws] == [
        0x10,
        0x40 + 4 * 4 + 0x10,
        0x1210,
        0x1210 + 0x05000000,
        (0xFFFFFF00 + 0x0FFFFFFF) & 0x0FFFFFFF,
    ]


@pytest.mark.parametrize(
    "cmds",
    [
        [Command.prim(STRIP, 3), C(Op.RET)],
        [_vtype(), Command.prim(STRIP, 3), C(Op.RET)],
        [C(Op.VADDR), _vtype(BITS8), Command.prim(STRIP, 3), C(Op.RET)],
        [C(Op.VADDR), C(Op.IADDR, 0x1000), _vtype(BITS8), Command.prim(STRIP, 3), C(Op.RET)],
        [C(Op.CALL, 0x100), C(Op.RET)],
        [C(Op.JUMP, 0x100), C(Op.RET)],
        [C(Op.BEZIER, 0x0202), C(Op.RET)],
    ],
)
def test_draws_refuse(cmds):
    data, _ = _mem(cmds, at=0)
    with pytest.raises(FormatError):
        list(DisplayList.from_bytes(data).draws(data, 0))


def test_triangles():
    five = [10, 11, 12, 13, 14]
    assert triangles(STRIP, five) == [(10, 11, 12), (12, 11, 13), (12, 13, 14)]
    assert triangles(STRIP, five, 1) == [(11, 10, 12), (11, 12, 13), (13, 12, 14)]
    seven = [*five, 15, 16]
    assert triangles(LIST, seven) == [(10, 11, 12), (13, 14, 15)]
    assert triangles(LIST, seven, 1) == [(11, 10, 12), (14, 13, 15)]
    assert triangles(FAN, five) == [(10, 11, 12), (10, 12, 13), (10, 13, 14)]
    assert triangles(FAN, five, 1) == [(11, 10, 12), (12, 10, 13), (13, 10, 14)]
    assert triangles(STRIP, [1, 1, 2, 3]) == [(1, 1, 2), (2, 1, 3)]
    for prim in (Prim.POINTS, Prim.LINES, Prim.LINE_STRIP, Prim.RECTANGLES):
        assert triangles(prim, five) == []
    assert triangles(STRIP, [1, 2]) == triangles(LIST, [1, 2]) == []
    with pytest.raises(FormatError):
        triangles(7, five)


def _area(points, tri):
    (ax, ay), (bx, by), (cx, cy) = (points[i] for i in tri)
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def test_winding():
    zigzag = [(i // 2, i % 2) for i in range(9)]  # a strip of quads along x
    signs = {_area(zigzag, t) > 0 for t in triangles(STRIP, range(9))}
    assert len(signs) == 1
    flipped = {_area(zigzag, t) > 0 for t in triangles(STRIP, range(9), 1)}
    assert flipped == {not s for s in signs}
    wheel = [(0, 0), (2, 0), (1, 1), (-1, 1), (-2, 0), (-1, -1)]
    assert len({_area(wheel, t) > 0 for t in triangles(FAN, range(6))}) == 1
    square = [(0, 0), (0, 1), (1, 0), (1, 1)]  # a list keeps each triangle as given
    assert len({_area(square, t) > 0 for t in triangles(LIST, [0, 1, 2, 2, 1, 3])}) == 1
