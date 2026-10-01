# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import pytest
from mhfu.em.intel import HitSphere
from mhfu_port.manifest import Hitbox, ManifestError
from mhfu_studio.monster import attacks as A

SETS = """
[[hitbox]]
bone = 10
radius = 150.0
set = 2

[[hitbox]]
bone = 18
radius = 150.0
set = 2

[[hitbox]]
bone = 4
radius = 90.0
set = 0

[[attack]]
id = 31
power = 30
"""
HOST = [
    HitSphere(10, 0, 0, 150.0),
    HitSphere(125, 0, 0, 0.0, "capsule", b=(0.0, 0.0, 0.0)),
    HitSphere(60, 0, 0, 120.0),
]


def test_adopt_set(doc):
    d = doc(SETS)
    s = A.AttackSession(d, n_bones=47)
    got = s.adopt_set(2, HOST, source="em75 set 2", label="charge")
    assert got.adopted == 3 and got.off_rig == [60]
    assert [h.bone for _, h in s.volumes_of(2)] == [10, 125, 60]
    assert s.volumes_of(2)[1][1].is_capsule and s.volumes_of(0)[0][1].bone == 4
    assert s.sets() == [0, 2]


def test_volumes(doc):
    d = doc(SETS)
    s = A.AttackSession(d)
    s.capacities = {2: 1}
    assert s.over_capacity(2) == 1 and s.over_capacity(0) == 0
    assert s.over_capacity_all() == {2: 1}
    s.scale_volume(0, 0.5)
    assert d.manifest.hitboxes[0].radius == 75.0 and s.volume_changed(0)
    with pytest.raises(ManifestError, match="set is 0 or more"):
        s.edit_volume(0, set=-1)
    with pytest.raises(ManifestError, match="no field"):
        s.edit_volume(0, part=1)
    assert s.keep_only(1) == 1 and [h.bone for h in s.volumes()] == [18, 4]
    assert s.add_volume(Hitbox(1, 10, set=5)) == 2 and s.drop_set(5) == 1
    s.remove_volume(0)
    assert [h.set for h in s.volumes()] == [0]


def test_levers(doc):
    d = doc(SETS)
    s = A.AttackSession(d)
    s.set_attack(6, power=40, label="softer")
    s.set_attack(31, volume=5)
    assert [(a.id, a.power, a.volume) for a in s.attacks()] == [(6, 40, None), (31, 30, 5)]
    assert [a.id for a in d.manifest.attacks] == [31, 6]
    s.set_attack(31, power=None)
    assert s.attack(31).power is None
    with pytest.raises(ManifestError, match="outside a byte"):
        s.set_attack(6, element=256)
    with pytest.raises(ManifestError, match="no lever"):
        s.set_attack(6, kind=1)
    s.clear_attack(6)
    assert (
        s.attack(6) is None
        and A.summarise(d.manifest)[1] == "attacks: 1 record(s) tuned: 31(volume)"
    )


def test_summarise(make):
    assert A.summarise(make(SETS))[0] == "hitboxes: 3 volume(s) over set(s) 0, 2"
    assert (
        A.summarise(make(), host_records=106)[1]
        == "attacks: none tuned; the host's 106 record(s) stand"
    )


def test_tigrex_charge(doc, em75):
    s = A.AttackSession(doc(), n_bones=47)
    got = s.adopt_set(2, em75.attacks.set(2).spheres)
    assert got.adopted == 10 and got.clean
