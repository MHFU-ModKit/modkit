# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import numpy as np
import pytest
from mhfu.files import monster_pac
from mhfu_port import manifest, verify
from mhfu_studio.monster.core.scene import MHFU, MHP3RD, Scene, SceneError, open_scene
from mhp_formats import Pac

NAMES = "\n[clips.walk]\nslot = 1\nframes = 10\nloop = true\n\n[clips.nod]\nslot = 2\nframes = 9\n"


@pytest.fixture
def scene(synthetic_pac, tmp_path):
    path = tmp_path / "file_09999.bin"
    path.write_bytes(synthetic_pac)
    return open_scene(path)


def test_synthetic(scene):
    assert scene.game == MHFU and scene.rig.n == 3 and scene.build_id.startswith("file_09999.bin@")
    (g,) = scene.groups
    assert g.n_vertices == 3 and g.n_faces == 1 and g.material == 0 and g.texture == 0
    assert np.allclose(g.positions[1], (0.0, 100.0, 50.0), atol=0.01)
    assert g.uvs.shape == (3, 2) and np.allclose(g.uvs[1], (1.0, 0.0), atol=1e-3)
    (t,) = scene.textures
    assert (t.width, t.height) == (2, 2) and t.rgba[0, 1].tolist() == [4, 5, 6, 7]
    assert scene.clip_table() == {1: (10, True), 2: (6, False)}
    walk, head = scene.clips
    assert walk.whole_rig and walk.driven == (0, 1, 2) and walk.tracks == 3
    assert not head.whole_rig and head.driven == (2,) and "are partial" in scene.summary()


def test_pose(scene):
    p = scene.pose(1, 10)
    assert np.allclose(p.joints[2], (50.0, 100.0, 0.0), atol=0.05), "joint 1 turned 90 degrees"
    bent = p.skin(scene.merged)
    assert np.allclose(bent[1], (50.0, 100.0, 0.0), atol=0.05) and np.allclose(bent[0], g0(scene))
    half = scene.pose(2, 3)
    assert np.allclose(half.joints, scene.rig.bind_joints), "a partial clip leaves the rest at bind"
    assert np.allclose(scene.bind_pose().joints, scene.rig.bind_joints)
    assert scene.group_range(0) == (0, 3)


def g0(scene):
    return scene.groups[0].positions[0]


def test_names(scene, make):
    with pytest.raises(KeyError):
        scene.clip("walk")
    scene.attach_manifest(make(NAMES))
    assert scene.clip("walk").slot == 1 and scene.clip(2).name == "nod"
    assert scene.clip_mismatches() == ["clips.nod: manifest says 9 frames, the PAC has 6"]
    with pytest.raises(KeyError):
        scene.clip(7)


def test_refused():
    with pytest.raises(SceneError, match="not a model PAC"):
        Scene.from_bytes(b"\0" * 32, "x")
    with pytest.raises(SceneError, match="no skeleton"):
        Scene.from_bytes(Pac([b"abc" * 8]).to_bytes(), "x")


def test_tigrex(games):
    sc = open_scene(games.fu.path(monster_pac(75)))
    assert (sc.rig.n, len(sc.groups), sc.n_vertices, len(sc.clips)) == (48, 214, 4128, 64)
    assert [c.slot for c in sc.clips if not c.whole_rig] == [24, 25]
    assert len(sc.textures) == 5 and all(g.texture is not None for g in sc.groups)
    assert sorted({g.texture for g in sc.groups}) == [0, 1, 2, 3, 4]


@pytest.mark.parametrize(
    ("name", "joints", "groups", "vertices", "pad"),
    [("zinogre", 51, 181, 4336, 0), ("brute_tigrex", 46, 88, 2862, 1)],
)
def test_donor_matches_port(games, built, ports, name, joints, groups, vertices, pad):
    """The donor read as the porter reads it is the geometry the port carries, and the port
    plays its donor's moveset joint for joint."""
    m = manifest.load(ports / f"{name}.toml")
    src = Scene.from_manifest(m, side="source", data=games)
    port = Scene.from_bytes(built(name), name, manifest=m)
    assert src.game == MHP3RD and src.rig.n == joints and src.record_to_bone
    assert (
        (len(src.groups), src.n_vertices)
        == (len(port.groups), port.n_vertices)
        == (
            groups,
            vertices,
        )
    )
    for a, b in zip(src.groups, port.groups, strict=True):
        assert np.allclose(a.positions, b.positions, atol=1e-6) and a.texture == b.texture
    c = verify.correspondence(src.skeleton, port.skeleton)
    assert c.pad == pad
    s_idx = np.array(sorted(c.tree))
    p_idx = np.array([c.tree[i] for i in s_idx])
    worst, compared = 0.0, 0
    for clip in src.clips:
        pc = port._by_slot.get(clip.slot)
        if pc is None or not pc.whole_rig:
            continue
        for frame in (clip.frames // 4, clip.frames // 2):
            a = src.pose(clip, frame).joints[s_idx]
            b = port.pose(pc, frame).joints[p_idx]
            a[:, 1] += m.build.ground_lift
            worst = max(worst, float(np.linalg.norm(a - b, axis=1).max()))
            compared += 1
    assert compared > 60 and worst < 0.05, (compared, worst)


def test_manifest_port(games, ports):
    sc = open_scene(ports / "zinogre.toml", data=games)
    assert sc.build_id.startswith("zinogre.bin@") and not sc.clip_mismatches()
    assert sc.clip("welcome_howl").slot == 2
