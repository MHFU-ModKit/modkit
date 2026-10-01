# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path

import pytest
from mhfu_studio.stage.checks import floor_at, surface_ids
from mhfu_studio.stage.file import StageFile
from mhfu_studio.stage.obj import read_obj
from mhp_formats.fu.stage import Tri, build_hits


def test_read_obj(tmp_path: Path):
    path = tmp_path / "a.obj"
    path.write_text(
        "# a quad with a seam\nv 0 0 0\nv 1 0 0\nv 1 0 1\nv 0 0 1\nvt 0 0\nvt 1 0\nvt 1 1\n"
        "vn 0 1 0\nf 1/1 2/2 3/3 4\nf -4/1 -2/3 -1/2\n"
    )
    m = read_obj(path)
    assert m.positions == [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1), (0, 0, 1)]
    assert m.uvs == [(0, 0), (1, 0), (1, 1), (0, 0), (1, 0)]
    assert m.triangles == [(0, 1, 2), (0, 2, 3), (0, 2, 4)]
    assert m.corners()[0] == ((0, 0, 0), (1, 0, 0), (1, 0, 1))
    path.write_text("v 0 0 0\nf 1 2 3\n")
    with pytest.raises(ValueError):
        read_obj(path)
    path.write_text("v 0 0 0\nv 1 0 0\nv 0 0 1\nf 1 2 3\n")
    assert read_obj(path).uvs is None


def test_floor_at():
    floor = Tri.from_verts((0.0, 0.0, 0.0), (0.0, 0.0, 1000.0), (1000.0, 0.0, 0.0))
    deck = Tri.from_verts((0.0, 300.0, 0.0), (0.0, 300.0, 1000.0), (1000.0, 300.0, 0.0))
    wall = Tri.from_verts((0.0, 0.0, 0.0), (0.0, 500.0, 0.0), (0.0, 0.0, 500.0))
    hits = build_hits([floor, deck, wall])
    assert floor_at(hits, 100, 100, 280) == pytest.approx(300)
    assert floor_at(hits, 100, 100, 20) == pytest.approx(0)
    assert floor_at(hits, 900, 900, 0) is None


def test_surface_ids(st: StageFile):
    assert surface_ids(st.stage) == {(0, 0): 2, (1, 1): 4}


def test_shipped_positions(st: StageFile, bare: StageFile):
    pmo = st.pmo(0)
    assert pmo is not None and list(st.positions(0, 1)) == pmo.positions(1)
    assert st.positions(0, 1) is st.positions(0, 1)
    assert st.positions(0, 9) == () and bare.positions(2, 0) == ()
