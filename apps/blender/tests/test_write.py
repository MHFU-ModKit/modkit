# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Writing an edited model back as a PAC, without Blender; these run in normal CI."""

from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest
from mhfu_port import motion
from mhfu_port.model import Model
from mhp_formats import Pac, Pmo, Tmh, fu
from mhp_formats.anim import Channel, Clip, Keyframe, Track
from mhp_formats.skeleton import FU_MAGIC, Skeleton


@pytest.fixture(scope="module")
def write(module: Callable[[str], ModuleType]) -> ModuleType:
    return module("write")


@pytest.fixture
def model(two_mesh: bytes) -> Model:
    return Model.from_bytes(two_mesh, "file_09999")


def held(write: ModuleType, model: Model) -> dict[int, Any]:
    """Each group as an untouched import holds it."""
    out = {}
    for g in model.groups:
        skin = g.skin
        influences = [
            [(int(j), float(w)) for j, w in zip(js, ws, strict=True) if w > 0]
            for js, ws in zip(skin.joints, skin.weights, strict=True)
        ]
        normals = np.zeros((g.n_vertices, 3)) if g.normals is None else g.normals
        length = np.linalg.norm(normals, axis=1, keepdims=True)
        normals = normals / np.where(length > 0, length, 1.0)
        uvs = None if g.uvs is None else g.uvs.copy()
        out[g.index] = write.Geometry(
            g.positions.copy(), g.triangles.copy(), normals, uvs, influences
        )
    return out


def export(write: ModuleType, model: Model, groups: dict[int, Any] | None = None, **kw: Any) -> Any:
    clips = kw.pop("clips", {c.slot: c.source for c in model.clips})
    skeleton = kw.pop("skeleton", model.skeleton)
    return write.write(model, skeleton, held(write, model) if groups is None else groups, clips)


def test_untouched(write: ModuleType, model: Model) -> None:
    out = export(write, model)
    assert out.pac == model.pac and out.rebuilt == []


def test_move_vertex(write: ModuleType, model: Model) -> None:
    groups = held(write, model)
    groups[1].positions[0] += (3.0, -2.0, 1.5)
    out = export(write, model, groups)
    assert len(out.pac) == len(model.pac or b"") and out.rebuilt == []
    back = Model.from_bytes(out.pac, "back")
    step = 256.0 / 32768
    assert np.abs(back.groups[1].positions - groups[1].positions).max() <= step / 2 + 1e-9
    for g in (0, 2):
        assert np.array_equal(back.groups[g].positions, model.groups[g].positions)


def test_turn_normal_and_uv(write: ModuleType, model: Model) -> None:
    groups = held(write, model)
    groups[2].normals[3] = (1.0, 0.0, 0.0)
    groups[2].uvs[1] = (0.5, 0.25)
    groups[2].normals[0] = (0.0, 0.999999, 0.001)  # within quantisation: keeps its bytes
    out = export(write, model, groups)
    back = Model.from_bytes(out.pac, "back").groups[2]
    assert len(out.pac) == len(model.pac or b"")
    assert back.normals is not None and back.uvs is not None
    assert back.normals[3] == pytest.approx((127 / 128, 0.0, 0.0))
    assert back.uvs[1] == pytest.approx((0.5, 0.25))
    assert np.array_equal(back.normals[0], model.groups[2].normals[0])


def test_weights_in_place(write: ModuleType, model: Model) -> None:
    groups = held(write, model)
    groups[2].influences[0] = [(2, 1.0)]
    out = export(write, model, groups)
    assert out.rebuilt == [] and len(out.pac) == len(model.pac or b"")
    assert Model.from_bytes(out.pac, "back").groups[2].skin.joints[0, 0] == 2


def test_new_joint_rebuilds_and_repatches(write: ModuleType, model: Model) -> None:
    """Group 1 takes joint 0 in, which rebuilds it with a new palette patch; group 2, which
    inherited group 1's, keeps drawing on joints 1 and 2."""
    groups = held(write, model)
    groups[1].influences[0] = [(0, 1.0)]
    out = export(write, model, groups)
    assert out.rebuilt == [1]
    back = Model.from_bytes(out.pac, "back")
    pmo = Pmo.from_bytes(Pac.from_bytes(out.pac).entries[1])
    assert pmo.palette(2)[:2] == [1, 2] and pmo.groups()[2].bones
    for g in (1, 2):
        want, got = model.groups[g].skin, back.groups[g].skin
        want_joints = want.joints.copy()
        if g == 1:
            want_joints[0, 0] = 0
        assert np.array_equal(got.joints[got.weights > 0], want_joints[want.weights > 0])


def test_added_geometry(write: ModuleType, model: Model) -> None:
    groups = held(write, model)
    geo = groups[1]
    geo.positions = np.vstack([geo.positions, (20.0, 120.0, 0.0)])
    geo.normals = np.vstack([geo.normals, (0.0, 1.0, 0.0)])
    geo.uvs = np.vstack([geo.uvs, (0.5, 0.5)])
    geo.triangles = np.vstack([geo.triangles, (2, 3, 4)]).astype(np.int32)
    geo.influences = [*geo.influences, [(1, 1.0)]]
    out = export(write, model, groups)
    assert out.rebuilt == [1] and len(out.pac) > len(model.pac or b"")
    assert len(out.pac) % write.PAD == 0
    back = Model.from_bytes(out.pac, "back")
    assert [g.n_vertices for g in back.groups] == [3, 5, 4]
    assert [g.n_faces for g in back.groups] == [1, 3, 2]
    assert np.allclose(back.groups[1].positions, geo.positions, atol=256 / 32768)
    raw = Pac.from_bytes(out.pac).entries[1]
    meshes, groups_at = (int.from_bytes(raw[at : at + 4], "little") for at in (0x20, 0x24))
    assert groups_at - meshes == 0x30, "two 0x18 mesh records"
    assert back.clips[0].source == model.clips[0].source


def test_missing_group_draws_nothing(write: ModuleType, model: Model) -> None:
    groups = held(write, model)
    del groups[0]
    out = export(write, model, groups)
    assert len(out.pac) == len(model.pac or b"") and "draw nothing" in out.notes[0]
    assert Model.from_bytes(out.pac, "back").groups[0].n_faces == 0


def test_refused(write: ModuleType, model: Model) -> None:
    groups = held(write, model)
    groups[1].influences[2] = []
    with pytest.raises(write.ExportError, match="group 1: 1 vertices have no weight"):
        export(write, model, groups)
    groups = held(write, model)
    groups[0].positions[0] = (300.0, 0.0, 0.0)
    with pytest.raises(write.ExportError, match="outside the model's 512x512x512 box"):
        export(write, model, groups)
    with pytest.raises(write.ExportError, match="no group 7"):
        export(write, model, {**held(write, model), 7: groups[0]})
    scaled = Clip([Track([Channel(0x200, [Keyframe(256, 0), Keyframe(256, 4)])])] + [Track()] * 2)
    with pytest.raises(write.ExportError, match="SCALE_CHANNEL"):
        export(write, model, clips={1: scaled})


def test_emptied_track_rests(write: ModuleType, model: Model) -> None:
    clip = model.clips[0].source
    edited = Clip([Track(), *clip.tracks[1:]], clip.loop, clip.loop_start)
    out = export(write, model, clips={model.clips[0].slot: edited})
    anim = fu.Anim.from_bytes(Pac.from_bytes(out.pac).entries[3])
    body = anim.streams[0][model.clips[0].slot]
    assert body is not None and body.tracks[0] == motion.rest(20)


def test_bind(write: ModuleType, model: Model) -> None:
    joints = model.rig.bind_joints.copy()
    assert write.skeleton(model, joints + 1e-5) == model.skeleton
    joints[2] += (0.0, 0.0, 7.0)
    moved = write.skeleton(model, joints)
    assert (
        moved.bones[2].position == (0.0, 0.0, 57.0) and moved.bones[:2] == model.skeleton.bones[:2]
    )
    out = export(write, model, skeleton=moved)
    assert Skeleton.from_bytes(Pac.from_bytes(out.pac).entries[0]) == moved
    assert len(out.pac) == len(model.pac or b"")


def test_donor(write: ModuleType, donor: bytes) -> None:
    source = donor
    model = Model.from_bytes(source, "file_09998")
    out = export(write, model)
    entries = Pac.from_bytes(out.pac).entries
    skeleton = Skeleton.from_bytes(entries[0])
    assert skeleton.magic == FU_MAGIC and {b.name for b in skeleton.bones} == {None}
    assert [b.position for b in skeleton.bones] == [b.position for b in model.skeleton.bones]
    assert entries[2] == next(e for e in Pac.from_bytes(source).entries if Tmh.sniff(e))
    assert len(out.pac) % write.PAD == 0 and "manifest" in out.notes[0]
    back = Model.from_bytes(out.pac, "back")
    assert back.game == "mhfu" and back.clips == []
    assert [g.n_vertices for g in back.groups] == [4] and back.groups[0].n_faces == 2
    assert np.allclose(back.groups[0].positions, model.groups[0].positions)
    with pytest.raises(write.ExportError, match="port"):
        write.place(out.pac, model)


def test_place(write: ModuleType, model: Model, tmp_path: Path) -> None:
    root = tmp_path / "memstick"
    (root / "PSP").mkdir(parents=True)
    source = model.pac or b""
    placed = write.place(source, model, str(root))
    folder = root / "PSP/PLUGINS/mhfu_framework/inject"
    assert placed.path == str(folder / "file_09999.bin") and not placed.grown
    assert (folder / "file_09999.bin.orig").read_bytes() == source
    assert placed.lua.startswith("mhfu.inject_register(10000, ")
    grown = write.place(source + bytes(write.PAD), model, str(root), file_id=6185)
    assert grown.path == str(folder / "file_06185_grown.bin") and grown.grown
    assert "mhfu.inject_relocate(6186, " in grown.lua and "file_06185.bin.orig" in grown.lua
    model.name = "tigrex"
    with pytest.raises(write.ExportError, match="no file id"):
        write.place(source, model, str(root))
