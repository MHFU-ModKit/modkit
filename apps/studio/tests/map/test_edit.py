# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import json
from pathlib import Path

import numpy as np
import pytest
from mhfu.files import Extracted
from mhfu_studio.map.core import shapes
from mhfu_studio.map.core.edit import (
    FACE,
    GROUP,
    OBJECT,
    CollisionSelection,
    EditError,
    EditSession,
    Selection,
    apply,
    compose,
    decompose,
    describe_op,
    transform_op,
)
from mhfu_studio.map.core.pick import in_rect, pick, pick_collision, ray_faces
from mhfu_studio.map.core.scene import CLIMB, MapScene, open_stage
from mhfu_studio.stage import mesh
from mhp_formats.pmo import Pmo

QUANTUM = 30000.0 / 32768.0
"""One step of the synthetic stage's positions."""


def test_compose_decompose():
    pivot = (100.0, 20.0, -50.0)
    for by, rot, sc in (
        ((10, 0, 0), (0, 0, 0), (1, 1, 1)),
        ((0, 5, 0), (0, 45, 0), (1, 1, 1)),
        ((3, 4, 5), (10, -20, 30), (2, 0.5, 1.5)),
        ((0, 0, 0), (0, 0, 90), (1, 1, 1)),
    ):
        m = compose(pivot=pivot, by=by, rotate=rot, scale=sc)
        d = decompose(m, pivot)
        assert np.allclose(d["by"], by) and np.allclose(d["rotate"], rot)
        assert np.allclose(d["scale"], sc)
        assert np.allclose(apply(m, np.array([pivot]))[0], np.add(pivot, by), atol=1e-3)
    m = compose(pivot=(1, 2, 3), by=(4, 5, 6), rotate=(10, 20, 30))
    assert np.allclose(mesh.transform_matrix(transform_op((0, 1), [0], m, (1, 2, 3))), m)
    op = transform_op((0, 1), [0], m, (1, 2, 3))
    del op["matrix"]
    assert np.allclose(mesh.transform_matrix(op), m, atol=1e-6)


def test_picking():
    v = np.array([[0, 0, 0], [10, 0, 0], [0, 0, 10]], np.float32)
    t = np.array([[0, 1, 2]], np.int32)
    for d in ((0, -1, 0), (0, 1, 0)):
        tt, hit = ray_faces((2.0, -5.0 * d[1], 2.0), d, v, t)
        assert hit[0] and abs(tt[0] - 5.0) < 1e-6
    assert np.isinf(ray_faces((20.0, 5.0, 20.0), (0, -1, 0), v, t)[0][0])
    p = np.array([[10, 10, 0.5], [50, 50, 0.5], [30, 30, 2.0], [-5, 30, 0.5]])
    assert in_rect(p, 0, 0, 40, 40).tolist() == [True, False, False, False]


def test_pick_scene(scene: MapScene):
    down = (0, -1, 0)
    hit = pick(scene, (400.0, 5000.0, 400.0), down)
    assert hit is not None and hit.key == (0, 1) and abs(hit.point[1] - 100) < 1.0
    under = pick(scene, (400.0, 5000.0, 400.0), down, hidden=[(0, 1)])
    assert under is not None and under.key == (0, 0)
    assert pick(scene, (-8000.0, 5000.0, -8000.0), down) is None
    assert pick(scene, (-8000.0, 5000.0, -8000.0), down, backdrop=True) is not None
    assert pick_collision(scene, (500.0, 100.0, 500.0), down) == (1, 1, pytest.approx(100.0))
    assert pick_collision(scene, (500.0, 100.0, 500.0), down, chunks=[0]) is None


def test_selection_algebra(scene: MapScene):
    a = Selection.object(scene, (0, 1), 0)
    b = Selection.object(scene, (0, 1), 1)
    ab = a.add(b)
    assert ab.n_vertices == 98 and ab.parts[(0, 1)] == [0, 1] and ab.straddling(scene) == 0
    assert ab.n_faces(scene) == 144 and "2 object(s)" in ab.describe(scene)
    back = ab.toggle(scene, b)
    assert back.n_vertices == 49 and back.parts[(0, 1)] == [0]
    assert back.toggle(scene, a).empty
    g = Selection.group(scene, (0, 0))
    assert g.kind == GROUP and g.n_vertices == 81
    assert g.remove(scene, (0, 0), 0).empty
    f = Selection.face(scene, (0, 0), 0)
    assert f.kind == FACE and f.n_vertices == 3 and f.straddling(scene) > 0
    two = f.add(Selection.face(scene, (0, 0), 1))
    assert two.remove(scene, (0, 0), 1).n_vertices == 3
    assert Selection.from_pick(scene, OBJECT, (0, 1), 100).parts == {(0, 1): [1]}
    lo, hi = b.bounds(scene)
    assert np.allclose(hi - lo, (300, 0, 300), atol=1)
    assert Selection().describe(scene) == "nothing selected"


def test_session_preview_commit_undo(scene: MapScene):
    sess = EditSession(scene)
    sel = Selection.object(scene, (0, 1), 1)
    ids = sel.vertices[(0, 1)].copy()
    before = sel.positions(scene).copy()
    m = compose(pivot=sel.centroid(scene), by=(550, 193, -1250), rotate=(0, 45, 0))
    sess.begin(sel)
    sess.preview(compose(by=(1, 0, 0)))
    assert np.allclose(sel.positions(scene)[:, 0], before[:, 0] + 1) and sess.previewing
    ops = sess.commit(m)
    assert [o["op"] for o in ops] == ["transform"] and ops[0]["selection"]["parts"] == [1]
    assert 0 in sess.rebuilt and (0, 1) in sess.dirty and sess.can_undo()
    after = scene.group(0, 1).positions[ids].copy()
    assert np.abs(after - apply(m, before)).max() < QUANTUM
    again = sel.refresh(scene)
    assert again.vertices[(0, 1)].tolist() == ids.tolist() and len(again.parts[(0, 1)]) == 1
    assert sess.undo() == ops and sess.n_steps == 0 and not sess.ops
    assert np.allclose(scene.group(0, 1).positions[ids], before, atol=1e-3)
    assert sess.redo() == ops and len(sess.ops) == 1
    assert np.allclose(scene.group(0, 1).positions[ids], after)
    sess.begin(again)
    sess.preview(compose(by=(9, 9, 9)))
    assert sess.commit(np.eye(4)) == [] and np.allclose(scene.group(0, 1).positions[ids], after)
    b = sess.budget()
    assert b["spent"] == 0 and b["total"] == 128 + 144 + 2 + 8 + 8 and b["free"] == 0
    assert sess.range_check() == {}
    assert "transform" in describe_op(ops[0])


def test_two_transforms_compose(scene: MapScene):
    sess = EditSession(scene)
    sel = Selection.object(scene, (0, 1), 0)
    ids = sel.vertices[(0, 1)]
    before = scene.group(0, 1).positions[ids].copy()
    sess.apply_now(sel, compose(by=(500, 0, 0)))
    sess.apply_now(sel.refresh(scene), compose(by=(0, 300, 0)))
    moved = (scene.group(0, 1).positions[ids] - before).mean(0)
    assert np.abs(moved - (500, 300, 0)).max() < QUANTUM


def test_range_check(scene: MapScene):
    sess = EditSession(scene)
    sess.apply_now(Selection.object(scene, (0, 1), 0), compose(by=(60000, 0, 0)))
    assert sess.range_check() == {} or sess.range_check()[(0, 1)] <= 25


def test_refused_step(scene: MapScene, tmp_path: Path):
    sess = EditSession(scene, base_dir=tmp_path)
    with pytest.raises(EditError, match="group 9"):
        sess.push([{"op": "transform", "sub": 0, "group": 9, "vertices": [0], "by": [1, 0, 0]}])
    with pytest.raises(EditError, match="needs vertices"):
        sess.push([{"op": "transform", "sub": 0, "group": 1}])
    assert not sess.ops and not sess.can_undo()
    assert sess.push([]) == []


def test_add_remove_budget(scene: MapScene, tmp_path: Path):
    sess = EditSession(scene, base_dir=tmp_path)
    sel = Selection.object(scene, (0, 1), 1)
    faces0 = scene.group(0, 1).n_faces
    cap = sess.capacity((0, 1), sel)
    assert cap == 72 and sess.capacity((0, 1)) == 0
    c = sel.centroid(scene)
    v, t, uv = shapes.box(size=(150, 150, 150), at=(c[0], c[1], c[2]))
    name = shapes.next_asset_name(tmp_path, "box")
    shapes.write_obj(tmp_path / name, v, t, uv)
    with pytest.raises(EditError, match="no free primitive"):
        sess.add_mesh((0, 1), name, 12)
    with pytest.raises(EditError, match="needs 80"):
        sess.add_mesh((0, 1), name, 80, sacrifice=sel)
    new = sess.add_mesh((0, 1), name, 12, sacrifice=sel, solid="box", colour=(1, 2, 3))
    assert new.n_vertices == 24 and new.n_faces(scene) == 12
    lo, hi = new.bounds(scene)
    assert np.allclose(hi - lo, (150, 150, 150), atol=2 * QUANTUM)
    assert scene.group(0, 1).n_faces == faces0 - 72 + 12
    b = sess.budget()
    assert b["spent"] == 1 and b["free"] <= cap - 12
    assert sum(ch.n_triangles - ch.n_shipped for ch in scene.collision) == 12
    assert sess.ops[-1]["op"] == "pack" and sess.ops[-1]["prims"] == [6, 7, 8, 9, 10, 11]
    ops = sess.remove(new, solid=True)
    assert ops[0]["op"] == "clear" and ops[0]["solid"] and len(ops[0]["box"]) == 6
    assert scene.group(0, 1).n_faces == faces0 - 72 and sess.budget()["free"] == cap
    with pytest.raises(EditError, match="no whole primitive"):
        sess.remove(Selection.face(scene, (0, 0), 0))
    sess.undo()
    sess.undo()
    assert scene.group(0, 1).n_faces == faces0 and not sess.ops
    assert sum(ch.n_triangles - ch.n_shipped for ch in scene.collision) == 0


def test_add_needs_a_folder(scene: MapScene):
    with pytest.raises(EditError, match="save the document"):
        EditSession(scene).add_mesh((0, 1), "assets/box_1.obj", 12)
    with pytest.raises(EditError, match="save the document"):
        EditSession(scene).import_texture(0, png="a.png")


def test_material_and_texture(scene: MapScene):
    sess = EditSession(scene)
    sess.set_material((0, 1), texture=0)
    assert (
        scene.group(0, 1).texture == 0
        and sess.ops[-1]["mat"] == 1
        and sess.textures_changed is False
    )
    assert sess.material_sharers((0, 0)) == [(0, 2)]
    with pytest.raises(EditError, match="nothing to change"):
        sess.set_material((0, 1))
    sess.undo()
    assert scene.group(0, 1).texture == 1
    px = scene.textures[0].rgba.copy()
    sess.import_texture(0, rgb=(200, 40, 40))
    assert sess.textures_changed and (scene.textures[0].rgba[:, :, :3] != px[:, :, :3]).any()
    sess.undo()
    assert np.array_equal(scene.textures[0].rgba, px)
    with pytest.raises(EditError, match="needs a png"):
        sess.import_texture(0)
    sess.import_texture(1, from_=(139, 0))
    assert sess.findings == [] or all(f.level != "error" for f in sess.findings)


def test_collision_ops(scene: MapScene):
    sess = EditSession(scene)
    sess.set_climbable([(0, 1)])
    assert scene.chunk(0).klass[1] == CLIMB and (0, 1) in scene.climbable()
    sess.set_climbable([(0, 0), (0, 1)], on=False)
    assert not scene.climbable()
    sess.collision_delete([(1, 3)])
    assert not scene.chunk(1).alive[3] and 3 not in scene.chunk(1).cells_of(3)
    sess.collision_box((100, 0, 100), (300, 200, 300), flags={"material": 3}, inflate=5)
    assert sum(c.n_triangles - c.n_shipped for c in scene.collision) == 12
    wall = [[100, 0, 1000], [100, 500, 1000], [600, 500, 1000]]
    sess.collision_add([wall], flags={"material": 10}, chunk=0)
    added = scene.chunk(0).n_triangles - 1
    assert scene.chunk(0).klass[added] == CLIMB
    sess.collision_set(0, [added], verts=[[100, 0, 1000], [100, 600, 1000], [600, 600, 1000]])
    assert scene.chunk(0).verts[added][1][1] == 600 and scene.chunk(0).cells_of(added)
    sess.begin_collision(CollisionSelection([(0, added)]))
    sess.preview(compose(by=(0, 100, 0)))
    assert scene.chunk(0).verts[added][0][1] == 100 and sess.collision_preview
    sess.commit(compose(by=(0, 100, 0)))
    assert abs(scene.chunk(0).verts[added][0][1] - 100) < 0.01
    for args in ((0, []), (0, [1, 2])):
        with pytest.raises(EditError):
            sess.collision_set(*args, verts=wall)
    with pytest.raises(EditError):
        sess.collision_delete([])
    with pytest.raises(EditError):
        sess.collision_add([])
    while sess.undo():
        pass
    assert not sess.ops and scene.chunk(1).alive.all() and scene.chunk(0).n_triangles == 2
    assert sess.climb_warnings([(1, 0)]) and sess.climb_warnings([(0, 0), (0, 1)])


def test_replay(scene: MapScene, synth: object, game: Extracted):
    ops = [
        {"op": "transform", "sub": 0, "group": 1, "vertices": [0, 1], "by": [0, 50, 0]},
        {"op": "transform", "sub": 0, "group": 9, "vertices": [0], "by": [0, 50, 0]},
    ]
    sess = EditSession(scene, ops)
    assert sess.ops is ops and not sess.can_undo()
    assert [f.code for f in sess.findings if f.level == "error"] == ["refused"]
    assert scene.group(0, 1).positions[0][1] == pytest.approx(150, abs=QUANTUM)


def test_session_bytes_are_the_writers(camp: MapScene):
    """What the viewport shows after a commit is what the writer exports."""
    sess = EditSession(open_stage(camp.file.game, 98) if camp.file.game else camp)
    sc = sess.scene
    sel = Selection.object(sc, (0, 11), 17)
    ops = sess.apply_now(
        sel,
        compose(pivot=sel.centroid(sc), by=(550, 193, -1250), rotate=(0, 45, 0), scale=(1.2,) * 3),
    )
    ids = np.asarray(ops[0]["vertices"])
    edit = mesh.edit(sc.file, 0, json.loads(json.dumps(ops)), Path("."))
    assert edit.safe and len(edit.data) == sc.sub_sizes[0]
    pmo = Pmo.from_bytes(edit.data)
    got = np.asarray(pmo.positions(11), np.float32)
    assert np.array_equal(got[ids], sc.group(0, 11).positions[ids])


def test_pick_camp(camp: MapScene):
    s30 = next(x for x in camp.spheres if x.id == 30)
    o, d = (s30.pos[0], 20000.0, s30.pos[2]), (0, -1, 0)
    hit = pick(camp, o, d)
    assert hit is not None and hit.key == (0, 11)
    g11 = camp.group(0, 11)
    assert int((g11.components == g11.components[g11.triangles[hit.face][0]]).sum()) == 169
    below = pick(camp, o, d, hidden=[(0, 11)])
    assert below is not None and below.key != (0, 11) and below.t > hit.t
    col = pick_collision(camp, o, d, chunks=[1])
    assert col is not None and col[0] == 1
