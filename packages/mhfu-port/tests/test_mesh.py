# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import hashlib
from dataclasses import replace

import pytest
from mhfu_port import mesh, skin
from mhfu_port.data import Data
from mhfu_port.mesh import Part, Skinned
from mhp_formats import p3rd
from mhp_formats import pmo as fu
from mhp_formats.pac import Pac
from mhp_formats.pmo import Block, BoneSlot, Group, Material
from mhp_formats.psp.ge import Command, Op, Prim
from mhp_formats.psp.vtype import (
    BITS8,
    BITS16,
    COLOR_4444,
    VertexType,
    Vertices,
    quantize_vertices,
)

SCALE = (64.0, 64.0, 64.0)
VT = VertexType(texture=BITS16, normal=BITS8, position=BITS16, weight=BITS8, weight_count=2)


def _vertices(vt: VertexType, n: int, **extra: object) -> Vertices:
    return quantize_vertices(
        vt,
        [(float(i), 2.0 * i, -float(i)) for i in range(n)],
        SCALE,
        uvs=[(i / 8, 0.5) for i in range(n)],
        weights=[(0.75, 0.25)] * n if vt.weight else None,
        **extra,  # type: ignore[arg-type]
    )


def _strips() -> Block:
    """Two unindexed strips after one VADDR: the second starts where the first ends."""
    commands = [Command(Op.ORIGIN), Command(Op.BASE), Command(Op.VADDR)]
    commands += [Command(Op.VTYPE, VT.to_word()), Command(Op.FFACE)]
    commands += [Command.prim(Prim.TRIANGLE_STRIP, 4), Command.prim(Prim.TRIANGLE_STRIP, 3)]
    commands += [Command(Op.OFFSETADDR), Command(Op.RET)]
    return Block(commands, _vertices(VT, 7, normals=[(0.0, 0.5, 0.0)] * 7))


def _list() -> Block:
    """An indexed triangle list of two triangles, which does not alternate winding."""
    vt = VertexType(texture=BITS16, color=COLOR_4444, position=BITS16, weight=BITS8, index=1)
    commands = [Command(Op.ORIGIN), Command(Op.BASE), Command(Op.IADDR), Command(Op.VADDR)]
    commands += [Command(Op.VTYPE, vt.to_word()), Command(Op.FFACE)]
    commands += [Command.prim(Prim.TRIANGLES, 6), Command(Op.OFFSETADDR), Command(Op.RET)]
    colors = [(255, 0, 0, 255)] * 4
    vertices = quantize_vertices(
        vt,
        [(0.0, 0.0, 0.0), (8.0, 0.0, 0.0), (0.0, 0.0, 8.0), (8.0, 0.0, 8.0)],
        (16.0, 16.0, 16.0),
        uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)],
        weights=[(1.0,)] * 4,
        colors=colors,
    )
    return Block(commands, vertices, [0, 1, 2, 2, 1, 3])


def _donor() -> p3rd.Pmo:
    first = p3rd.Mesh([Group(_strips(), 0, [BoneSlot(0, 4), BoneSlot(1, 7)])], [0])
    first.scale = SCALE
    second = p3rd.Mesh([Group(_list(), 0, [BoneSlot(0, 9)])], [1], uv_scale=(0.5, 0.25))
    second.scale, second.uv_offset = (16.0, 16.0, 16.0), (0.25, 0.5)
    materials = [Material(texture=6), Material(texture=2)]
    return p3rd.Pmo([first, second], materials, SCALE, 60.0, remap_table=False)


def test_parts():
    strips, quad = mesh.parts(_donor())
    assert strips.triangles == [(0, 1, 2), (2, 1, 3), (4, 5, 6)]
    assert strips.positions[6] == (6.0, 12.0, -6.0) and strips.normals[0] == (0.0, 0.5, 0.0)
    assert strips.influences[0] == [(4, 0.75), (7, 0.25)] and strips.texture == 6
    assert strips.colors == [] and strips.uvs[4] == (0.5, 0.5)
    assert quad.triangles == [(0, 1, 2), (2, 1, 3)]
    assert quad.uvs == [(0.25, 0.5), (0.75, 0.5), (0.25, 0.75), (0.75, 0.75)]
    assert quad.normals == [] and quad.colors[0] == (255, 0, 0, 255)
    assert quad.positions[3] == (8.0, 0.0, 8.0) and quad.texture == 2


def test_donor():
    main = _donor()
    low = p3rd.Pmo([p3rd.Mesh([Group(_list())], [0])], [Material()], remap_table=False)
    pac = Pac([b"\0" * 16, low.to_bytes(), main.to_bytes()]).to_bytes()
    assert len(mesh.donor(pac).meshes) == 2
    assert len(mesh.donor(main.to_bytes()).meshes) == 2
    with pytest.raises(ValueError):
        mesh.donor(Pac([b"\0" * 16]).to_bytes())


def _part(influences: list[list[tuple[int, float]]], texture: int = 0) -> Part:
    n = len(influences)
    positions = [(float(i), 0.0, float(i % 2)) for i in range(n)]
    triangles = [(i, i + 1, i + 2) for i in range(n - 2)]
    return Part(positions, [], [(0.0, 0.0)] * n, [], triangles, influences, texture)


def test_dominant():
    assert _part([[(3, 0.4), (5, 0.6)], [(3, 0.4), (5, 0.1)], [(8, 1e-5)]]).dominant() == 3
    assert _part([[(2, 0.5), (1, 0.5)]] * 3).dominant() == 2
    assert _part([[(-1, 1.0)]] * 3).dominant() == -1


def test_drop():
    parts = [_part([[(b, 1.0)]] * 3) for b in (1, 2, 3)]
    assert mesh.drop(parts, {2, 9}) == [parts[0], parts[2]]


def test_palette():
    part = _part([[(5, 0.5), (2, 0.5)], [(2, 1.0)], [(7, 0.0), (-1, 1.0), (3, 1.0)]])
    assert mesh.palette(Skinned(part, part.influences)) == [5, 2, 3]
    assert mesh.palette(Skinned(part, [[], [], []])) == [0]


def test_overflow():
    part = _part([[(0, 1.0)]] * 3)
    part.positions = [(63.99, 0.0, -64.0), (64.0, -64.5, 0.0), (0.0, 0.0, 0.0)]
    over = mesh.overflow([part], SCALE)
    assert [(o.group, o.vertex, o.axis) for o in over] == [(0, 1, 0), (0, 1, 1)]
    assert over[0].lost == pytest.approx(64.0 / 32768) and over[1].lost == pytest.approx(-0.5)


def test_build():
    parts = mesh.parts(_donor())
    joint_of = {4: 1, 7: 2, 9: 3}
    built = mesh.build(skin.source(parts, joint_of), SCALE)
    back = fu.Pmo.from_bytes(built.to_bytes())
    assert back == built and back.clip == mesh.CLIP and back.scale == SCALE
    assert [m.texture for m in back.materials] == [2, 6]
    assert {(m.color, m.shadow) for m in back.materials} == {(mesh.COLOR, mesh.SHADOW)}
    assert back.meshes[0].materials == [0, 1] and back.meshes[0].lighting == mesh.LIGHTING
    assert [g.material for g in back.groups()] == [1, 0]
    assert back.groups()[0].bones == [BoneSlot(0, 1), BoneSlot(1, 2)]
    assert back.influences(0)[0] == [(1, 0.75), (2, 0.25)]
    assert back.positions(0) == parts[0].positions
    assert back.triangles(0) == [(0, 1, 2), (2, 1, 3), (4, 5, 6)]
    assert back.positions(1) == parts[1].positions and back.influences(1)[0] == [(3, 1.0)]
    assert back.groups()[1].block.vertices.normals()[0] == mesh.NO_NORMAL
    assert back.groups()[1].block.vertices.uvs() == parts[1].uvs
    tris = back.triangles(1)
    assert sorted(t for t in tris if len(set(t)) == 3) == [(0, 1, 2), (2, 1, 3)]


def test_group():
    part = _part([[(5, 0.5), (2, 0.5)]] * 4, texture=3)
    skinned = Skinned(part, part.influences)
    group = mesh.group(skinned, SCALE, 7)
    assert group == replace(mesh.build([skinned], SCALE).groups()[0], material=7)
    assert group.bones == [BoneSlot(0, 5), BoneSlot(1, 2)]


def test_palette_limit():
    part = _part([[(j, 1.0)] for j in range(9)] + [[(0, 1.0)]])
    with pytest.raises(ValueError, match="more than"):
        mesh.build([Skinned(part, part.influences)], SCALE)
    assert len(mesh.build(skin.source([part], {j: j for j in range(9)}), SCALE).groups()) == 1


DONORS = {  # model, geometry: groups, vertices, triangles, sha256 of our PMO on an identity map
    "brute": (5248, 5249, 88, 2862, 2973, "cb0d830eb6c61a09"),
    "zinogre": (5339, 5340, 181, 4336, 4426, "98b3869f594ce302"),
}


@pytest.mark.parametrize("name", DONORS)
def test_game(data: Data, name: str):
    model, geometry, groups, vertices, triangles, digest = DONORS[name]
    donor = mesh.donor(data.p3rd.read(model), data.p3rd.read(geometry))
    parts = mesh.parts(donor)
    assert len(parts) == groups and sum(len(p.positions) for p in parts) == vertices
    assert sum(len(p.triangles) for p in parts) == triangles
    assert not mesh.overflow(parts, donor.scale)
    bones = {b for p in parts for row in p.influences for b, _ in row}
    built = mesh.build(skin.source(parts, {b: b for b in bones}), donor.scale).to_bytes()
    back = fu.Pmo.from_bytes(built)
    assert [back.positions(g) for g in range(groups)] == [p.positions for p in parts]
    assert hashlib.sha256(built).hexdigest()[:16] == digest
