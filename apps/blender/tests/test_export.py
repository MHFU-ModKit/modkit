# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""File > Export and Push to Game inside Blender: what an edit made in Blender writes."""

from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest
from mhfu_port import fk
from mhfu_port.model import Model
from mhp_formats import Pac, Pmo, Skeleton, Tmh, fu
from mhp_formats.skeleton import FU_MAGIC

STEP = 256.0 / 32768
"""A synthetic position's quantum."""


@pytest.fixture
def source(two_mesh: bytes, tmp_path: Path) -> Path:
    path = tmp_path / "file_09999.bin"
    path.write_bytes(two_mesh)
    return path


def export(bpy: ModuleType, path: Path) -> bytes:
    assert bpy.ops.export_scene.mhfu_pac(filepath=str(path)) == {"FINISHED"}
    return path.read_bytes()


def group(owner: Any, g: int) -> Any:
    return next(o for o in owner.children if o.get("mhfu_group") == g)


def test_untouched(bpy: ModuleType, load: Callable[..., Any], source: Path, tmp_path: Path) -> None:
    load(source)
    assert export(bpy, tmp_path / "out.bin") == source.read_bytes()


def test_moved_vertex_and_object(
    bpy: ModuleType, load: Callable[..., Any], source: Path, tmp_path: Path
) -> None:
    """A vertex moved in Edit Mode and an object moved in Object Mode both ship, in place."""
    owner = load(source)
    before = Model.from_bytes(source.read_bytes(), "before")
    group(owner, 1).data.vertices[0].co.x += 3.0
    group(owner, 2).location = (0.0, 5.0, 0.0)
    out = export(bpy, tmp_path / "out.bin")
    assert len(out) == len(source.read_bytes())
    back = Model.from_bytes(out, "back")
    moved = before.groups[1].positions.copy()
    moved[0, 0] += 3.0
    assert np.abs(back.groups[1].positions - moved).max() <= STEP / 2 + 1e-6
    shifted = before.groups[2].positions + (0.0, 5.0, 0.0)
    assert np.abs(back.groups[2].positions - shifted).max() <= STEP / 2 + 1e-6
    assert np.array_equal(back.groups[0].positions, before.groups[0].positions)


def test_added_geometry(
    bpy: ModuleType, load: Callable[..., Any], source: Path, tmp_path: Path
) -> None:
    import bmesh

    owner = load(source)
    obj = group(owner, 1)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    corner = bm.verts[2], bm.verts[3]
    face = bm.faces.new((*corner, bm.verts.new((20.0, 120.0, 0.0))))
    uv = bm.loops.layers.uv["UV"]
    for loop, vert in zip(face.loops[:2], corner, strict=True):  # no seam: the UVs it has
        loop[uv].uv = next(other for other in vert.link_loops if other is not loop)[uv].uv
    bm.to_mesh(obj.data)
    bm.free()
    obj.vertex_groups["j01"].add([4], 1.0, "REPLACE")
    out = export(bpy, tmp_path / "out.bin")
    back = Model.from_bytes(out, "back")
    assert [g.n_vertices for g in back.groups] == [3, 5, 4]
    assert [g.n_faces for g in back.groups] == [1, 3, 2]
    assert back.groups[1].positions[4] == pytest.approx((20.0, 120.0, 0.0), abs=STEP)
    pmo = Pac.from_bytes(out).entries[1]
    meshes, groups = (int.from_bytes(pmo[at : at + 4], "little") for at in (0x20, 0x24))
    assert groups - meshes == 0x30, "two 0x18 mesh records"
    assert Pmo.from_bytes(pmo).palette(2)[:2] == [1, 2]


def test_seam(bpy: ModuleType, load: Callable[..., Any], source: Path, tmp_path: Path) -> None:
    """A UV seam cut in Blender splits the vertex on it."""
    owner = load(source)
    mesh = group(owner, 2).data
    mesh.uv_layers["UV"].data[mesh.polygons[1].loop_start].uv = (0.25, 0.25)  # vertex 2
    back = Model.from_bytes(export(bpy, tmp_path / "out.bin"), "back").groups[2]
    assert (back.n_vertices, back.n_faces) == (5, 2) and back.uvs is not None
    on = np.flatnonzero(np.abs(back.positions - (10.0, 120.0, 0.0)).max(axis=1) < STEP)
    assert {tuple(back.uvs[v]) for v in on} == {(0.0, 1.0), (0.25, 0.75)}


def test_bone_moved_in_edit_mode(
    bpy: ModuleType, load: Callable[..., Any], source: Path, tmp_path: Path
) -> None:
    owner = load(source)
    bpy.ops.object.mode_set(mode="EDIT")
    bone = owner.data.edit_bones["j02"]
    bone.head.z += 7.0
    bone.tail.z += 7.0
    out = export(bpy, tmp_path / "out.bin")  # from Edit Mode: its edits are read
    bpy.ops.object.mode_set(mode="OBJECT")
    skeleton = Skeleton.from_bytes(Pac.from_bytes(out).entries[0])
    assert skeleton.bones[2].position == pytest.approx((0.0, 0.0, 57.0))
    assert len(out) == len(source.read_bytes())


def test_refused(bpy: ModuleType, load: Callable[..., Any], source: Path, tmp_path: Path) -> None:
    owner = load(source)
    obj = group(owner, 1)
    obj.vertex_groups.remove(obj.vertex_groups["j01"])
    with pytest.raises(RuntimeError, match="vertices have no weight"):
        export(bpy, tmp_path / "out.bin")
    bpy.ops.object.mode_set(mode="EDIT")
    owner.data.edit_bones.new("extra").tail = (0.0, 1.0, 0.0)
    bpy.ops.object.mode_set(mode="OBJECT")
    with pytest.raises(RuntimeError, match="'extra' is new"):
        export(bpy, tmp_path / "out.bin")
    assert not (tmp_path / "out.bin").exists()


def test_push(
    bpy: ModuleType, load: Callable[..., Any], extension: str, source: Path, tmp_path: Path
) -> None:
    load(source)
    root = tmp_path / "memstick"
    (root / "PSP").mkdir(parents=True)
    bpy.context.preferences.addons[extension].preferences.memstick = str(root)
    assert bpy.ops.mhfu.push() == {"FINISHED"}
    folder = root / "PSP/PLUGINS/mhfu_framework/inject"
    assert (folder / "file_09999.bin").read_bytes() == source.read_bytes()
    assert (folder / "file_09999.bin.orig").read_bytes() == source.read_bytes()


def test_donor(bpy: ModuleType, load: Callable[..., Any], donor: bytes, tmp_path: Path) -> None:
    path = tmp_path / "file_09998.bin"
    path.write_bytes(donor)
    load(path)
    entries = Pac.from_bytes(export(bpy, tmp_path / "out.bin")).entries
    assert Skeleton.from_bytes(entries[0]).magic == FU_MAGIC
    assert [len(g.block.vertices) for g in Pmo.from_bytes(entries[1]).groups()] == [4]
    with pytest.raises(RuntimeError, match="port"):
        bpy.ops.mhfu.push()


def test_tigrex(
    bpy: ModuleType,
    load: Callable[..., Any],
    posed: Callable[..., np.ndarray],
    module: Callable[[str], ModuleType],
    extension: str,
    mhfu_data: Path,
    tmp_path: Path,
) -> None:
    """Untouched, the PAC comes back byte for byte; one key edited changes only its slot's part,
    which then plays what Blender poses."""
    animation, stored = module("animation"), module("stored")
    source = (mhfu_data / "file_06185.bin").read_bytes()
    owner = load(mhfu_data / "file_06185.bin")
    assert export(bpy, tmp_path / "same.bin") == source
    slot = min(map(int, owner[stored.CLIPS].keys()))
    action = owner[stored.CLIPS][str(slot)]
    curve = next(
        c
        for c in animation.fcurves(action)
        if c.data_path.endswith(".rotation_euler") and len(c.keyframe_points) > 2
    )
    joint = owner.data.bones[curve.data_path.split('"')[1]][stored.JOINT]
    key = curve.keyframe_points[1]
    turn = 600 * np.pi / 8192
    for point in (key.co, key.handle_left, key.handle_right):
        point.y += turn
    out = export(bpy, tmp_path / "edited.bin")
    before, after = (fu.Anim.from_bytes(Pac.from_bytes(b).entries[3]) for b in (source, out))
    part = 2 * Skeleton.from_bytes(Pac.from_bytes(source).entries[0]).bones[joint].stream
    changed = {
        (s, i)
        for s, (old, new) in enumerate(zip(before.streams, after.streams, strict=True))
        for i, (a, b) in enumerate(zip(old, new, strict=True))
        if a != b
    }
    assert changed == {(part, slot)}
    model = Model.from_bytes(out, "edited")
    clip = model.clip(slot)
    animation.play(owner, action)
    for frame in np.linspace(0.0, clip.frames, 9):
        got = posed(owner, float(frame))
        want = fk.world_matrices(model.rig, clip.source, frame)
        assert np.abs(got[:, :3, 3] - want[:, :3, 3]).max() < 1e-2, frame
        assert np.abs(got[:, :3, :3] - want[:, :3, :3]).max() < 1e-4, frame
    root = tmp_path / "memstick"
    (root / "PSP").mkdir(parents=True)
    bpy.context.preferences.addons[extension].preferences.memstick = str(root)
    assert bpy.ops.mhfu.push() == {"FINISHED"}
    inject = root / "PSP/PLUGINS/mhfu_framework/inject"
    assert sorted(p.name for p in inject.iterdir()) == ["file_06185.bin", "file_06185.bin.orig"]


def test_brute(
    bpy: ModuleType,
    load: Callable[..., Any],
    module: Callable[[str], ModuleType],
    mhp3rd_data: Path,
    tmp_path: Path,
) -> None:
    stored = module("stored")
    owner = load(mhp3rd_data / "file_05248.bin")
    entries = Pac.from_bytes(export(bpy, tmp_path / "brute.bin")).entries
    skeleton = Skeleton.from_bytes(entries[0])
    assert skeleton.magic == FU_MAGIC and len(skeleton.bones) == len(owner.data.bones) == 46
    meshes = {o[stored.GROUP]: len(o.data.vertices) for o in owner.children}
    pmo = Pmo.from_bytes(entries[1])
    assert [len(g.block.vertices) for g in pmo.groups()] == [meshes[g] for g in sorted(meshes)]
    assert len(Tmh.from_bytes(entries[2]).images) == 5
    assert fu.Anim.from_bytes(entries[3]).streams == [[]]
