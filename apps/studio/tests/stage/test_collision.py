# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import json
from pathlib import Path

import numpy as np
import pytest
from mhfu_studio.stage import collision as C
from mhfu_studio.stage.file import StageFile
from mhp_formats.fu.stage import Collision, TriFlags, cell_map, cells_for_tri


def _complete(plan: C.Plan) -> Collision:
    """The plan as a file, checked: every live triangle listed in each cell it overlaps."""
    coll = plan.collision()
    gone = set(plan.deleted)
    for ci, hits in enumerate(coll.chunks):
        want = cell_map(hits.tris, hits.grid, hits.cell, hits.origin)
        for w, run in zip(want, hits.cells, strict=True):
            lost = [t for t in w - set(run) if (ci, t) not in gone and not hits.tris[t].vertical()]
            assert not lost
    return coll


def test_box_is_closed_and_outward():
    tris = C.box_tris((0, 0, 0), (100, 200, 300), TriFlags(0, 4, 0), inflate=1)
    assert len(tris) == 12 and {t.flags.material for t in tris} == {4}
    centre = np.array((50.0, 100.0, 150.0))
    for t in tris:
        mid = np.mean([t.v0, t.v1, t.v2], axis=0)
        assert np.dot(mid - centre, t.normal) > 0
    lo = np.min([v for t in tris for v in (t.v0, t.v1, t.v2)], axis=0)
    assert lo.tolist() == [-1, -1, -1]


def test_flags():
    base = TriFlags(1, 3, 7)
    assert C.flags_of({"flags": {"material": 10}}, base) == TriFlags(1, 10, 7)
    assert C.flags_of({"flags": 0x10500}) == TriFlags(0, 5, 1)
    assert C.flags_of({}) == TriFlags()


def test_move_relinks_the_cells_left_and_entered(st: StageFile, assets: Path):
    op = {"op": "move", "group": None, "box": [-1, -1, -1, 751, 1, 1001], "by": [600, 0, 0]}
    p = C.plan(st, [op], assets)
    assert sorted((c, t) for c, t, _ in p.moved) == [(1, 0), (1, 1)]
    hits = p.chunks[1]
    for _, t, tri in p.moved:
        now = set(cells_for_tri(tri, hits.grid, hits.cell, hits.origin))
        for cell, run in enumerate(hits.cells):
            listed = C.Ref(False, t) in p.cells[1].get(cell, [C.Ref(False, x) for x in run])
            assert listed == (cell in now)
    _complete(p)
    assert "2 triangles" in p.log[0]


def test_a_relinked_cell_keeps_the_rest(st: StageFile, assets: Path):
    op = {"op": "move", "group": None, "box": [-1, -1, -1, 751, 1, 1001], "by": [600, 0, 0]}
    p = C.plan(st, [op], assets)
    moved = {t for _, t, _ in p.moved}
    assert p.cells[1]
    for cell, refs in p.cells[1].items():
        lost = set(p.chunks[1].cells[cell]) - {r.number for r in refs} - moved
        assert not lost


def test_collision_ops_compose(st: StageFile, assets: Path):
    wall = [[1300, 0, 100], [1300, 900, 100], [1300, 0, 400]]
    ops = [
        {"op": "collision", "group": None, "chunk": None, "add": [wall]},
        {"op": "collision", "group": None, "chunk": 0, "tri": 2, "flags": {"material": 10}},
        {"op": "collision", "group": None, "chunk": 0, "tri": 0, "delete": True},
        {
            "op": "collision",
            "group": None,
            "chunk": 1,
            "tri": 3,
            "verts": [[0, 5, 0], [0, 5, 400], [400, 5, 0]],
        },
        {"op": "collision", "group": None, "solid_box": [100, 0, 100, 200, 300, 200]},
        {"op": "collision", "group": None, "chunk": 1, "tri": 4, "flags": {"surface": 2}},
        {"op": "collision", "group": None, "chunk": 1, "tri": 77, "delete": True},
    ]
    p = C.plan(st, ops, assets)
    assert p.added[0][0] == 0 and p.added[0][1].flags.material == 10
    assert (0, 0) in p.deleted and len(p.added) == 13
    floor = [k for k, (c, _) in enumerate(p.added) if c == 1]
    assert p.added[floor[0]][1].flags.surface_id == 2
    assert [f.code for f in p.findings] == ["no-triangle"]
    coll = _complete(p)
    assert all(0 not in run for run in coll.chunks[0].cells)
    assert coll.chunks[1].tris[3].v0 == (0.0, 5.0, 0.0)


def test_solid_from_the_mesh_ops(st: StageFile, assets: Path):
    ops = [
        {"op": "pack", "group": 0, "obj": "slab.obj", "solid": True, "chunk": 1},
        {"op": "pack", "group": 0, "obj": "tetra.obj", "solid": "box"},
        {
            "op": "move",
            "group": 1,
            "vertices": [0, 5],
            "box": [1990, 0, 1990, 2110, 100, 2110],
            "by": [0, 0, 0],
            "solid": True,
        },
        {
            "op": "transform",
            "group": 1,
            "vertices": list(range(16)),
            "by": [-1500, 0, -1500],
            "solid": True,
            "inflate": 10,
        },
    ]
    p = C.plan(st, ops, assets)
    assert [c for c, _ in p.added[:2]] == [1, 1] and len(p.added) == 2 + 12 + 4 + 12
    xs = [v[0] for _, t in p.added[-12:] for v in (t.v0, t.v1, t.v2)]
    assert (min(xs), max(xs)) == pytest.approx((490.0, 810.0), abs=0.2)
    _complete(p)


def test_volumes_see_added_and_skip_deleted(st: StageFile, assets: Path):
    wall = [[1300, 0, 100], [1300, 900, 100], [1300, 0, 400]]
    ops = [
        {"op": "collision", "group": None, "chunk": 0, "add": [wall]},
        {"op": "collision", "group": None, "chunk": 0, "tri": 0, "delete": True},
        {"op": "move", "group": None, "box": [1100, -1, 0, 1400, 901, 900], "by": [0, 10, 0]},
    ]
    p = C.plan(st, ops, assets)
    assert [t for _, t, _ in p.moved] == [1] and p.added[0][1].v0 == (1300.0, 10.0, 100.0)
    assert "2 triangles" in p.log[-1]


def test_clear_unlinks_inside(st: StageFile, assets: Path):
    op = {"op": "clear", "group": 0, "solid": True, "box": [1100, -1, 200, 1300, 900, 800]}
    p = C.plan(st, [op], assets)
    assert sorted(p.deleted) == [(0, 0), (0, 1)]
    assert all(not refs for refs in p.cells[0].values())
    assert C.plan(st, [{**op, "solid": False}], assets).empty


def test_serialise(st: StageFile, assets: Path):
    ops = [{"op": "collision", "group": None, "add": [[[0, 0, 0], [0, 0, 9], [9, 0, 0]]]}]
    data = json.loads(json.dumps(C.serialise(C.plan(st, ops, assets))))
    assert data["added"][0]["chunk"] == 1 and data["moved"] == []
    assert [1, 0] in data["cells"]["1"]["0"]


def test_no_collision(bare: StageFile, assets: Path):
    stage = bare.stage
    stage.collision = None
    st = StageFile(1, stage.to_bytes())
    p = C.plan(st, [{"op": "collision", "tri": 0, "flags": {"material": 9}}], assets)
    assert p.empty and p.findings[0].code == "no-collision"
