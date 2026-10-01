# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The stage writers on the shipped stages (MHFU_DATA)."""

from pathlib import Path

import pytest
from mhfu import files
from mhfu.files import Extracted
from mhfu_studio.stage import checks, collision, mesh, rebuild, textures
from mhfu_studio.stage.file import TEXTURES, StageFile
from mhfu_studio.stage.obj import read_obj
from mhp_formats.fu.stage import Tri, cells_for_tri
from mhp_formats.pmo import Pmo
from mhp_formats.tmh import Tmh

CAMP = 98
"""The snowy mountains' base camp."""
CRATE = [14450, 1100, 16250, 400]
"""A sphere around the camp's supply crate, in terrain group 11."""


@pytest.fixture(scope="module")
def game(mhfu_data: Path) -> Extracted:
    return Extracted.find(mhfu_data)


@pytest.fixture(scope="module")
def camp(game: Extracted) -> StageFile:
    return StageFile.read(game, CAMP)


@pytest.fixture
def shapes(tmp_path: Path) -> Path:
    (tmp_path / "slab.obj").write_text(
        "v 13000 1400 15400\nv 13600 1400 15400\nv 13600 1400 16000\nv 13000 1400 16000\n"
        "f 1 2 3\nf 1 3 4\n"
    )
    (tmp_path / "tetra.obj").write_text(
        "v 14500 1700 16000\nv 14300 1300 15800\nv 14700 1300 15800\nv 14500 1300 16200\n"
        "f 1 2 3\nf 1 3 4\nf 1 4 2\nf 2 4 3\n"
    )
    return tmp_path


def test_exits_land(game: Extracted):
    found = checks.landings(game)
    assert (len(found), len({k.stage for k in found})) == (555, 226)
    assert sum(k.lands for k in found) == 532
    # the floor nearest the destination counts: these four stand on an upper level
    upper = {(84, 83), (142, 144), (145, 144), (218, 220)}
    assert upper <= {(k.stage, k.target) for k in found if k.lands}


def test_census(game: Extracted):
    c = checks.census(game)
    assert (c[0].triangles, len(c[0].stages)) == (217655, 246)
    assert sum(e.triangles for e in c.values()) == 283454


def test_self_checks(game: Extracted):
    p = checks.planes(game)
    assert p.stages == 246 and [n for n, _ in p.failing] == [46]
    assert checks.inplace(game, [CAMP, 139]) == (4, [])
    v = textures.verify(game, [CAMP])
    assert v.exact == v.images == 16 and not v.failed


def test_materials_resolve(game: Extracted):
    for n in files.STAGES[1:]:
        sf = StageFile.read(game, n)
        for sub in (0, 2):
            pmo = sf.pmo(sub)
            assert pmo is None or all(pmo.material(g) for g in range(len(pmo.groups())))


def test_move_relinks_the_cells(camp: StageFile, shapes: Path):
    op = {
        "op": "move",
        "group": 11,
        "sphere": CRATE,
        "by": [0, 0, -3000],
        "collision": {"chunks": [1]},
    }
    p = collision.plan(camp, [op], shapes)
    assert p.moved
    hits = p.chunks[1]
    for _, t, tri in p.moved:
        now = set(cells_for_tri(tri, hits.grid, hits.cell, hits.origin))
        for cell in now:
            assert collision.Ref(False, t) in p.cells[1][cell]
        for cell, run in enumerate(hits.cells):
            if t in run and cell not in now:
                assert collision.Ref(False, t) not in p.cells[1].get(cell, [])


def test_relinked_cells_keep_the_rest(camp: StageFile, shapes: Path):
    op = {
        "op": "move",
        "group": 11,
        "sphere": CRATE,
        "by": [40, 0, 40],
        "collision": {"chunks": [0, 1]},
    }
    p = collision.plan(camp, [op], shapes)
    for c, cells in p.cells.items():
        moved = {t for k, t, _ in p.moved if k == c}
        for cell, refs in cells.items():
            assert not set(p.chunks[c].cells[cell]) - {r.number for r in refs} - moved


def test_added_triangles_are_reachable(camp: StageFile, shapes: Path):
    p = collision.plan(
        camp, [{"op": "pack", "group": 11, "obj": "slab.obj", "solid": True, "chunk": 1}], shapes
    )
    assert len(p.added) == 2
    hits = p.chunks[1]
    for k, (_, tri) in enumerate(p.added):
        for cell in cells_for_tri(tri, hits.grid, hits.cell, hits.origin):
            assert collision.Ref(True, k) in p.cells[1][cell]


def test_pack_fills_strips(camp: StageFile, shapes: Path):
    block = camp.pmo(0).groups()[9].block
    assert block.budget()[:3] == (158, 632, 316)
    e = mesh.edit(
        camp, 0, [{"op": "pack", "group": 9, "obj": "tetra.obj", "collapse": False}], shapes
    )
    assert e.safe and not e.findings and "4 triangles into" in e.log[0]
    assert Pmo.from_bytes(e.data).groups()[9].block.commands == block.commands


def test_pack_keeps_the_layout(camp: StageFile, shapes: Path):
    for reindex in (True, False):
        op = {
            "op": "pack",
            "group": 11,
            "obj": "tetra.obj",
            "first": 0,
            "count": 64,
            "reindex": reindex,
        }
        e = mesh.edit(camp, 0, [op], shapes)
        assert e.safe and not e.findings
        shipped = camp.pmo(0).groups()[11].block
        packed = Pmo.from_bytes(e.data).groups()[11].block
        assert (packed.indices == shipped.indices) == (not reindex)
        assert sum(n for _, n in e.runs()) < 4000


def test_sculpt_writes_no_command(camp: StageFile, shapes: Path):
    e = mesh.edit(camp, 0, [{"op": "sculpt", "group": 9, "obj": "tetra.obj"}], shapes)
    shipped = camp.pmo(0).groups()[9].block
    block = Pmo.from_bytes(e.data).groups()[9].block
    assert e.safe and block.commands == shipped.commands and block.indices == shipped.indices


def test_grow_lands_where_asked(camp: StageFile, shapes: Path):
    stage, _ = rebuild.grow(camp, 0, read_obj(shapes / "slab.obj"), group=11, solid=1)
    pmo = Pmo.from_bytes(stage.terrain)
    shipped = camp.pmo(0)
    assert shipped is not None
    n = len(shipped.groups()[11].block.vertices)
    got = pmo.positions(11)[n:]
    step = max(pmo.scale) / 32768
    for p, q in zip(got, read_obj(shapes / "slab.obj").positions, strict=True):
        assert max(abs(a - b) for a, b in zip(p, q, strict=True)) <= step
    for g in range(len(pmo.groups())):
        if g != 11:
            assert pmo.positions(g) == shipped.positions(g)
    assert stage.collision and stage.collision.chunks[1].grid_check().missing == 0
    assert stage.textures == camp.entry(TEXTURES)


def test_texture_import(game: Extracted, camp: StageFile, tmp_path: Path):
    donor = next(
        i
        for i, im in enumerate(Tmh.from_bytes(StageFile.read(game, 139).entry(TEXTURES)).images)
        if (im.width, im.height) == (128, 128)
    )
    e = textures.build(
        camp, [{"op": "texture", "slot": 7, "from": {"stage": 139, "slot": donor}}], tmp_path
    )
    before, after = Tmh.from_bytes(e.original).images, Tmh.from_bytes(e.data).images
    assert len(e.data) == len(e.original) and not e.findings
    assert [a == b for a, b in zip(before, after, strict=True)].count(False) == 1
    assert (after[7].width, after[7].height) == (128, 128) and after[7].decode() != before[
        7
    ].decode()


def test_solid_box_on_the_camp(camp: StageFile, shapes: Path):
    tri = Tri.from_verts(
        (14600.0, 1500.0, 14600.0), (14600.0, 1500.0, 15400.0), (15400.0, 1500.0, 14600.0)
    )
    op = {"op": "collision", "group": None, "add": [[tri.v0, tri.v1, tri.v2]], "chunk": 1}
    p = collision.plan(camp, [op], shapes)
    assert p.collision().chunks[1].tris[-1] == tri
