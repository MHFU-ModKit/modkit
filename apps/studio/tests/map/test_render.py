# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from mhfu.files import Extracted
from mhfu_studio.harness.stats import compare_all, load_golden, measure
from mhfu_studio.map.core.atlas import Atlas
from mhfu_studio.map.core.scene import MapScene
from mhfu_studio.map.render.headless import Layers, tiles, views
from mhfu_studio.map.render.stage_mesh import HL_SELECTED
from mhfu_studio.map.render.viewport import EYE_HEIGHT, MapViewport, hunter
from mhfu_studio.shell.camera import Bounds, OrbitCamera

GOLDEN = Path(__file__).parent / "golden"
VIEWS = ("iso", "top", "hunter")
SIZE = (320, 200)


def coverage(img: np.ndarray, rgba: tuple[float, ...]) -> float:
    bg = np.round(np.asarray(rgba[:3]) * 255)
    return float((np.abs(img[:, :, :3].astype(int) - bg).max(axis=2) > 12).mean())


def changed(a: np.ndarray, b: np.ndarray) -> float:
    return float((np.abs(a.astype(int) - b.astype(int)).max(axis=2) > 8).mean())


def matches(shots: Any, golden: Path) -> list[str]:
    stats = {k: measure(v, shots.clear, shots.renderer) for k, v in shots.images.items()}
    return [str(f) for f in compare_all(stats, load_golden(golden)) if f.level != "info"]


def test_hunter():
    cam = OrbitCamera().frame(Bounds(np.zeros(3), np.array([1000.0, 300.0, 1000.0])))
    hunter(cam, (100.0, 50.0, 200.0), 0.0)
    assert np.allclose(cam.eye, (100.0, 50.0 + EYE_HEIGHT, 200.0))
    assert np.allclose(cam.forward, (0.0, 0.0, 1.0)) and cam.pitch == 0.0
    hunter(cam, (0.0, 0.0, 0.0), 90.0)
    assert np.allclose(cam.forward, (1.0, 0.0, 0.0), atol=1e-9)


def test_layers_change_pixels(gl: Any, scene: MapScene, synth: Any):
    arrivals = [(98, synth.EXITS[0])]
    with MapViewport(gl, SIZE, samples=0) as vp:
        vp.set_scene(scene, arrivals)
        assert vp.clear_color()[:3] == tuple(c / 255 for c in synth.FOG[:3])
        vp.camera.look("iso")
        vp.draw()
        base = vp.target.read()
        assert coverage(base, vp.clear_color()) > 0.2
        for flip in ("show_mesh", "show_collision", "show_lattice", "show_bounds", "show_axes"):
            setattr(vp, flip, not getattr(vp, flip))
            vp.draw()
            assert changed(base, vp.target.read()) > 0, flip
            setattr(vp, flip, not getattr(vp, flip))
        assert vp.mesh is not None
        vp.mesh.hide_group((0, 0), True)
        vp.draw()
        assert changed(base, vp.target.read()) > 0.01
        vp.mesh.hide_group((0, 0), False)
        for g in scene.terrain:
            vp.mesh.highlight(g.key, range(g.n_vertices), HL_SELECTED, replace=False)
        vp.draw()
        assert changed(base, vp.target.read()) > 0.01
        vp.mesh.clear_highlight()
        vp.mesh.select_group((0, 1))
        vp.draw()
        assert changed(base, vp.target.read()) > 0.001
        vp.mesh.select_group(None)
        vp.draw()
        assert changed(base, vp.target.read()) == 0
        assert [lb.kind for lb in vp.labels()] == ["exit", "arrival", "sphere"]
        vp.show_labels = False
        assert vp.labels() == []
        assert vp.stand_at_entry() and vp.camera.pitch == 0.0
        assert np.allclose(vp.camera.eye, (10.0, EYE_HEIGHT, 10.0))


def test_edits_reach_the_gpu(gl: Any, scene: MapScene, tmp_path: Path):
    from mhfu_studio.map.core.edit import EditSession, Selection, compose

    with MapViewport(gl, SIZE, samples=0) as vp:
        vp.set_scene(scene)
        vp.camera.look("top")
        vp.draw()
        base = vp.target.read()
        sess = EditSession(scene, base_dir=tmp_path)
        sess.apply_now(Selection.object(scene, (0, 1), 1), compose(by=(0, 0, -900)))
        assert vp.mesh is not None and vp.collision is not None
        vp.mesh.rebuild(sess.rebuilt)
        vp.draw()
        assert changed(base, vp.target.read()) > 0.001
        sess.import_texture(0, rgb=(255, 0, 0))
        vp.mesh.reload_textures()
        vp.draw()
        red = vp.target.read()
        assert changed(base, red) > 0.1
        sess.collision_delete([(1, 0)])
        vp.show_collision = True
        vp.collision.rebuild()
        vp.collision.select([(1, 1)])
        vp.collision.hover((1, 2))
        vp.collision.set_class("floor", False)
        vp.draw()
        assert vp.mesh.gl_texture(0) is not None and "StageMesh" in repr(vp.mesh)


def test_synthetic_golden(gl: Any, scene: MapScene, synth: Any):
    shots = views(scene, VIEWS, arrivals=[(98, synth.EXITS[0])], size=SIZE, ctx=gl)
    assert matches(shots, GOLDEN / "synthetic.json") == []


def test_tiles(gl: Any, game: Extracted, synth: Any):
    scenes = [(synth.build(game, n), []) for n in (synth.STAGE, synth.OTHER)]
    shots = tiles(scenes, "iso", layers=Layers(collision=True), size=(160, 100), ctx=gl)
    assert list(shots.images) == ["st139", "st098"] and shots.clear[:3] == (0.10, 0.11, 0.13)


@pytest.mark.parametrize("stage", [98, 139])
def test_shipped_golden(gl: Any, shipped: Extracted, stage: int):
    from mhfu_studio.map.core.scene import open_stage

    shots = views(
        open_stage(shipped, stage),
        VIEWS,
        arrivals=Atlas(shipped).arrivals(stage),
        size=SIZE,
        ctx=gl,
        label=lambda v: f"st{stage:03d}_{v}",
    )
    assert coverage(next(iter(shots.images.values())), shots.clear) > 0.5
    assert matches(shots, GOLDEN / f"st{stage:03d}.json") == []
