# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import math
from pathlib import Path

import numpy as np
import pytest
from mhfu_studio.stage import mesh, rebuild
from mhfu_studio.stage.file import StageFile
from mhfu_studio.stage.obj import read_obj
from mhp_formats.fu.stage import Stage, Tri
from mhp_formats.pmo import Block, Pmo
from mhp_formats.psp.ge import Op

QUANTUM = 4000.0 / 32768
"""One position step of the synthetic stage."""


def _pmo(e: mesh.MeshEdit) -> Pmo:
    return Pmo.from_bytes(e.data)


def _key(tri: tuple) -> tuple:
    """A triangle of points, rotated to start at its least corner: winding kept."""
    k = tri.index(min(tri))
    return tuple(tuple(round(c) for c in p) for p in (*tri[k:], *tri[:k]))


def _drawn(pmo: Pmo, g: int) -> set[tuple]:
    pos = pmo.positions(g)
    tris = [tuple(pos[i] for i in t) for t in pmo.triangles(g)]
    return {_key(t) for t in tris if len(set(t)) == 3}


def _source(path: Path) -> set[tuple]:
    m = read_obj(path)
    return {_key(c) for c in m.corners()}


def test_transform_composes(st: StageFile, assets: Path):
    before = st.pmo(0).positions(0)
    ops = [
        {"op": "transform", "group": 0, "vertices": [0, 1, 6], "by": [10, 0, 0]},
        {"op": "transform", "group": 0, "vertices": [0, 1, 6], "by": [0.03, 20, 0]},
        {
            "op": "move",
            "group": 0,
            "sphere": [before[0][0] + 10, 20, before[0][2], 1],
            "by": [0, 0, -5],
        },
    ]
    e = mesh.edit(st, 0, ops, assets)
    assert e.safe and e.applied == 3 and len(e.data) == len(e.original)
    after = _pmo(e).positions(0)
    for i in (0, 1, 6):
        want = np.add(before[i], (10.03, 20, -5 if i == 0 else 0))
        assert np.abs(np.subtract(after[i], want)).max() <= QUANTUM
    assert [after[i] for i in range(36) if i not in (0, 1, 6)] == [
        before[i] for i in range(36) if i not in (0, 1, 6)
    ]
    assert "0 straddling" not in e.log[0] and "straddling" in e.log[0]


def test_transform_matrix():
    op = {"pivot": [100, 0, 0], "rotate": [0, 90, 0], "scale": [2, 2, 2], "by": [0, 5, 0]}
    m = mesh.transform_matrix(op)
    p = m @ [200, 0, 0, 1]
    assert np.allclose(p[:3], [100, 5, -200])
    assert np.array_equal(mesh.transform_matrix({"matrix": m.tolist()}), m)
    flat = {"matrix": [v for row in m.tolist() for v in row]}
    assert np.array_equal(mesh.transform_matrix(flat), m)


def test_select():
    pts = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 50.0, 0.0)]
    assert mesh.select({"vertices": [2, 0, 9]}, pts) == [2, 0]
    assert mesh.select({"sphere": [0, 0, 0, 10]}, pts) == [0, 1]
    assert mesh.select({"box": [-1, -1, -1, 1, 60, 1]}, pts) == [0, 2]
    assert mesh.select({}, pts) == []


def test_pack_keeps_winding(st: StageFile, assets: Path):
    e = mesh.edit(st, 0, [{"op": "pack", "group": 0, "obj": "tetra.obj"}], assets)
    assert e.safe and not e.findings
    assert _drawn(_pmo(e), 0) == _source(assets / "tetra.obj")
    # the last primitive draws under FFACE 1: the second loose triangle lands there, wound right
    last = len(st.pmo(0).groups()[0].block.spans()) - 1
    op = {"op": "pack", "group": 0, "obj": "pair.obj", "prims": [0, last]}
    e = mesh.edit(st, 0, [op], assets)
    assert not e.findings and _drawn(_pmo(e), 0) >= _source(assets / "pair.obj")


def test_pack_options(st: StageFile, assets: Path):
    op = {"op": "pack", "group": 0, "obj": "quad.obj", "uv": "obj", "colour": [8, 16, 24]}
    pmo = _pmo(mesh.edit(st, 0, [op], assets))
    block = pmo.groups()[0].block
    used = sorted({i for t in pmo.triangles(0) if len(set(t)) == 3 for i in t})
    assert {tuple(block.vertices.uvs()[i]) for i in used} == {(0, 0), (1, 0), (1, 1), (0, 1)}
    assert {block.vertices.colors()[i][:3] for i in used} == {(8, 16, 24)}
    planar = _pmo(mesh.edit(st, 0, [{**op, "uv": "planar"}], assets)).groups()[0].block
    box = st.pmo(0).groups()[0].block.vertices.uvs()
    lo, hi = np.min(box, axis=0), np.max(box, axis=0)
    assert all((lo <= uv).all() and (uv <= hi).all() for uv in np.asarray(planar.vertices.uvs()))
    kept = _pmo(mesh.edit(st, 0, [{**op, "uv": "keep"}], assets)).groups()[0].block
    n = len(kept.vertices)  # a reader sizes the buffer by the highest index drawn
    assert kept.vertices.texture == st.pmo(0).groups()[0].block.vertices.texture[:n]


def test_sculpt_writes_positions_only(st: StageFile, assets: Path):
    shipped = st.pmo(0).groups()[2].block
    e = mesh.edit(st, 0, [{"op": "sculpt", "group": 2, "obj": "tetra.obj"}], assets)
    block = _pmo(e).groups()[2].block
    assert block.indices == shipped.indices and block.commands == shipped.commands
    assert not e.findings and _drawn(_pmo(e), 2) == _source(assets / "tetra.obj")
    e = mesh.edit(st, 0, [{"op": "sculpt", "group": 2, "obj": "tetra.obj", "first": 2}], assets)
    assert [f.code for f in e.findings] == ["did-not-fit"]
    assert _pmo(e).positions(2)[:8] == st.pmo(0).positions(2)[:8]


def test_sculpt_bends_nothing(st: StageFile, assets: Path):
    # every strip of the grid shares a row with the next and the first is not chosen: nothing
    # can take a triangle, and nothing is written
    e = mesh.edit(st, 0, [{"op": "sculpt", "group": 0, "obj": "tetra.obj", "first": 1}], assets)
    assert [f.code for f in e.findings] == ["did-not-fit"] and e.data == e.original


def test_replace(st: StageFile, assets: Path):
    e = mesh.edit(st, 0, [{"op": "replace", "group": 1, "obj": "quad.obj"}], assets)
    assert _drawn(_pmo(e), 1) == _source(assets / "quad.obj")
    assert "0 left over" in e.log[0]


def test_clear(st: StageFile, assets: Path):
    e = mesh.edit(
        st, 0, [{"op": "clear", "group": 1}, {"op": "clear", "group": 0, "prims": [0, 2]}], assets
    )
    pmo = _pmo(e)
    assert all(len(set(t)) == 1 for t in pmo.triangles(1)) or pmo.triangles(1) == []
    spans = st.pmo(0).groups()[0].block.spans()
    gone = sum(spans[p][1] - 2 for p in (0, 2))
    drawn = [len(set(t)) == 3 for t in pmo.triangles(0)]
    assert sum(drawn) == len(drawn) - gone and e.safe


def test_material(st: StageFile, assets: Path):
    ops = [
        {"op": "material", "group": 1, "rgba": [1, 2, 3, 4], "ambient": [5, 6, 7, 8]},
        {"op": "material", "group": 0, "mat": 1, "texture": 0},
        {"op": "material", "group": 0, "mat": 5, "texture": 0},
    ]
    e = mesh.edit(st, 0, ops, assets)
    mat = _pmo(e).material(1)
    assert mat is not None and (mat.color, mat.shadow, mat.texture) == (67305985, 134678021, 0)
    assert [(f.code, f.target) for f in e.findings] == [("refused", (1, 2))]
    assert e.applied == 2 and e.safe


def test_refusals_leave_the_rest(st: StageFile, bare: StageFile, assets: Path):
    ops = [
        {"op": "transform", "group": 9, "vertices": [0], "by": [1, 0, 0]},
        {"op": "pack", "group": 0, "obj": "missing.obj"},
        {"op": "move", "group": 0, "vertices": [0], "by": [100, 0, 0]},
        {"op": "move", "sub": 2, "group": 0, "vertices": [0], "by": [100, 0, 0]},
        {"op": "texture", "slot": 0, "rgb": [1, 2, 3]},
    ]
    e = mesh.edit(st, 0, ops, assets)
    assert [f.code for f in e.findings] == ["refused", "refused"] and e.applied == 1
    assert set(mesh.edits(st, ops, assets)) == {0, 2}
    assert mesh.edit(bare, 2, ops, assets).findings[0].code == "no-pmo"


def test_diff_runs():
    old = bytes(16)
    new = bytearray(old)
    new[2:4] = b"ab"
    new[6] = 1
    new[15] = 1
    assert mesh.diff_runs(bytes(new), old) == [(2, 2), (6, 1), (15, 1)]
    assert mesh.diff_runs(bytes(new), old, gap=2) == [(2, 5), (15, 1)]
    with pytest.raises(ValueError):
        mesh.diff_runs(b"a", b"")


def test_budget(st: StageFile, bare: StageFile):
    rows = mesh.budget(st, 0)
    assert [r[0] for r in rows] == [0, 1, 2]
    assert rows[1] == (1, 4, 27, 19, 16)
    assert mesh.budget(bare, 2) == []


def test_remesh(st: StageFile):
    pos = st.pmo(0).positions(1)
    e = mesh.remesh(st, 0, {1: [(x, y + 100, z) for x, y, z in pos]})
    assert e.safe and np.allclose(_pmo(e).positions(1), np.add(pos, (0, 100, 0)), atol=QUANTUM)
    with pytest.raises(ValueError):
        mesh.remesh(st, 0, {1: pos[:3]})


def test_grow(st: StageFile, assets: Path):
    quad = read_obj(assets / "quad.obj")
    stage, line = rebuild.grow(st, 0, quad, solid=1)
    pmo = Pmo.from_bytes(stage.terrain)
    assert len(pmo.groups()[0].block.vertices) == 36 + 4 and "g0" in line
    assert _source(assets / "quad.obj") <= _drawn(pmo, 0)
    block = pmo.groups()[0].block
    prims = [k for k, c in enumerate(block.commands) if c.op == Op.PRIM]
    assert prims[-1] < [k for k, c in enumerate(block.commands) if c.op == Op.OFFSETADDR][0]
    assert stage.collision and len(stage.collision.chunks[1].tris) == 4 + 2
    assert stage.collision.chunks[1].grid_check().missing == 0
    assert Stage.from_bytes(stage.to_bytes()) == stage


def test_grow_promotes_indices(st: StageFile, tmp_path: Path):
    n = 300
    lines = [f"v {10 * k} 0 {k % 7}" for k in range(n)]
    lines += [f"f {k + 1} {k + 2} {k + 3}" for k in range(n - 2)]
    (tmp_path / "strip.obj").write_text("\n".join(lines))
    stage, _ = rebuild.grow(st, 0, read_obj(tmp_path / "strip.obj"), group=1)
    block: Block = Pmo.from_bytes(stage.terrain).groups()[1].block
    assert block.vertices.vtype.index == 2 and len(block.vertices) == 16 + n


def test_rebuild(st: StageFile):
    far = Tri.from_verts((2000.0, 0.0, 0.0), (2000.0, 0.0, 100.0), (2100.0, 0.0, 0.0))
    stage = rebuild.rebuild(st.stage, [far], chunks=[1], refit=True)
    assert stage.collision and stage.collision.chunks[0] == st.chunks[0]
    assert stage.collision.chunks[1].grid == (5, 2) and stage.collision.chunks[1].tris[-1] == far
    with pytest.raises(ValueError):
        rebuild.rebuild(st.stage, chunks=[4])
    assert math.isclose(stage.verify().frac, 1.0)
