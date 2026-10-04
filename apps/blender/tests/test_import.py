# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""File > Import inside Blender: the scene it builds, its poses against the engine's FK, and its
Actions read back into the clips they came from."""

from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest
from mhfu_port import fk, motion
from mhfu_port.model import Model

POSITION = 1e-2
"""Joint positions in game units: 1e-4 of a Blender unit at the default import scale. Blender
poses in float32; the largest monsters land within 2e-3."""
ROTATION = 1e-4


@pytest.fixture
def scene(bpy: ModuleType, extension: str) -> Iterator[Any]:
    """An empty scene, the extension enabled."""
    bpy.ops.wm.read_homefile(use_empty=True)
    yield bpy.context.scene


def load(bpy: ModuleType, path: Path, **options: Any) -> Any:
    assert bpy.ops.import_scene.mhfu_model(filepath=str(path), **options) == {"FINISHED"}
    return bpy.context.view_layer.objects.active


def posed(bpy: ModuleType, owner: Any, joint: str, frame: float) -> np.ndarray:
    """Each joint's pose matrix; `pose.bones` runs in tree order, not joint order."""
    bpy.context.scene.frame_set(int(frame), subframe=frame - int(frame))
    bones = sorted(owner.pose.bones, key=lambda b: b.bone[joint])
    return np.array([np.array(b.matrix) for b in bones])


def check_poses(
    bpy: ModuleType, module: Callable[[str], ModuleType], owner: Any, model: Model, frames: int
) -> None:
    animation, stored = module("animation"), module("stored")
    for clip in model.clips:
        animation.play(owner, owner[stored.CLIPS][str(clip.slot)])
        for frame in np.linspace(0.0, clip.frames, frames):
            got = posed(bpy, owner, stored.JOINT, float(frame))
            want = fk.world_matrices(model.rig, clip.source, frame, clip.joint_tracks)
            assert np.abs(got[:, :3, 3] - want[:, :3, 3]).max() < POSITION, (clip.slot, frame)
            assert np.abs(got[:, :3, :3] - want[:, :3, :3]).max() < ROTATION, (clip.slot, frame)


def check_round_trip(module: Callable[[str], ModuleType], owner: Any) -> Model:
    """Every untouched Action reads back as its clip; an MHFU pack is rewritten byte for byte."""
    animation, stored = module("animation"), module("stored")
    model = stored.model(owner)
    anim = model.anim
    for clip in model.clips:
        action = owner[stored.CLIPS][str(clip.slot)]
        got = animation.to_clip(owner, action, model, clip.slot)
        assert got == clip.source, clip.slot
        if anim is not None:
            anim = motion.put(anim, clip.slot, got, model.skeleton)
    if anim is not None and model.anim is not None:
        assert anim.to_bytes() == model.anim.to_bytes()
    return model


def test_synthetic(
    bpy: ModuleType,
    scene: Any,
    module: Callable[[str], ModuleType],
    synthetic: bytes,
    tmp_path: Path,
) -> None:
    stored = module("stored")
    path = tmp_path / "file_09999.bin"
    path.write_bytes(synthetic)
    owner = load(bpy, path)
    assert owner.type == "ARMATURE" and bytes(owner[stored.PAC]) == synthetic
    assert (owner[stored.GAME], owner[stored.PATH]) == ("mhfu", str(path))
    bones = owner.data.bones
    assert [(b.name, b[stored.JOINT]) for b in bones] == [("j00", 0), ("j01", 1), ("j02", 2)]
    for b, head in zip(bones, [(0, 10, 0), (0, 110, 0), (0, 110, 50)], strict=True):
        assert np.allclose(np.array(b.matrix_local), np.array(_translation(head)), atol=1e-5)
    (mesh,) = owner.children
    assert mesh[stored.GROUP] == 0 and mesh.parent == owner
    assert (len(mesh.data.vertices), len(mesh.data.polygons)) == (3, 1)
    weights = {mesh.vertex_groups[g.group].name: g.weight for g in mesh.data.vertices[1].groups}
    assert weights == pytest.approx({"j01": 0.25, "j02": 0.75}, abs=1e-3)
    tree = mesh.data.materials[0].node_tree
    image = next(n for n in tree.nodes if n.type == "TEX_IMAGE")
    assert image.inputs["Vector"].links[0].from_node.type == "UVMAP"
    clips = owner[stored.CLIPS]
    assert {k: v.name for k, v in clips.items()} == {"1": "clip_01", "2": "clip_02"}
    assert (clips["1"][stored.LOOP], clips["1"][stored.LOOP_START]) == (1, 4.0)
    assert "mhfu_ch_001" in owner.pose.bones["j01"]
    model = Model.from_bytes(synthetic, "file_09999")
    check_poses(bpy, module, owner, model, 41)
    check_round_trip(module, owner)


def test_saved(
    bpy: ModuleType,
    scene: Any,
    module: Callable[[str], ModuleType],
    synthetic: bytes,
    tmp_path: Path,
) -> None:
    """The .blend alone carries the model: its bytes and every Action, none on a fake user."""
    stored = module("stored")
    path = tmp_path / "file_09999.bin"
    path.write_bytes(synthetic)
    load(bpy, path)
    path.unlink()
    blend = tmp_path / "saved.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    owner = next(o for o in bpy.data.objects if stored.PAC in o)
    assert not any(a.use_fake_user for a in bpy.data.actions)
    assert len(owner[stored.CLIPS]) == 2
    check_round_trip(module, owner)


def test_refused(bpy: ModuleType, scene: Any, tmp_path: Path) -> None:
    path = tmp_path / "junk.bin"
    path.write_bytes(b"\0" * 64)
    with pytest.raises(RuntimeError, match="not a model PAC"):
        bpy.ops.import_scene.mhfu_model(filepath=str(path))
    assert not bpy.data.objects


def test_tigrex(
    bpy: ModuleType, scene: Any, module: Callable[[str], ModuleType], mhfu_data: Path
) -> None:
    owner = load(bpy, mhfu_data / "file_06185.bin")
    model = check_round_trip(module, owner)
    assert len(owner.children) == len(model.groups) == 214
    check_poses(bpy, module, owner, model, 4)


def test_brute(
    bpy: ModuleType, scene: Any, module: Callable[[str], ModuleType], mhp3rd_data: Path
) -> None:
    stored = module("stored")
    owner = load(bpy, mhp3rd_data / "file_05248.bin")
    assert (owner[stored.GAME], owner[stored.EM_ID]) == ("mhp3rd", 58)
    assert owner.data.bones[1].name == "j01 COG"
    model = check_round_trip(module, owner)
    check_poses(bpy, module, owner, model, 4)


def _translation(at: tuple[float, float, float]) -> np.ndarray:
    m = np.eye(4)
    m[:3, 3] = at
    return m


def test_legacy_actions(
    bpy: ModuleType,
    scene: Any,
    module: Callable[[str], ModuleType],
    synthetic: bytes,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blender 4.2 and 4.3 have only `Action.fcurves`, which 4.4 and 4.5 still answer."""
    animation = module("animation")
    if "fcurves" not in bpy.types.Action.bl_rna.properties:
        pytest.skip("Blender 5 has no Action.fcurves")
    monkeypatch.setattr(animation, "slotted", lambda action: False)
    path = tmp_path / "file_09999.bin"
    path.write_bytes(synthetic)
    check_round_trip(module, load(bpy, path))
