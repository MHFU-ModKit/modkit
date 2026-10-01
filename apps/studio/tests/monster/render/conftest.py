# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A synthetic skinned monster, the data-free subject of the render tests and their golden.

Seven joints: root 0, hip 1 (it carries the travel), spine 2 (the fork), neck 3 and head 4 in
skeleton part 0, tail 5 and 6 in part 1; joint 7 is a second root no clip drives. A textured
tube along +Z blends between adjacent joints (two influences per vertex, two textures), and a
box hangs on joint 7. Slot 1 walks (looping, whole rig), slot 2 strikes (one-shot, part 0
only).
"""

import math
from collections.abc import Callable, Iterator
from typing import Any

import numpy as np
import pytest
from mhfu_port import mesh
from mhfu_port.mesh import Part, Skinned
from mhfu_studio.monster.core.scene import Scene
from mhp_formats import Channel, Clip, Keyframe, Pac, Tmh, TmhImage, Track, fu
from mhp_formats.anim import quantize
from mhp_formats.skeleton import Bone, Skeleton

ROT = {"x": 0x008, "y": 0x010, "z": 0x020}
LOC = {"x": 0x040, "y": 0x080, "z": 0x100}
#: (parent, local offset, skeleton part)
JOINTS = [
    (-1, (0.0, 0.0, 0.0), 0),
    (0, (0.0, 300.0, 0.0), 0),
    (1, (0.0, 0.0, 100.0), 0),
    (2, (0.0, 0.0, 200.0), 0),
    (3, (0.0, 0.0, 200.0), 0),
    (2, (0.0, 0.0, -200.0), 1),
    (5, (0.0, 0.0, -200.0), 1),
    (-1, (300.0, 0.0, 0.0), 0),
]
#: chain joints and their world z along the tube's axis (y = 300)
FRONT = [(2, 100.0), (3, 300.0), (4, 500.0)]
REAR = [(2, 100.0), (5, -100.0), (6, -300.0)]


def channel(bit: int, kind: str, keys: list[tuple[int, float]]) -> Channel:
    return Channel(bit, [Keyframe(quantize(kind, v), f) for f, v in keys])  # type: ignore[arg-type]


def tube(chain: list[tuple[int, float]], z0: float, z1: float, texture: int) -> Skinned:
    """Rings from z0 to z1 around y = 300, each vertex blended between its two joints."""
    segs, radius = 12, 60.0
    zs = np.linspace(z0, z1, 9)
    pos, nrm, uvs, infl = [], [], [], []
    order = sorted(chain, key=lambda jz: jz[1])
    for r, z in enumerate(zs):
        lo = next(i for i in range(len(order) - 1) if z <= order[i + 1][1] or i == len(order) - 2)
        (ja, za), (jb, zb) = order[lo], order[lo + 1]
        t = min(max((z - za) / (zb - za), 0.0), 1.0)
        for s in range(segs):
            a = 2 * math.pi * s / segs
            c, sn = math.cos(a), math.sin(a)
            pos.append((radius * c, 300.0 + radius * sn, float(z)))
            nrm.append((c, sn, 0.0))
            uvs.append((s / segs, r / (len(zs) - 1)))
            infl.append([(ja, 1.0 - t), (jb, t)] if 0 < t < 1 else [(ja if t == 0 else jb, 1.0)])
    tris = []
    for r in range(len(zs) - 1):
        for s in range(segs):
            a, b = r * segs + s, r * segs + (s + 1) % segs
            tris += [(a, b, a + segs), (b, b + segs, a + segs)]
    part = Part(pos, nrm, uvs, [], tris, infl, texture)
    return Skinned(part, infl)


def box(centre: tuple[float, float, float], half: float, joint: int, texture: int) -> Skinned:
    cx, cy, cz = centre
    signs = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    corners = [(cx + x * half, cy + y * half, cz + z * half) for x, y, z in signs]
    faces = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1)]
    faces += [(2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]
    nrm = [(x * 0.577, y * 0.577, z * 0.577) for x, y, z in signs]
    infl = [[(joint, 1.0)] for _ in corners]
    part = Part(corners, nrm, [(0.0, 0.0)] * 8, [], faces, infl, texture)
    return Skinned(part, infl)


def texture(kind: int) -> TmhImage:
    """16x16 RGBA8888: a checker (0) or stripes (1)."""
    px = bytearray()
    for y in range(16):
        for x in range(16):
            on = (x // 4 + y // 4) % 2 if kind == 0 else (y // 2) % 2
            px += bytes((220, 160, 60, 255) if on else (60, 90, 170, 255))
    return TmhImage(3, 16, 16, bytes(16 * 16 * 4)).encode(bytes(px))


def walk() -> tuple[Clip, Clip]:
    """Slot 1, 40 frames, looping: the hip travels 240 along +Z, the neck dips, the head
    and tail sway."""
    part0 = [
        Track(),
        Track([channel(LOC["z"], "loc", [(0, 0.0), (40, 240.0)])]),
        Track([channel(ROT["x"], "rot", [(0, 0.0), (20, -0.15), (40, 0.0)])]),
        Track([channel(ROT["x"], "rot", [(0, 0.0), (20, 0.6), (40, 0.0)])]),
        Track([channel(ROT["y"], "rot", [(0, 0.0), (20, 0.7), (40, 0.0)])]),
    ]
    part1 = [
        Track([channel(ROT["y"], "rot", [(0, 0.0), (20, -0.5), (40, 0.0)])]),
        Track([channel(ROT["y"], "rot", [(0, 0.0), (20, 0.9), (40, 0.0)])]),
    ]
    return Clip(part0, 1), Clip(part1, 1)


def strike() -> Clip:
    """Slot 2, 30 frames, one-shot, part 0 only: the spine rears, the neck lunges."""
    return Clip(
        [
            Track(),
            Track(),
            Track([channel(ROT["x"], "rot", [(0, 0.0), (15, -0.4), (30, 0.0)])]),
            Track([channel(ROT["x"], "rot", [(0, 0.0), (15, 0.9), (30, 0.2)])]),
        ]
    )


def rig_pac(**override: Any) -> bytes:
    """The PAC; `override` replaces `joints` (a JOINTS-like list) or `textures`."""
    joints = override.get("joints", JOINTS)
    bones = [Bone(parent=p, position=o, stream=s) for p, o, s in joints]
    skel = Skeleton(bones, [0, 7])
    parts = [
        tube(FRONT, 100.0, 550.0, 0),
        tube(REAR, -350.0, 100.0, 1),
        box((300.0, 40.0, 0.0), 40.0, 7, 0),
    ]
    model = mesh.build(parts, (1024.0, 1024.0, 1024.0))
    body, tail = walk()
    anim = fu.Anim([[None, body, strike()], [None, None, None], [None, tail, None]])
    images = override.get("textures", [texture(0), texture(1)])
    entries = [skel.to_bytes(), model.to_bytes(), Tmh(images).to_bytes(), anim.to_bytes()]
    return Pac(entries).to_bytes()


@pytest.fixture(scope="session")
def rig_bytes() -> bytes:
    return rig_pac()


@pytest.fixture
def rig(rig_bytes: bytes) -> Scene:
    return Scene.from_bytes(rig_bytes, "rig")


@pytest.fixture
def make_rig() -> Callable[..., Scene]:
    return lambda **kw: Scene.from_bytes(rig_pac(**kw), "rig")


@pytest.fixture
def lit() -> Callable[[Any, Any], int]:
    """Pixels further than 12 levels from the clear colour."""

    def count(img: Any, clear: Any) -> int:
        bg = np.round(np.asarray(clear[:3]) * 255)
        return int((np.abs(img[..., :3].astype(int) - bg).max(axis=2) > 12).sum())

    return count


@pytest.fixture
def viewport(gl: Any) -> Iterator[Any]:
    from mhfu_studio.monster.render.viewport import MonsterViewport

    with MonsterViewport(gl, (320, 240), 0) as vp:
        yield vp
