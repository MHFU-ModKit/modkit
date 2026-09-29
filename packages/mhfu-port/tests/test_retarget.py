# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import pytest
from mhfu import files
from mhfu_port import retarget
from mhp_formats.pac import Pac
from mhp_formats.skeleton import Bone, Skeleton

ORIGIN = (0.0, 0.0, 0.0)


def skeleton(parents, local):
    return Skeleton([Bone(parent=p, position=x) for p, x in zip(parents, local, strict=True)])


def test_match():
    """The origin chains pair from the hip; a joint out of reach stays at rest."""
    tail = [(0.0, 0.0, -10.0)] * 2
    donor = skeleton([-1, 0, 1, 2], [ORIGIN] * 2 + tail)
    host = skeleton([-1, 0, 1, 2, 3, 0], [ORIGIN] * 3 + tail + [(100.0, 0.0, 0.0)])
    assert retarget.match(donor, host) == {0: None, 1: 0, 2: 1, 3: 2, 4: 3, 5: None}


def test_match_depth():
    """At one distance the joint at the same depth wins."""
    donor = skeleton([-1, 0, 0, 2], [ORIGIN, (0.0, 0.0, 10.0), (0.0, 5.0, 0.0), (0.0, -5.0, 10.0)])
    host = skeleton([-1, 0, 1], [ORIGIN, (0.0, 2.5, 5.0), (0.0, 0.0, 5.0)])
    assert retarget.match(donor, host) == {0: 0, 1: None, 2: 3}


def test_match_empty():
    assert retarget.match(Skeleton([]), skeleton([-1], [ORIGIN])) == {0: None}


def test_depth_of():
    assert retarget.depth_of([-1, 0, 1, 0, -1]) == [0, 1, 2, 1, 0]


@pytest.mark.parametrize(("model", "matched", "hip"), [(5248, 46, 1), (5339, 36, 2)])
def test_donor(data, model, matched, hip):
    """Onto the Tigrex, whose hip is joint 2."""
    host = Skeleton.from_bytes(Pac.from_bytes(data.fu.read(files.monster_pac(75))).entries[0])
    donor = Skeleton.from_bytes(Pac.from_bytes(data.p3rd.read(model)).entries[0])
    m = retarget.match(donor, host)
    bones = [b for b in m.values() if b is not None]
    assert (len(m), len(bones), len(set(bones))) == (48, matched, matched)
    assert m[2] == hip
