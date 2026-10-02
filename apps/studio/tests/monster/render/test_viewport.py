# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from mhfu_studio.monster.core.scene import Scene
from mhfu_studio.monster.render.viewport import MonsterViewport, scene_bounds

Lit = Callable[[Any, Any], int]


def test_draws_and_layers_off_draw_nothing(viewport: Any, rig: Scene, lit: Lit) -> None:
    vp = viewport
    vp.set_scene(rig)
    assert np.allclose(vp.camera.target, vp.bounds().center)
    assert not np.allclose(vp.bounds().center, scene_bounds(rig).center), "posed == bind"
    vp.show_axes = vp.show_bounds = vp.show_points = True
    vp.draw()
    assert lit(vp.target.read(), vp.background) > 500
    vp.show_ground = vp.show_axes = vp.show_points = vp.show_bounds = False
    vp.show_mesh = vp.show_skeleton = False
    vp.draw()
    assert lit(vp.target.read(), vp.background) == 0


def test_bind_and_unposed(viewport: Any, rig: Scene) -> None:
    viewport.set_scene(rig, posed=False, frame_camera=False)
    assert viewport.clip is None and viewport.frame == 0.0
    assert np.allclose(viewport.skeleton.positions, rig.rig.bind_joints)


def test_reference_beside_the_port(viewport: Any, rig: Scene, lit: Lit) -> None:
    vp = viewport
    vp.set_scene(rig)
    alone = vp.bounds()
    vp.draw()
    lit_alone = lit(vp.target.read(), vp.background)
    ref = vp.set_reference(rig)
    assert ref.offset[0] > alone.radius and ref.offset[1] == ref.offset[2] == 0
    both = vp.bounds()
    assert both.radius > alone.radius and both.hi[0] > alone.hi[0]
    vp.draw()
    assert lit(vp.target.read(), vp.background) > lit_alone
    vp.play_reference_clip(rig.clip(2), 0.0)
    assert ref.playback.end == 30
    vp.playback.speed = 3.0
    vp.playback.play()
    assert vp.tick(1.0)
    assert ref.playback.speed == 3.0 and ref.playback.phase > 0
    vp.play_clip(rig.clip(1))
    assert vp.playback.phase == 0.0 and ref.playback.phase == 0.0
    assert ref.clip is not None and ref.clip.slot == 2
    vp.strip_root = True
    assert ref.strip_root and vp.strip_root
    vp.clear_reference(frame_camera=True)
    assert vp.reference is None and np.allclose(vp.bounds().hi, vp.mesh.bounds.hi)


def test_transport_drives_the_mesh(viewport: Any, rig: Scene) -> None:
    vp = viewport
    vp.set_scene(rig)
    vp.play_clip(rig.clip(1), 0)
    assert not vp.tick(1 / 60.0), "paused"
    vp.playback.play()
    assert not vp.tick(1 / 60.0), "half a game frame is carried"
    assert vp.tick(1 / 60.0) and vp.frame == vp.playback.phase > 0
    vp.draw()
    a = vp.target.read()
    for _ in range(10):
        vp.tick(1 / 30.0)
    vp.draw()
    assert not np.array_equal(a, vp.target.read())
    vp.play_clip(None)
    assert vp.clip is None and vp.playback.end == 0


def test_no_scene(viewport: Any) -> None:
    vp = viewport
    assert vp.scene is None and vp.clip is None and vp.selected_joint is None
    assert vp.set_hitboxes([]) is None and not vp.tick(0.1)
    vp.draw()
    with pytest.raises(RuntimeError):
        _ = vp.playback


def _every_clip(gl: Any, sc: Scene, lit: Lit) -> float:
    n = 0
    t0 = time.perf_counter()
    with MonsterViewport(gl, (240, 180), 0) as vp:
        vp.set_scene(sc)
        for c in sc.clips:
            vp.play_clip(c)
            assert vp.playback.end == c.frames and vp.playback.loop == c.loop
            for f in np.linspace(0.0, c.frames, 4):
                vp.set_pose(c, float(f))
                j = vp.skeleton.positions
                assert np.isfinite(j).all() and np.abs(j).max() < 1e7, (c.slot, f)
                vp.draw()
                n += 1
            assert lit(vp.target.read(), vp.background) > 200, c.slot
    return n / (time.perf_counter() - t0)


def test_every_clip_plays_tigrex(gl: Any, mhfu_data: Path, lit: Lit) -> None:
    assert _every_clip(gl, Scene.from_path(mhfu_data / "file_06185.bin"), lit) > 20.0


@pytest.mark.parametrize("name", ["zinogre", "brute_tigrex"])
def test_every_clip_plays_port(gl: Any, built: Callable[[str], bytes], name: str, lit: Lit) -> None:
    assert _every_clip(gl, Scene.from_bytes(built(name), name), lit) > 20.0
