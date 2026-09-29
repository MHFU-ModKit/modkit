# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The port's geometry: the donor's draw groups, and the MHFU PMO built from them once skinned."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import NamedTuple

from mhp_formats import p3rd, pmo
from mhp_formats.pac import Pac
from mhp_formats.pmo import Influence, Triangle, Vec3
from mhp_formats.psp.color import Rgba
from mhp_formats.psp.vtype import BITS8, BITS16, VertexType, quantize_vertices

Uv = tuple[float, float]

PALETTE = 8
"""Bone matrices the GE blends: the most joints one group can use."""

# The port's mesh and material values. Retail monsters use lighting 0x80000003 and shadow
# 0x7F7F7F, which are untried on a port.
LIGHTING = 0
BLEND = 0
SHADOW = 0
COLOR = 0xFFFFFFFF
CLIP = 0.0
NO_NORMAL = (0.0, 0.0, 0.0)
"""Written for a donor vertex that carries no normal."""

_FAINT = 1e-4
"""A weight `Part.dominant` does not count."""
_POSITION = 32768
"""A 16-bit position's unit: the GE divides by this, and the range is -32768..32767 of it."""


@dataclass
class Part:
    """One donor draw group."""

    positions: list[Vec3]
    """Model units."""
    normals: list[Vec3]
    """Empty when the donor's vertices carry none."""
    uvs: list[Uv]
    """What the GE samples: the donor mesh's texture scale and offset applied."""
    colors: list[Rgba]
    """Empty when the donor's vertices carry none."""
    triangles: list[Triangle]
    """Wound as drawn; degenerate ones left out."""
    influences: list[list[Influence]]
    """Per vertex, (donor bone, weight)."""
    texture: int
    """The donor's TMH image."""

    def dominant(self) -> int:
        """The donor bone carrying the most weight over the part, the first seen on a tie; -1
        for none."""
        total: dict[int, float] = {}
        for row in self.influences:
            for bone, w in row:
                if w > _FAINT and bone >= 0:
                    total[bone] = total.get(bone, 0.0) + w
        return max(total, key=total.__getitem__, default=-1)


@dataclass
class Skinned:
    """A part bound to the output skeleton."""

    part: Part
    influences: list[list[Influence]]
    """Per vertex, (output joint, weight)."""


class Overflow(NamedTuple):
    """A position the output scale cannot hold, which quantising clamps."""

    group: int
    vertex: int
    axis: int
    value: float
    lost: float
    """Model units the clamp moves it by."""


def donor(model: bytes, geometry: bytes | None = None) -> p3rd.Pmo:
    """The donor's model from a PMO or a model PAC: of a PAC's PMOs the one with the most meshes
    (the other is the low-detail one). `geometry` is the companion file (the next file)."""
    if p3rd.Pmo.sniff(model):
        return p3rd.Pmo.from_bytes(model, geometry)
    pmos = [e for e in Pac.from_bytes(model).entries if p3rd.Pmo.sniff(e)]
    if not pmos:
        raise ValueError("the donor's model PAC holds no MHP3rd PMO")
    read = [p3rd.Pmo.from_bytes(e, geometry if p3rd.Pmo.needs_geometry(e) else None) for e in pmos]
    return max(read, key=lambda m: len(m.meshes))


def parts(model: p3rd.Pmo) -> list[Part]:
    """Every group, in table order."""
    out = []
    for g, group in enumerate(model.groups()):
        vertices = group.block.vertices
        mesh = model.mesh_of(g)
        (su, sv), (ou, ov) = mesh.uv_scale, _uv_offset(mesh)
        material = model.material(g)
        if material is None:
            raise ValueError(f"group {g} points past the material table")
        out.append(
            Part(
                positions=model.positions(g),
                normals=[(x, y, z) for x, y, z in vertices.normals()],
                uvs=[(u * su + ou, v * sv + ov) for u, v in vertices.uvs()],
                colors=vertices.colors() if vertices.color else [],
                triangles=[t for t in model.triangles(g) if len(set(t)) == 3],
                influences=model.influences(g),
                texture=material.texture,
            )
        )
    return out


def _uv_offset(mesh: pmo.Mesh) -> Uv:
    return mesh.uv_offset if isinstance(mesh, p3rd.Mesh) else (0.0, 0.0)


def drop(parts: Sequence[Part], joints: Collection[int]) -> list[Part]:
    """The parts whose dominant donor bone is not one of `joints`."""
    return [p for p in parts if p.dominant() not in joints]


def palette(skinned: Skinned) -> list[int]:
    """The group's joints in slot order, first seen first; `[0]` when it has none."""
    seen: dict[int, None] = {}
    for row in skinned.influences:
        for joint, w in row:
            if w and joint >= 0:
                seen.setdefault(joint)
    return list(seen) or [0]


def overflow(parts: Sequence[Part], scale: Vec3) -> list[Overflow]:
    """Positions outside what a 16-bit position holds at `scale`."""
    out = []
    for g, part in enumerate(parts):
        for v, position in enumerate(part.positions):
            for axis, (value, s) in enumerate(zip(position, scale, strict=True)):
                q = round(value / s * _POSITION)
                kept = min(max(q, -_POSITION), _POSITION - 1)
                if kept != q:
                    out.append(Overflow(g, v, axis, value, value - kept * s / _POSITION))
    return out


def build(skinned: Sequence[Skinned], scale: Vec3) -> pmo.Pmo:
    """The MHFU PMO: one mesh, a group per part, one material per distinct texture in texture
    order, and positions quantised against `scale` (the donor's `Pmo.scale`). Vertex colours are
    not written."""
    textures = sorted({s.part.texture for s in skinned})
    material_of = {t: i for i, t in enumerate(textures)}
    groups = [pmo.Group(_block(s, scale), material_of[s.part.texture], _bones(s)) for s in skinned]
    mesh = pmo.Mesh(groups, list(range(len(textures))), lighting=LIGHTING, blend=BLEND)
    materials = [pmo.Material(color=COLOR, shadow=SHADOW, texture=t) for t in textures]
    return pmo.Pmo([mesh], materials, scale, CLIP)


def _bones(skinned: Skinned) -> list[pmo.BoneSlot]:
    return [pmo.BoneSlot(slot, joint) for slot, joint in enumerate(palette(skinned))]


def _block(skinned: Skinned, scale: Vec3) -> pmo.Block:
    joints = palette(skinned)
    if len(joints) > PALETTE:
        raise ValueError(f"a group blends {len(joints)} joints, more than the GE's {PALETTE}")
    slot_of = {j: s for s, j in enumerate(joints)}
    weights = []
    for row in skinned.influences:
        w = [0.0] * len(joints)
        for joint, weight in row:
            if weight and joint in slot_of:
                w[slot_of[joint]] += weight
        weights.append(w)
    part = skinned.part
    vtype = VertexType(
        texture=BITS16, normal=BITS8, position=BITS16, weight=BITS8, weight_count=len(joints)
    )
    vertices = quantize_vertices(
        vtype,
        part.positions,
        scale,
        normals=part.normals or [NO_NORMAL] * len(part.positions),
        uvs=part.uvs,
        weights=weights,
    )
    return pmo.Block.build(vertices, part.triangles)
