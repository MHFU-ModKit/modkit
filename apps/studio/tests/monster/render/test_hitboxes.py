# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from collections.abc import Callable
from typing import Any

import numpy as np
from mhfu.em.intel import AttackSet, HitSphere
from mhfu_port.manifest import Hitbox, Hurtbox
from mhfu_studio.monster.core.scene import Scene
from mhfu_studio.monster.render.hitboxes import (
    Volume,
    attack_volumes,
    capsule_surface,
    group_color,
    set_color,
    sphere_surface,
    volumes,
)

Lit = Callable[[Any, Any], int]


def test_surfaces_lie_on_the_radius() -> None:
    s = sphere_surface((10.0, -5.0, 2.0), 100.0)
    assert len(s) % 3 == 0 and np.allclose(np.linalg.norm(s - (10.0, -5.0, 2.0), axis=1), 100.0)
    a, b = np.zeros(3), np.array([30.0, 0.0, 300.0])
    c = capsule_surface(a, b, 50.0)
    t = np.clip(((c - a) @ (b - a)) / float((b - a) @ (b - a)), 0.0, 1.0)
    assert np.allclose(np.linalg.norm(c - (a + t[:, None] * (b - a)), axis=1), 50.0)
    tri = t.reshape(-1, 3)
    assert ((tri.min(axis=1) < 0.05) & (tri.max(axis=1) > 0.95)).any(), "no tube"
    assert len(capsule_surface((1, 2, 3), (1, 2, 3), 10.0)) == len(sphere_surface((1, 2, 3), 10.0))


def test_adapters_group_by_set_and_part() -> None:
    vs = volumes(
        [
            Hitbox(bone=10, radius=150.0, set=2),
            Hitbox(bone=127, radius=300.0, set=5),
            Hitbox(bone=125, radius=0.0, set=2, shape="capsule", to=[0.0, 0.0, 0.0]),
            Hurtbox(bone=3, radius=1.0, part=6, hitzone_row=5),
            Hurtbox(bone=3, radius=1.0, part=9, shape="capsule", to=[1.0, 2.0, 3.0]),
        ]
    )
    assert [v.group for v in vs] == [2, 5, 2, 6, 1]
    assert [v.part for v in vs] == [0, 0, 0, 6, 1]
    assert vs[1].is_node_space and vs[1].is_marker and not vs[2].is_node_space
    assert vs[4].b == (1.0, 2.0, 3.0) and vs[3].b is None
    host = attack_volumes(
        [
            AttackSet(2, 0, (HitSphere(10, 0, 0, 150.0), HitSphere(18, 0, 0, 150.0))),
            AttackSet(11, 0, (HitSphere(35, 0, 0, 300.0),)),
        ]
    )
    assert [(v.bone, v.group) for v in host] == [(10, 2), (18, 2), (35, 11)]
    assert volumes([HitSphere(4, 9, 2, 5.0)])[0].group == 1
    cols = [set_color(g) for g in range(56)]
    for i in range(56):
        for j in range(i + 1, min(56, i + 4)):
            assert max(abs(x - y) for x, y in zip(cols[i], cols[j], strict=True)) > 0.08
    assert group_color(9, "part") == group_color(1, "part")
    assert group_color(9, "set") != group_color(1, "set")


def test_hurtboxes_ride_the_pose(viewport: Any, rig: Scene, lit: Lit) -> None:
    vp = viewport
    vp.set_scene(rig)
    vols = [
        Volume(2, 97.0, 1, 2, (0.0, -30.0, 30.0), group=1),
        Volume(3, 65.0, 4, 5, (35.0, 0.0, 0.0), (0.0, 0.0, 180.0), group=4),
        Volume(rig.rig.n + 5, 50.0, 7, group=7),
    ]

    def count() -> int:
        vp.draw()
        return lit(vp.target.read(), vp.background)

    vp.show_mesh = vp.show_skeleton = vp.show_ground = False
    assert count() == 0
    ov = vp.set_hitboxes(vols)
    assert ov is not None and vp.show_hitboxes
    assert len(ov.orphans) == 1 and len(ov.shown()) == 2 and count() > 0
    vp.set_pose(rig.clip(1), 0.0)
    before = ov.world_centres().copy()
    vp.set_pose(rig.clip(1), 20.0)
    after = ov.world_centres()
    assert not np.allclose(before, after)
    j3 = vp.skeleton.positions[3]
    vp.set_pose(rig.clip(1), 0.0)
    j3_0 = vp.skeleton.positions[3]
    assert not np.allclose(after[1] - j3, before[1] - j3_0, atol=1e-6), "rotation ignored"

    ov.set_visible([1])
    assert [v.part for v in ov.shown()] == [1]
    one = count()
    ov.set_visible(None)
    assert count() > one
    ov.set_visible([1])
    outline = count()
    ov.set_selected_group(1)
    filled = count()
    assert ov.filled > 0 and filled > outline
    ov.set_visible([4])
    dimmed = count()
    ov.set_selected_group(None)
    assert count() > dimmed
    ov.set_visible(None)
    ov.set_selected_volume(1)
    count()
    assert ov.filled > 0
    ov.set_selected_volume(9)
    assert ov.selected_volume is None

    ov.set_selected_group(1)
    vp.set_reference_hitboxes(vols[:2])
    assert vp.reference is None
    ref = vp.set_reference(rig)
    assert ref.hitboxes is not None and ref.hitboxes.selected_group == 1
    shown = len(ref.hitboxes.shown())
    with_ref = count()
    vp.set_reference_hitboxes(None)
    assert vp.reference.hitboxes is None and with_ref > count()
    vp.clear_reference()
    vp.set_reference(rig)
    vp.set_reference_hitboxes(vols[:2])
    assert vp.reference.hitboxes.selected_group == 1
    assert len(vp.reference.hitboxes.shown()) == shown
    vp.clear_hitboxes()
    assert vp.hitboxes is None and not vp.show_hitboxes and vp.reference.hitboxes is None
    assert count() == 0


def test_attack_volumes_by_set(viewport: Any, rig: Scene, lit: Lit) -> None:
    vp = viewport
    vp.set_scene(rig)
    n = rig.rig.n
    vols = volumes(
        [
            Hitbox(bone=3, radius=150.0, set=2),
            Hitbox(bone=4, radius=120.0, set=2, shape="capsule", to=[0.0, 0.0, -200.0]),
            Hitbox(bone=6, radius=200.0, set=3),
            Hitbox(bone=127, radius=250.0, set=4),
            Hitbox(bone=125, radius=0.0, set=2, shape="capsule", to=[0.0, 0.0, 0.0]),
            Hitbox(bone=n + 3, radius=50.0, set=2),
        ]
    )

    def count() -> int:
        vp.draw()
        return lit(vp.target.read(), vp.background)

    vp.show_mesh = vp.show_skeleton = vp.show_ground = False
    ov = vp.set_attacks(vols)
    assert ov is not None and ov.palette == "set" and vp.show_attacks and not vp.show_hitboxes
    assert [v.bone for v in ov.orphans] == [n + 3]
    assert [v.bone for v in ov.shown()] == [3, 4, 6, 127] and ov.groups() == [2, 3, 4]
    c = ov.world_centres()
    assert np.allclose(c[3], 0.0)
    everything = count()
    vp.set_pose(rig.clip(1), 0.0)
    c2 = ov.world_centres()
    assert np.allclose(c2[3], 0.0) and not np.allclose(c[0], c2[0])
    ov.set_visible([3])
    assert [v.group for v in ov.shown()] == [3] and count() < everything
    ov.set_visible(None)
    ov.set_selected_group(2)
    vp.set_hitboxes(volumes([Hurtbox(bone=2, radius=90.0, part=1)]))
    both = count()
    vp.show_attacks = False
    assert count() < both
    vp.show_attacks = True
    vp.set_reference_attacks(vols[:3])
    ref = vp.set_reference(rig)
    assert ref.attacks is not None and ref.attacks.selected_group == 2
    vp.clear_attacks()
    assert vp.attacks is None and vp.reference.attacks is None
    vp.show_hitboxes = False
    vp.clear_reference()
    assert count() == 0
