# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import pytest
from mhfu.em.intel import GridState, HitSphere
from mhfu_port import manifest
from mhfu_port.manifest import Hurtbox, ManifestError
from mhfu_studio.monster import parts as P

GRID = '\n[[hitzone]]\nstate = "normal"\nrows = ' + str([[10] * 10] * 7) + "\n"
VOLUMES = """
[[hurtbox]]
bone = 2
radius = 90.0
part = 1

[[hurtbox]]
bone = 3
radius = 50.0
part = 2
"""


def test_name_part(doc):
    d = doc()
    s = P.PartSession(d)
    s.name_part(1, "head", severable=True)
    assert s.name_of(1) == "head" and d.manifest.parts["head"].severable
    s.name_part(1, "skull")
    assert list(d.manifest.parts) == ["skull"]
    with pytest.raises(ManifestError, match="already names slot 1"):
        s.name_part(2, "skull")
    with pytest.raises(ManifestError, match="is not a part"):
        s.name_part(8, "x")
    with pytest.raises(ManifestError, match="not a name"):
        s.name_part(3, "left wing")


def test_a_rename_keeps_the_part_severable(doc):
    d = doc()
    s = P.PartSession(d)
    s.name_part(3, "tail", severable=True)
    s.name_part(3, "stump")
    assert d.manifest.parts["stump"].severable and list(d.manifest.parts) == ["stump"]
    s.name_part(3, "tail", severable=False)
    assert not d.manifest.parts["tail"].severable


def test_sever_below(doc, sever_field):
    d = doc("\n[parts.tail]\nindex = 3\nseverable = true\n[parts.head]\nindex = 1\n")
    s = P.PartSession(d)
    assert s.severable(3) and not s.severable(1) and s.sever_below(3) is None
    before = d.manifest
    s.set_sever_below(3, 50)
    assert d.manifest.parts["tail"].sever_below == 50 and s.sever_below(3) == 50
    s.set_sever_below(3, 50)
    d.undo()
    assert d.manifest == before, "the same value is no second step"
    s.set_sever_below(3, 50)
    s.set_sever_below(3, 0)
    assert d.manifest.parts["tail"].sever_below is None
    s.set_sever_below(3, 25)
    s.name_part(3, "stump")
    assert d.manifest.parts["stump"].sever_below == 25, "a rename keeps it"
    s.name_part(3, "stump", severable=False)
    assert d.manifest.parts["stump"].sever_below is None


def test_sever_below_refusals(doc, sever_field):
    d = doc("\n[parts.tail]\nindex = 3\nseverable = true\n[parts.head]\nindex = 1\n")
    s = P.PartSession(d)
    with pytest.raises(ManifestError, match="part 1 is not a severable"):
        s.set_sever_below(1, 50)
    with pytest.raises(ManifestError, match="part 5 is not a severable"):
        s.set_sever_below(5, 50)
    with pytest.raises(ManifestError, match="1 to 100"):
        s.set_sever_below(3, 101)


def test_grid(doc):
    d = doc(GRID)
    s = P.PartSession(d)
    s.set_hitzone(0, 2, "cut", 75)
    s.set_hitzone(0, 2, 3, 40)
    rows = d.manifest.hitzones[0].rows
    assert rows[2][1] == 75 and rows[2][3] == 40 and rows[1][1] == 10
    s.fill_row(0, 6, 255)
    assert rows is not d.manifest.hitzones[0].rows
    assert d.manifest.hitzones[0].rows[6][1:4] == [255] * 3
    with pytest.raises(ManifestError, match="0..255"):
        s.set_hitzone(0, 0, "cut", 256)
    with pytest.raises(ManifestError, match="not in the grid"):
        s.set_hitzone(0, 7, "cut", 1)
    with pytest.raises(ManifestError, match="adopt or add"):
        s.set_hitzone(1, 0, "cut", 1)
    assert s.add_state("enraged") == 1 and d.manifest.hitzones[1].rows == P.blank_grid()
    with pytest.raises(ManifestError, match="already there"):
        s.add_state("enraged")
    with pytest.raises(ManifestError, match="7 rows"):
        s.add_state("bad", [[0] * 10])


def test_adopt_grid(doc):
    d = doc(GRID)
    host = [GridState(0, tuple((i,) * 10 for i in range(7))), GridState(1, ((0,) * 10,) * 7)]
    assert P.PartSession(d).adopt_grid(host) == 2
    assert [s.name for s in d.manifest.hitzones] == ["normal", "enraged"]
    assert d.manifest.hitzones[0].rows[3] == [3] * 10 and "adopted" in d.manifest.hitzones[0].label


def test_adopt_volumes(doc):
    d = doc()
    s = P.PartSession(d, n_bones=10)
    s.name_part(4, "left_wing")
    spheres = [
        HitSphere(2, 1, 1, 97.0),
        HitSphere(44, 4, 5, 65.0, "capsule", (35.0, 0.0, 0.0), (330.0, 0.0, 0.0), 0x101),
        HitSphere(0x7D, 0, 0, 0.0),
    ]
    got = s.adopt_volumes(spheres, source="em75")
    assert got.adopted == 3 and got.off_rig == [44] and "(44)" in got.describe()
    cap = d.manifest.hurtboxes[1]
    assert cap.is_capsule and cap.to == [330.0, 0.0, 0.0] and cap.flags == 0x101
    assert cap.label == "left_wing" and cap.hitzone_row == 5
    only = P.PartSession(doc(), n_bones=100).adopt_volumes(spheres, parts=[1])
    assert only.adopted == 1 and only.clean and "not the same as" in only.describe()


def test_volumes(doc):
    d = doc(VOLUMES)
    s = P.PartSession(d, n_bones=10)
    s.capacity = 1
    assert s.over_capacity == 1 and not s.volume_changed(0)
    s.scale_volume(0, 2.0)
    assert d.manifest.hurtboxes[0].radius == 180.0 and s.volume_changed(0)
    s.edit_volume(1, offset=(1, 2, 3), part=5)
    assert d.manifest.hurtboxes[1].offset == [1.0, 2.0, 3.0]
    for bad, why in (
        ({"part": 8}, "not a part"),
        ({"hitzone_row": 7}, "not a row"),
        ({"radius": -1}, "negative"),
        ({"bone": 0x10000}, "u16"),
        ({"shape": "cube"}, "one of"),
        ({"offset": [1, 2]}, "x, y, z"),
        ({"colour": 1}, "no field"),
    ):
        with pytest.raises(ManifestError, match=why):
            s.edit_volume(0, **bad)
    assert s.add_volume(Hurtbox(5, 10, part=1)) == 2
    assert s.keep_only(2) == 2 and len(d.manifest.hurtboxes) == 1
    s.remove_volume(0)
    assert not d.manifest.hurtboxes
    with pytest.raises(ManifestError, match="no volume"):
        s.remove_volume(0)


def test_save_round_trip(doc):
    d = doc(VOLUMES)
    P.PartSession(d).edit_volume(0, flags=0x101, label="head")
    back = manifest.load(d.save())
    assert back.hurtboxes[0].flags == 0x101 and back.hurtboxes[0].label == "head"


def test_summarise(make):
    m = make(VOLUMES)
    assert "INHERITS the host's 2 state(s)" in P.summarise(m, host_states=2)[2]
    assert P.summarise(make(GRID))[0].startswith("parts: none named")


def test_tigrex_volumes(doc, em75):
    s = P.PartSession(doc(), n_bones=48)
    got = s.adopt_volumes(em75.parts.spheres(), source="em75")
    assert got.adopted == 42 and got.clean
