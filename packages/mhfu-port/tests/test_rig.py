# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import hashlib

import pytest
from mhfu import files
from mhfu_port import fk, rig
from mhp_formats.pac import Pac
from mhp_formats.skeleton import Bone, Skeleton

# root, hip, spine; the head (3, 4) sits in mid order at +Z, the tail (7, 8) at -Z
PARENTS = [-1, 0, 1, 2, 3, 2, 2, 1, 7, 5]
LOCAL = [
    (0.0, 0.0, 0.0),
    (0.0, 10.0, 0.0),
    (0.0, 0.0, 10.0),
    (0.0, 0.0, 10.0),
    (0.0, 0.0, 10.0),
    (5.0, -10.0, 0.0),
    (-5.0, -10.0, 0.0),
    (0.0, 0.0, -10.0),
    (0.0, 0.0, -10.0),
    (0.0, -5.0, 0.0),
]
ORDER = [0, 1, 2, 5, 6, 9, 3, 4, 7, 8]


def skeleton(parents, local, animated=None):
    bones = [Bone(parent=p, position=x) for p, x in zip(parents, local, strict=True)]
    return Skeleton(bones, [0, len(bones) if animated is None else animated])


def donor():
    """The rig above plus an orphan root chain (10, 11) past the animated bones."""
    return skeleton([*PARENTS, -1, 10], [*LOCAL, (0.0, 0.0, -50.0), (0.0, 0.0, -5.0)])


def host():
    """Three origin joints, then a body."""
    return skeleton([-1, 0, 1, 2], [(0.0, 0.0, 0.0)] * 3 + [(0.0, 10.0, 0.0)])


def test_partition():
    assert rig.partition(PARENTS, fk.bind_world(PARENTS, LOCAL), 10) == ([6, 2, 2], ORDER)
    moved = rig.reorder(PARENTS, ORDER)
    assert all(p < j for j, p in enumerate(moved))
    again = rig.partition(moved, fk.bind_world(moved, [LOCAL[i] for i in ORDER]), 10)
    assert again == ([6, 2, 2], list(range(10)))


def test_partition_small():
    assert rig.partition([-1, 0], [(0.0, 0.0, 0.0)] * 2, 2) == ([2], [0, 1])


def test_reorder():
    assert rig.reorder([-1, 0, 0, 1], [0, 2, 1, 3]) == [-1, 0, 0, 2]


def test_from_donor():
    r = rig.from_donor(donor(), host(), 10)
    pad = 2  # the host's origin chain is 3 joints, the donor's 1
    assert (r.lead_pad, r.adopted, r.streams, r.animated) == (pad, [10], [8, 2, 2], 12)
    assert r.joint_of == {old: pad + new for new, old in enumerate([*ORDER, 10, 11])}
    assert r.parents[:3] == [-1, 0, 1]
    assert r.parents[r.joint_of[10]] == r.joint_of[1]
    assert all(p < j for j, p in enumerate(r.parents))
    assert r.bind[r.joint_of[11]] == (0.0, 10.0, -55.0)
    sk = r.skeleton
    assert sk.params == [0, 12]
    assert [b.stream for b in sk.bones] == [0] * 8 + [1] * 2 + [2] * 2 + [3] * 2
    assert Skeleton.from_bytes(sk.to_bytes()) == sk


def test_links():
    bones = rig.from_donor(donor(), host(), 10).skeleton.bones
    for j, b in enumerate(bones):
        kids = [i for i, c in enumerate(bones) if c.parent == j]
        assert b.child == (kids[0] if kids else -1)
        for a, c in zip(kids, kids[1:], strict=False):
            assert bones[a].sibling == c
    assert [b.sibling for b in bones if b.parent == -1] == [-1]


def test_from_donor_clamps():
    assert rig.from_donor(donor(), host(), 0).animated == 14
    assert rig.from_donor(donor(), host(), 99).animated == 14


def test_from_donor_second_root():
    """A root at bone 1 goes onto bone 0, not onto itself."""
    r = rig.from_donor(skeleton([-1, -1, 1], [(0.0, 0.0, 0.0)] * 3), host(), 3)
    assert (r.adopted, r.parents) == ([1], [-1, 0, 1])


def test_from_donor_order():
    with pytest.raises(ValueError, match="parent"):
        rig.from_donor(skeleton([-1, 2, 0], [(0.0, 0.0, 0.0)] * 3), host(), 3)


def test_from_host():
    h = host()
    for b, s in zip(h.bones, [0, 0, 1, 2], strict=True):
        b.stream = s
    r = rig.from_host(h)
    assert (r.streams, r.joint_of, r.lead_pad, r.parents) == ([2, 1, 1], {}, 0, [-1, 0, 1, 2])
    assert r.skeleton == h and r.skeleton is not h
    h.bones[3].stream = 5
    with pytest.raises(ValueError, match="stream"):
        rig.from_host(h)
    h.params = [0]
    with pytest.raises(ValueError, match="count"):
        rig.from_host(h)


# our own output, pinned: the skeletons the port ships
DONORS = [
    (5248, [31, 9, 4], 1, [43], 47, "df4d23ca7da440ed"),
    (5339, [33, 6, 7], 0, [46], 51, "8e522dea8d9d1901"),
]


@pytest.mark.parametrize(("model", "streams", "pad", "adopted", "joints", "digest"), DONORS)
def test_donor(data, model, streams, pad, adopted, joints, digest):
    host = Skeleton.from_bytes(Pac.from_bytes(data.fu.read(files.monster_pac(75))).entries[0])
    donor = Skeleton.from_bytes(Pac.from_bytes(data.p3rd.read(model)).entries[0])
    animated = donor.params[1]  # the driven range, as test_records shows
    r = rig.from_donor(donor, host, animated)
    assert (r.streams, r.lead_pad, r.adopted, len(r.parents)) == (streams, pad, adopted, joints)
    assert r.animated == animated + pad == r.skeleton.params[1]
    assert sorted(r.joint_of) == list(range(len(donor.bones)))
    assert all(p < j for j, p in enumerate(r.parents))
    assert hashlib.sha256(r.skeleton.to_bytes()).hexdigest()[:16] == digest


def test_host(data):
    host = Skeleton.from_bytes(Pac.from_bytes(data.fu.read(files.monster_pac(75))).entries[0])
    r = rig.from_host(host)
    assert (r.streams, len(r.parents)) == ([31, 9, 5], 48)
    assert r.skeleton.to_bytes() == host.to_bytes()
