# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import pytest
from mhfu_port import mesh, skin
from mhfu_port.data import Data
from mhfu_port.mesh import Part, Skinned
from mhp_formats import pmo as fu
from mhp_formats.pac import Pac

# a spine along x (joints 0-3) and a limb up y (joints 4-5), forking at the root
BIND = [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (200.0, 0.0, 0.0), (300.0, 0.0, 0.0)]
BIND += [(0.0, 100.0, 0.0), (0.0, 200.0, 0.0)]
PARENTS = [-1, 0, 1, 2, 0, 4]
SCALE = (128.0, 128.0, 128.0)


def _part(positions, influences=None) -> Part:
    n = len(positions)
    influences = influences or [[(0, 1.0)]] * n
    return Part(list(positions), [], [(0.0, 0.0)] * n, [], [], influences, 0)


def _joints(row):
    return {j for j, _ in row}


def test_source():
    part = _part([(0.0, 0.0, 0.0)] * 3, [[(4, 0.5), (7, 0.5)], [(7, 1.0)], [(4, 0.0), (5, 1.0)]])
    (out,) = skin.source([part], {4: 1, 5: 3})
    assert out.part is part
    assert out.influences == [[(1, 1.0)], [(0, 1.0)], [(3, 1.0)]]


def test_source_cap():
    rows = [[(j, 1.0)] for j in range(10)] + [[(0, 1.0), (1, 1.0)]]
    (out,) = skin.source([_part([(0.0, 0.0, 0.0)] * 11, rows)], {j: j for j in range(10)})
    assert len(mesh.palette(out)) == mesh.PALETTE
    assert out.influences[9] == [(0, 1.0)] and out.influences[10] == [(0, 0.5), (1, 0.5)]


def test_auto():
    (out,) = skin.auto([_part([(150.0, 5.0, 0.0)])], BIND, PARENTS)
    assert [j for j, _ in out.influences[0]] == [2, 1, 3]
    assert out.influences[0][0][1] > out.influences[0][1][1] == out.influences[0][2][1]
    assert sum(w for _, w in out.influences[0]) == pytest.approx(1.0)
    (out,) = skin.auto([_part([(150.0, 5.0, 0.0)])], BIND, PARENTS, exclude={2}, nb=1)
    assert out.influences == [[(1, 1.0)]]


def test_auto_region():
    limb = (0.0, 150.0, 0.0)
    (alone,) = skin.auto([_part([limb])], BIND, PARENTS, nb=1)
    assert alone.influences == [[(5, 1.0)]]
    spine = [(250.0, 1.0, 0.0), (260.0, 1.0, 0.0), (270.0, 1.0, 0.0)]
    (locked,) = skin.auto([_part([*spine, limb])], BIND, PARENTS)
    assert _joints(locked.influences[3]) <= {0, 1, 2, 3}


def test_weld():
    at = (50.0, 50.0, 0.0)
    far = [Skinned(_part([at]), [[(1, 1.0)]]), Skinned(_part([at]), [[(5, 1.0)]])]
    assert skin.weld(far, BIND) == 1
    assert [s.influences for s in far] == [[[(1, 1.0)]], [[(1, 1.0)]]]
    near = [Skinned(_part([at]), [[(1, 1.0)]]), Skinned(_part([at]), [[(2, 1.0)]])]
    assert skin.weld(near, BIND) == 0 and near[1].influences == [[(2, 1.0)]]
    one = [Skinned(_part([at, at]), [[(1, 1.0)], [(5, 1.0)]])]
    assert skin.weld(one, BIND) == 0


def _host() -> fu.Pmo:
    quad = [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (0.0, 0.0, 100.0), (100.0, 0.0, 100.0)]
    part = Part(quad, [], [(0.0, 0.0)] * 4, [], [(0, 1, 2), (2, 1, 3)], [], 0)
    return mesh.build(
        [Skinned(part, [[(1, 1.0)], [(2, 1.0)], [(1, 1.0)], [(2, 1.0)]])], SCALE, tip=None
    )


def test_transfer():
    part = _part([(50.0, 10.0, 50.0), (25.0, -5.0, 0.0), (-20.0, 0.0, -20.0)])
    (out,) = skin.transfer([part], _host(), PARENTS)
    assert [dict(row) for row in out.influences] == [
        pytest.approx({1: 0.5, 2: 0.5}),
        pytest.approx({1: 0.75, 2: 0.25}),
        {1: 1.0},
    ]
    (dead,) = skin.transfer([part], _host(), PARENTS, dead={2})
    assert [dict(row) for row in dead.influences] == [pytest.approx({1: 1.0})] * 3


def test_transfer_flat():
    line = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)]
    part = Part(line, [], [(0.0, 0.0)] * 3, [], [(0, 1, 2)], [], 0)
    host = mesh.build([Skinned(part, [[(0, 1.0)]] * 3)], SCALE, tip=None)
    with pytest.raises(ValueError, match="area"):
        skin.transfer([_part([(0.0, 0.0, 0.0)])], host, PARENTS)


@pytest.mark.parametrize("model", [5248, 5339])
def test_game(data: Data, model: int):
    donor = mesh.donor(data.p3rd.read(model), data.p3rd.read(model + 1))
    parts = mesh.parts(donor)
    host = fu.Pmo.from_bytes(Pac.from_bytes(data.fu.read(6185)).entries[1])
    moved = skin.transfer(parts, host, [])
    assert all(len(mesh.palette(s)) <= mesh.PALETTE for s in moved)
    assert all(sum(w for _, w in row) == pytest.approx(1.0) for s in moved for row in s.influences)
