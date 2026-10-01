# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from mhfu import files
from mhfu.files import Extracted
from mhfu_studio.map.core.scene import (
    CLIMB,
    FLOOR,
    SINK,
    WALL,
    MapScene,
    SceneError,
    faces,
    open_stage,
    weld_components,
)
from mhfu_studio.stage import collision as C
from mhfu_studio.stage.file import TERRAIN, StageFile
from mhp_formats.fu.stage import Stage


def test_weld():
    pos = np.array(
        [
            [0, 0, 0],
            [1, 0, 0],
            [0, 1, 0],
            [5, 5, 5],
            [6, 5, 5],
            [5, 6, 5],
            [1, 0, 0],
            [2, 0, 0],
            [1, 1, 0],
        ],
        np.float32,
    )
    comp, n = weld_components(pos, np.array([[0, 1, 2], [3, 4, 5], [6, 7, 8]], np.int32))
    assert n == 2 and len(set(comp[[0, 1, 2, 6, 7, 8]])) == 1 and comp[3] != comp[0]
    assert (
        weld_components(np.array([[0, 0, 0], [9, 9, 9]], np.float32), np.zeros((0, 3), int))[1] == 2
    )
    assert weld_components(np.zeros((0, 3), np.float32), np.zeros((0, 3), int))[1] == 0


def test_faces_per_primitive(synth: SimpleNamespace):
    b = synth.block([(3, (0.0, 0.0, 0.0))])
    b.indices[0:2] = [b.indices[0]] * 2  # the first strip's first triangle collapses
    tris, owner, prims = faces(b)
    assert prims == [(4, 6), (4, 6)]
    assert len(tris) == 7 and owner.tolist() == [0, 0, 0, 1, 1, 1, 1]
    assert tris[0].tolist() == [1, 0, 4]  # a strip alternates: the second triangle swaps


def test_synthetic(scene: MapScene, synth: SimpleNamespace):
    assert scene.name == "Pokke village" and scene.fog_rgba == tuple(synth.FOG)
    assert [g.key for g in scene.groups] == [(0, 0), (0, 1), (0, 2), (0, 3), (2, 0)]
    floor, crates, far, plain = scene.terrain
    assert floor.texture == 0 and crates.texture == 1 and plain.untextured and plain.texture is None
    assert crates.n_components == 2 and floor.n_components == 1
    assert far.backdrop and far.untextured and not floor.backdrop and not crates.backdrop
    assert crates.budget.triangles == 144 and len(crates.prims) == 12
    assert crates.free_prims().tolist() == []
    assert floor.colours is not None and floor.colours.shape == (81, 4) and floor.uvs is not None
    assert np.allclose(floor.positions[80], (2000, 0, 2000), atol=1.0)
    assert {t.index: (t.width, t.height, t.colours) for t in scene.textures} == {
        0: (16, 16, 256),
        1: (32, 8, 16),
    }
    walls, ground = scene.collision
    assert walls.klass.tolist() == [CLIMB, WALL]
    assert ground.klass.tolist() == [FLOOR, FLOOR, SINK, FLOOR]
    assert scene.climbable() == [(0, 0)]
    assert scene.floor_height(500.0, 500.0) == 0.0 and scene.floor_height(-50.0, 0.0) is None
    lo, hi = scene.playable_bounds()
    assert lo.tolist() == [0, 0, 0] and hi.tolist() == [2000, 0, 2000]
    assert scene.group(0, 1) is crates and scene.chunk(1) is ground
    with pytest.raises(KeyError):
        scene.group(2, 9)
    assert "pack budget" in scene.summary()


def test_objects_and_primitives(scene: MapScene):
    g = scene.group(0, 1)
    second = g.component_vertices(1)
    assert len(second) == 49 and len(g.component_faces(1)) == 72
    assert g.prims_of_vertices(second).tolist() == [6, 7, 8, 9, 10, 11]
    assert g.prim_capacity([6, 7, 99]) == 24
    assert len(g.prim_faces([6])) == 12


def test_collision_plan(scene: MapScene, tmp_path: Path):
    ops = [
        {"op": "collision", "group": None, "chunk": 1, "tris": [1], "delete": True},
        {"op": "collision", "group": None, "solid_box": [100, 0, 100, 300, 200, 300]},
    ]
    scene.replace_collision(C.plan(scene.file, ops, tmp_path))
    walls, ground = scene.collision
    assert (
        not ground.alive[1] and ground.n_shipped == 4 and ground.n_alive == ground.n_triangles - 1
    )
    assert walls.n_triangles + ground.n_triangles == 6 + 12
    scene.replace_collision(None)
    assert scene.chunk(1).alive.all()


def test_placeholder(game: Extracted):
    stub = Stage().to_bytes()
    game.path(files.stage_pac(131)).write_bytes(stub)
    with pytest.raises(SceneError, match="placeholder"):
        open_stage(game, 131)
    with pytest.raises(SceneError):
        open_stage(game, 132)


def test_open_without_overlay(game: Extracted, synth: SimpleNamespace):
    sc = open_stage(game, synth.STAGE)
    assert not sc.exits and sc.surface_table is None and sc.notes[0].startswith("overlay")
    assert sc.chunk(1).klass.tolist() == [FLOOR] * 4  # no surface table: no sink


def test_replace_sub(scene: MapScene):
    pmo = scene.file.pmo(TERRAIN)
    assert pmo is not None
    pmo.groups()[0].block.vertices.position[0] = (0, 3000, 0)
    scene.replace_sub(0, pmo.to_bytes())
    assert scene.group(0, 0).positions[0].tolist() == pytest.approx([0, 2746.58, 0], abs=0.01)


# the shipped stages


def test_camp(camp: MapScene):
    t = camp.terrain
    assert len(t) == 13 and len(camp.props) == 7
    assert sum(g.n_vertices for g in t) == 16502 and sum(g.n_faces for g in t) == 12597
    assert sum(g.budget.triangles for g in t) == 12597
    assert camp.fog_rgba == (170, 204, 255, 255) and camp.name == "Snowy base camp"
    assert camp.sub_sizes[:2] == [263104, 123472]
    assert [camp.group(0, g).n_components for g in (11, 9, 10)] == [186, 158, 569]
    sizes = np.bincount(camp.group(0, 9).components)
    assert sizes.min() == sizes.max() == 4
    assert np.bincount(camp.group(0, 11).components).max() == 591
    assert [camp.group(0, g).texture for g in (11, 8, 10, 12, 9)] == [7, 3, 3, 4, 5]
    t3, t7 = camp.texture(3), camp.texture(7)
    assert t3 is not None and (t3.width, t3.height, t3.colours, t3.mode) == (128, 128, 256, 5)
    assert t7 is not None and (t7.colours, t7.mode) == (16, 4) and len(camp.textures) == 16
    assert all(g.colours is not None and g.uvs is not None for g in camp.groups)
    assert not camp.notes


def test_camp_collision(camp: MapScene):
    assert [c.n_triangles for c in camp.collision] == [254, 503]
    floor = camp.floor
    assert floor is not None and (floor.klass == FLOOR).sum() == 495
    assert (floor.grid, floor.cell, floor.origin) == ((40, 40), (501, 501), (0, 0))
    assert not camp.climbable()
    y = camp.floor_height(15167.6, 14539.6)
    assert y is not None and abs(y + 140.0 - 1338.4) < 40.0
    assert camp.floor_height(-99999.0, -99999.0) is None


def test_mesh_and_collision_share_a_frame(camp: MapScene):
    s24 = next(s for s in camp.spheres if s.id == 24)
    d = np.linalg.norm(camp.group(0, 11).positions - np.array(s24.pos, np.float32), axis=1)
    assert (d < s24.radius * 2).sum() > 100
    g4 = camp.group(0, 4)
    rng = np.random.default_rng(0)
    diffs = [
        abs(f - y)
        for x, y, z in g4.positions[rng.choice(g4.n_vertices, 50, replace=False)]
        if (f := camp.floor_height(float(x), float(z))) is not None
    ]
    assert len(diffs) > 30 and np.median(diffs) < 60.0


def test_camp_overlay(camp: MapScene):
    (e,) = camp.exits
    assert (e.target, e.target_name, e.trigger, e.radius) == (
        99,
        "Snowy area 1",
        (13200.0, 0.0, 13900.0),
        500.0,
    )
    assert abs(e.yaw_degrees - 270.0) < 0.01
    s = {x.id: x for x in camp.spheres}[24]
    assert (s.pos, s.radius) == ((14450.0, 1000.0, 16250.0), 200.0)
    assert camp.surface_table is not None and camp.surface_table[:2] == [0, 1]
    assert all(g.backdrop for g in camp.terrain if g.vg_rec in (0, 1, 2))
    assert not any(g.backdrop for g in camp.terrain if g.vg_rec in (4, 5, 6, 8, 9, 10, 11, 12))


def test_other_stages(shipped: Extracted):
    village = open_stage(shipped, 139)
    assert len(village.terrain) == 79 and sum(g.n_faces for g in village.terrain) == 11251
    assert len(village.props) == 27 and len(village.textures) == 42 and not village.exits
    assert village.floor is not None and (village.floor.klass == FLOOR).sum() == 558
    s2 = open_stage(shipped, 2)
    assert [s2.group(0, g).material for g in (13, 3, 26)] == [3, 3, 1]
    assert len(s2.materials[0]) == 25
    s4 = open_stage(shipped, 4)
    assert (s4.group(0, 0).material, s4.group(0, 1).material) == (3, 0)
    s95 = open_stage(shipped, 95)
    assert s95.group(2, 12).untextured and s95.group(2, 12).texture is None
    assert len(s95.climbable()) == 9
    assert all(s95.chunk(c).material[t] == 10 for c, t in s95.climbable())
    s94 = open_stage(shipped, 94)
    assert len(s94.climbable()) == 2 and all(
        s94.chunk(c).material[t] == 9 for c, t in s94.climbable()
    )
    assert not open_stage(shipped, 99).climbable()
    with pytest.raises(SceneError):
        open_stage(shipped, 131)


def test_climbable_census(shipped: Extracted):
    """253 triangles over 34 stages: CLIMB needs no surface table, so the files suffice."""
    from mhfu_studio.map.core.scene import _decode_collision

    tris = stages = 0
    for n in files.STAGES[1:]:
        st = StageFile.read(shipped, n)
        if st.stage.collision is None:
            continue
        found = sum(len(c.climbable()) for c in _decode_collision(st.chunks, None))
        stages += bool(found)
        tris += found
    assert (tris, stages) == (253, 34)


def test_added_triangles_follow_the_shipped(shipped: Extracted, tmp_path: Path):
    add = [[[0, 0, 0], [0, 500, 0], [500, 500, 0]]]
    sc = open_stage(shipped, 98)
    sc.replace_collision(
        C.plan(sc.file, [{"op": "collision", "group": None, "chunk": 0, "add": add}], tmp_path)
    )
    walls = sc.chunk(0)
    assert (walls.n_triangles, walls.n_shipped) == (255, 254) and walls.klass[254] == WALL
