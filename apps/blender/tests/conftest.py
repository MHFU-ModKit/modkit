# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Tests taking `bpy` or `extension` skip outside Blender; `build.py test` runs them inside it.

fake-bpy-module installs only `bpy-stubs`, so `import bpy` fails anywhere but in Blender.
"""

import importlib
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest
from mhfu_port import mesh
from mhfu_port.mesh import Part, Skinned
from mhp_formats import Channel, Clip, Keyframe, Mesh, Pac, Pmo, Tmh, TmhImage, Track, fu, p3rd
from mhp_formats.skeleton import P3RD_MAGIC, Bone, Skeleton


@pytest.fixture(scope="session")
def bpy() -> ModuleType:
    return pytest.importorskip("bpy", reason="needs Blender: uv run apps/blender/build.py test")


@pytest.fixture(scope="session")
def extension(bpy: ModuleType) -> str:
    """The installed extension's module name, enabled."""
    import addon_utils

    name = os.environ.get("MHFU_BLENDER_EXTENSION")
    if not name:
        pytest.skip("the extension is installed by: uv run apps/blender/build.py test")
    assert addon_utils.enable(name, default_set=True), "enable failed: traceback in stderr"
    return name


@pytest.fixture
def scene(bpy: ModuleType, extension: str) -> Iterator[Any]:
    """An empty scene, the extension enabled."""
    bpy.ops.wm.read_homefile(use_empty=True)
    yield bpy.context.scene


@pytest.fixture
def load(bpy: ModuleType, scene: Any) -> Callable[..., Any]:
    """File > Import of a path into the empty scene; the armature it makes."""

    def run(path: Path, **options: Any) -> Any:
        assert bpy.ops.import_scene.mhfu_model(filepath=str(path), **options) == {"FINISHED"}
        return bpy.context.view_layer.objects.active

    return run


@pytest.fixture
def posed(
    bpy: ModuleType, module: Callable[[str], ModuleType]
) -> Callable[[Any, float], np.ndarray]:
    """Each joint's pose matrix at a frame; `pose.bones` runs in tree order, not joint order."""
    joint = module("stored").JOINT

    def run(owner: Any, frame: float) -> np.ndarray:
        bpy.context.scene.frame_set(int(frame), subframe=frame - int(frame))
        bones = sorted(owner.pose.bones, key=lambda b: b.bone[joint])
        return np.array([np.array(b.matrix) for b in bones])

    return run


@pytest.fixture(scope="session")
def module(request: pytest.FixtureRequest) -> Callable[[str], ModuleType]:
    """A module of the extension: the installed one inside Blender, the source tree elsewhere."""
    package = "mhfu_blender"
    if os.environ.get("MHFU_BLENDER_EXTENSION"):
        package = request.getfixturevalue("extension")
    return lambda name: importlib.import_module(f"{package}.{name}")


def keys(*rows: tuple[int, ...]) -> list[Keyframe]:
    """`(frame, value[, ease_in, ease_out])` rows."""
    return [Keyframe(v, f, *eases) for f, v, *eases in rows]


def synthetic_pac() -> bytes:
    """3 joints in two parts (0, 0, 1), one textured group of 3 vertices, a 2x2 texture. Slot 1
    plays on both parts with eased keys, a location, a doubled key and an unnamed channel; slot 2
    only on part 1."""
    bones = [
        Bone(parent=-1, position=(0.0, 10.0, 0.0), stream=0),
        Bone(parent=0, position=(0.0, 100.0, 0.0), stream=0),
        Bone(parent=1, position=(0.0, 0.0, 50.0), stream=1),
    ]
    verts = [(0.0, 110.0, 0.0), (0.0, 110.0, 50.0), (10.0, 110.0, 0.0)]
    uvs = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
    influences = [[(1, 1.0)], [(1, 0.25), (2, 0.75)], [(1, 1.0)]]
    part = Part(verts, [], uvs, [], [(0, 1, 2)], influences, 0)
    model = mesh.build([Skinned(part, part.influences)], (256.0, 256.0, 256.0), tip=None)
    root = Track(
        [
            Channel(0x008, keys((0, 0, 0, 300), (10, 2048, -100, 50), (20, 0))),
            Channel(0x080, keys((0, 160), (20, 480, 16, 0))),
        ]
    )
    neck = Track(
        [
            Channel(0x001, keys((0, 16), (10, 26))),
            Channel(0x010, keys((0, 0, 0, 0), (8, 4096, 512, 512), (8, 4096), (20, -1024, 7, 0))),
        ]
    )
    body = Clip([root, neck], 1, 4.0)
    head = Clip([Track([Channel(0x020, keys((0, 0, 0, 900), (12, 3000, -40, 0)))])])
    still = Clip([Track([Channel(0x008, keys((0, 0), (6, 0)))])])
    anim = fu.Anim([[None, body, None], [None, None, None], [None, head, still]])
    entries = [
        Skeleton(bones, [0, 3]).to_bytes(),
        model.to_bytes(),
        Tmh([TmhImage(3, 2, 2, bytes(range(16)))]).to_bytes(),
        anim.to_bytes(),
    ]
    return Pac(entries).to_bytes()


@pytest.fixture(scope="session")
def synthetic() -> bytes:
    return synthetic_pac()


def two_meshes(pac: bytes) -> bytes:
    """`pac` (`synthetic_pac`) with a second mesh of two groups that carry normals: the first
    patches the palette to joints 1 and 2, the second inherits that patch."""
    entries = Pac.from_bytes(pac).entries
    model = Pmo.from_bytes(entries[1])
    verts = [(0.0, 120.0, 0.0), (0.0, 120.0, 40.0), (10.0, 120.0, 0.0), (10.0, 120.0, 40.0)]
    normals = [(0.0, 1.0, 0.0), (0.0, 0.6, 0.8), (0.8, 0.6, 0.0), (0.0, 0.0, 1.0)]
    uvs = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)]
    weights = [[(1, 1.0)], [(1, 0.5), (2, 0.5)], [(1, 0.75), (2, 0.25)], [(2, 1.0)]]
    part = Part(verts, normals, uvs, [], [(0, 1, 2), (2, 1, 3)], weights, 0)
    patching = mesh.group(Skinned(part, part.influences), model.scale)
    inheriting = mesh.group(Skinned(part, part.influences), model.scale)
    inheriting.bones = []
    model.meshes.append(Mesh([patching, inheriting], [0]))
    entries[1] = model.to_bytes()
    return Pac(entries).to_bytes()


def donor_pac() -> bytes:
    """An MHP3rd model PAC: 3 named joints, one group of 4 vertices inline, a 2x2 texture."""
    bones = [
        Bone(parent=-1, position=(0.0, 10.0, 0.0), name=b"ROOT\0\0\0\0"),
        Bone(parent=0, position=(0.0, 100.0, 0.0), name=b"COG\0\0\0\0\0"),
        Bone(parent=1, position=(0.0, 0.0, 50.0), name=b"HEAD\0\0\0\0"),
    ]
    verts = [(0.0, 110.0, 0.0), (0.0, 110.0, 50.0), (10.0, 110.0, 0.0), (10.0, 110.0, 50.0)]
    normals = [(0.0, 1.0, 0.0)] * 4
    uvs = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)]
    weights = [[(1, 1.0)], [(1, 0.25), (2, 0.75)], [(1, 1.0)], [(2, 1.0)]]
    part = Part(verts, normals, uvs, [], [(0, 1, 2), (2, 1, 3)], weights, 0)
    scale = (256.0, 256.0, 256.0)
    built = mesh.build([Skinned(part, part.influences)], scale, tip=None)
    model = p3rd.Pmo(
        [p3rd.Mesh(built.groups(), [0], scale=scale)], built.materials, scale, remap_table=False
    )
    entries = [
        Skeleton(bones, [0, 3], magic=P3RD_MAGIC).to_bytes(),
        model.to_bytes(),
        Tmh([TmhImage(3, 2, 2, bytes(range(16)))]).to_bytes(),
    ]
    return Pac(entries).to_bytes()


@pytest.fixture(scope="session")
def two_mesh(synthetic: bytes) -> bytes:
    return two_meshes(synthetic)


@pytest.fixture(scope="session")
def donor() -> bytes:
    return donor_pac()
