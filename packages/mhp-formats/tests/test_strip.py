# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import random
from collections import Counter

import pytest
from mhp_formats.psp.ge import Prim, triangles
from mhp_formats.psp.strip import Stripper


def _grid(w, h):
    """A w x h grid of quads, two triangles each, all wound alike."""
    tris = []
    for y in range(h):
        for x in range(w):
            a, b = y * (w + 1) + x, y * (w + 1) + x + 1
            c, d = a + w + 1, b + w + 1
            tris += [(a, c, b), (b, c, d)]
    return tris


def _canon(tri):
    i = tri.index(min(tri))
    return tri[i:] + tri[:i]


@pytest.mark.parametrize("face_order", [0, 1])
@pytest.mark.parametrize("cap", [3, 4, 5, 8, 1000])
def test_strips(cap, face_order):
    source = _grid(7, 5)
    rnd = random.Random(cap)
    rnd.shuffle(source)
    s = Stripper(source)
    drawn = []
    while len(s):
        strip = s.strip(cap, face_order)
        assert 3 <= len(strip) <= cap
        drawn += triangles(Prim.TRIANGLE_STRIP, strip, face_order)
    assert Counter(map(_canon, drawn)) == Counter(map(_canon, source))
    assert s.strip(cap) == []


def _fans(quads):
    """Quads as an OBJ writes them: (a, b, c), (a, c, d), each fanned from its first corner."""
    return [t for a, b, c, d in quads for t in ((a, b, c), (a, c, d))]


BOX = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]


@pytest.mark.parametrize("face_order", [0, 1])
def test_fanned_quads_join(face_order):
    source = _fans(BOX)
    s = Stripper(source)
    strips = []
    while len(s):
        strips.append(s.strip(4, face_order))
    assert len(strips) == 6 and all(len(st) == 4 for st in strips)
    drawn = [t for st in strips for t in triangles(Prim.TRIANGLE_STRIP, st, face_order)]
    assert Counter(map(_canon, drawn)) == Counter(map(_canon, source))


def test_rotation_only_when_it_helps():
    assert Stripper([(0, 1, 2)]).strip(10) == [0, 1, 2]
    assert Stripper([(0, 1, 2), (0, 2, 3)]).strip(10) in ([1, 2, 0, 3], [2, 0, 1, 3])


def test_strips_chain():
    s = Stripper(_grid(10, 1))
    assert len(s.strip(22)) == 22


@pytest.mark.parametrize("face_order", [0, 1])
def test_loose(face_order):
    source = _grid(3, 3)
    s = Stripper(source)
    drawn = []
    while len(s):
        got = s.loose(4, face_order)
        assert 1 <= len(got) <= 4
        drawn += triangles(Prim.TRIANGLES, [i for t in got for i in t], face_order)
    assert Counter(map(_canon, drawn)) == Counter(map(_canon, source))
    assert s.loose(4) == []


def test_mixed():
    source = _grid(6, 6) + [(0, 0, 1), (5, 6, 5)]  # degenerate ones too
    rnd = random.Random(3)
    s = Stripper(source)
    drawn = []
    while len(s):
        if rnd.random() < 0.4:
            drawn += s.loose(rnd.randint(1, 3))
        else:
            drawn += triangles(Prim.TRIANGLE_STRIP, s.strip(rnd.randint(3, 9)))
    assert sorted(map(sorted, drawn)) == sorted(map(sorted, source))


def test_degenerate():
    s = Stripper([(0, 1, 2), (2, 2, 1)])
    assert s.strip(10) == [0, 1, 2]
    assert s.strip(10) == [2, 2, 1]


def test_short_cap():
    s = Stripper(_grid(1, 1))
    assert s.strip(2) == []
    assert len(s) == 2
