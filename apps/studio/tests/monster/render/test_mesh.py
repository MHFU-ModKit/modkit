# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from mhfu_studio.monster.core.scene import Scene
from mhfu_studio.monster.render.mesh import (
    ISOLATE_HIDE,
    ISOLATE_ONLY,
    MODE_FLAT,
    MODE_VGROUP,
    default_pose,
    static_attributes,
)
from mhfu_studio.monster.render.skeleton import undriven_geometry, vertex_counts

Lit = Callable[[Any, Any], int]


def test_default_pose_is_posed(rig: Scene) -> None:
    clip, frame = default_pose(rig)
    assert clip is not None and clip.slot == 1 and frame == 20.0
    moved = np.abs(rig.pose(clip, frame).joints - rig.rig.bind_joints).max()
    assert moved > 50.0
    rig.clips.clear()
    assert default_pose(rig) == (None, 0.0)


def test_undriven_geometry(rig: Scene) -> None:
    assert undriven_geometry(rig) == {7: 8}
    assert sum(vertex_counts(rig).values()) == rig.n_vertices


def test_static_attributes(rig: Scene) -> None:
    a = static_attributes(rig)
    assert a.shape == (rig.n_vertices, 4)
    lo, hi = rig.group_range(2)
    assert set(a[lo:hi, 2]) == {2.0} and set(a[lo:hi, 3]) == {7.0}


def test_severed_hides_the_tip(viewport: Any, built: Callable[[str], bytes], lit: Lit) -> None:
    vp = viewport
    counts = []
    for severed in (False, True):
        sc = Scene.from_bytes(built("brute_tigrex"), "brute")
        sc.severed = severed
        vp.set_scene(sc, frame_camera=not severed)  # one camera for both
        vp.show_ground = vp.show_skeleton = False
        vp.camera.look("top")
        vp.draw()
        counts.append(lit(vp.target.read(), vp.background))
    assert counts[1] < counts[0]


def test_modes_and_isolation(viewport: Any, rig: Scene, lit: Lit) -> None:
    vp = viewport
    vp.set_scene(rig)
    vp.show_ground = vp.show_skeleton = False
    vp.camera.look("side")

    def draw() -> Any:
        vp.draw()
        return vp.target.read()

    textured = draw()
    assert lit(textured, vp.background) > 2000
    for mode in (MODE_FLAT, MODE_VGROUP):
        vp.mesh.mode = mode
        assert not np.array_equal(draw(), textured)
    vp.mesh.mode = 0
    vp.tag_joints([3, 4])
    assert vp.mesh.tagged == (3, 4)
    tinted = draw()
    vp.mesh.isolate = ISOLATE_ONLY
    only = lit(draw(), vp.background)
    vp.mesh.isolate = ISOLATE_HIDE
    hide = lit(draw(), vp.background)
    vp.mesh.isolate = 0
    whole = lit(draw(), vp.background)
    assert not np.array_equal(tinted, textured)
    assert 0 < only < whole and 0 < hide < whole and only + hide >= whole * 0.95


def test_posing_moves_pixels(viewport: Any, rig: Scene) -> None:
    vp = viewport
    vp.set_scene(rig)
    vp.show_ground = vp.show_skeleton = False
    pics = []
    for clip, frame in ((rig.clip(1), 0.0), (rig.clip(1), 20.0), (None, 0.0)):
        vp.set_pose(clip, frame)
        vp.draw()
        pics.append(vp.target.read())
    assert not np.array_equal(pics[0], pics[1]) and not np.array_equal(pics[1], pics[2])


def test_picking_round_trips(viewport: Any, rig: Scene) -> None:
    from mhfu_studio.monster.render.skeleton import project

    vp = viewport
    vp.set_scene(rig)
    vp.camera.look("side")
    sk, size = vp.skeleton, vp.target.size
    xy, ok = project(vp.camera, sk.positions, size)
    tried = [j for j in range(len(xy)) if ok[j]]
    assert len(tried) >= 6
    for j in tried:
        got = sk.pick(vp.camera, size, *xy[j], radius=2.0)
        assert got is not None and np.allclose(xy[got], xy[j], atol=2.0)
    assert sk.pick(vp.camera, size, -500.0, -500.0, radius=8.0) is None


def test_tigrex_facts(mhfu_data: Path) -> None:
    from mhfu_port import records
    from mhfu_studio.monster.render.playback import leading_chain

    sc = Scene.from_path(mhfu_data / "file_06185.bin")
    parents = sc.rig.parents.tolist()
    assert records.body_fork(parents) == 2 and leading_chain(parents) == (0, 1, 2)
    orphans = undriven_geometry(sc)
    assert set(orphans) == {46, 47} and sum(orphans.values()) == 150
    clip, frame = default_pose(sc)
    assert clip is not None and clip.slot == 3 and clip.loop and 0 < frame < clip.frames
