# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The port's geometry: the donor's draw groups, and the MHFU PMO built from them once skinned."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np
from mhp_formats import p3rd, pmo
from mhp_formats.pac import Pac
from mhp_formats.pmo import Influence, Triangle, Vec3
from mhp_formats.psp.color import Rgba
from mhp_formats.psp.vtype import BITS8, BITS16, VertexType, quantize_vertices
from numpy.typing import NDArray

from .rig import OFFSET_TOL, Tip

Uv = tuple[float, float]

PALETTE = 8
"""Bone matrices the GE blends: the most joints one group can use."""

# The port's mesh and material values. Retail monsters use lighting 0x80000003 and shadow
# 0x7F7F7F, which are untried on a port.
LIGHTING = 0
BLEND = 0
SHADOW = 0
COLOR = 0xFFFFFFFF
NO_NORMAL = (0.0, 0.0, 0.0)
"""Written for a donor vertex that carries no normal."""

CUT_WELD = 0.5
"""The share of a cut-face part's vertices that weld to the body."""
CUT_FACING = 0.7
"""How squarely, area-weighted |cos|, a cut-face part's faces face along the tail."""

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


def tip_parts(skinned: Sequence[Skinned], joints: Collection[int]) -> list[int]:
    """The parts that ride only `joints` (a tail tip's chain); raises for a part that blends
    them with other joints, which neither the monster nor the dropped tail could draw."""
    out = []
    for i, s in enumerate(skinned):
        on = {j in joints for row in s.influences for j, w in row if w > _FAINT and j >= 0}
        if on == {True, False}:
            raise ValueError(f"part {i} blends the tip's joints {sorted(joints)} with the body's")
        if on == {True}:
            out.append(i)
    return out


def cut_parts(skinned: Sequence[Skinned], tip: Collection[int], offset: Vec3) -> list[int]:
    """Of the `tip` parts, the cut face: at least `CUT_WELD` of its vertices weld to the body's
    once moved by `offset` (the tip on the stump), and its faces face along the tail
    (`CUT_FACING`), the seam toward the tip. Native caps weld all; a donor's keeps its centre
    apart, and a fin welded at the seam faces sideways."""
    body = np.array([p for i, s in enumerate(skinned) if i not in tip for p in s.part.positions])
    if not tip or not len(body):
        return []
    moved = {i: np.array(skinned[i].part.positions) + np.array(offset) for i in sorted(tip)}
    welded = {i: _gap(x, body) < OFFSET_TOL for i, x in moved.items()}
    every = np.concatenate(list(moved.values()))
    seam = np.concatenate([x[welded[i]] for i, x in moved.items()])
    if not len(seam):
        return []
    axis = every.mean(axis=0) - seam.mean(axis=0)
    axis /= np.linalg.norm(axis) or 1.0
    return [
        i
        for i in sorted(tip)
        if welded[i].mean() >= CUT_WELD
        and _facing(moved[i], skinned[i].part.triangles, axis) >= CUT_FACING
    ]


def _gap(points: NDArray[np.float64], to: NDArray[np.float64]) -> NDArray[np.float64]:
    """Each point's distance to the nearest of `to`."""
    out: NDArray[np.float64] = np.sqrt(((points[:, None] - to[None]) ** 2).sum(axis=-1)).min(axis=1)
    return out


def _facing(
    positions: NDArray[np.float64], triangles: Sequence[Triangle], axis: NDArray[np.float64]
) -> float:
    """The area-weighted mean |cos| between the triangles' normals and `axis`."""
    t = np.array(triangles, dtype=np.intp).reshape(-1, 3)
    n = np.cross(positions[t[:, 1]] - positions[t[:, 0]], positions[t[:, 2]] - positions[t[:, 0]])
    area = np.linalg.norm(n, axis=1).sum()
    return float(np.abs(n @ axis).sum() / area) if area else 0.0


def build(skinned: Sequence[Skinned], scale: Vec3, chain: Tip | None = None) -> pmo.Pmo:
    """The MHFU PMO: mesh 0 holds the body; mesh 1, where the host's dropped tail draws from,
    the parts riding the `chain` (`tip_parts`): its material 0 the tip's, 1 the cut face's
    (`cut_parts`; em75 hides it until the cut; a copy of 0 without one), then the tip's other
    textures. A group per part, one material per texture of a mesh in texture order, and
    positions quantised against `scale` (the donor's `Pmo.scale`). Vertex colours are not
    written."""
    tip = [] if chain is None else tip_parts(skinned, chain.joints)
    cut = cut_parts(skinned, tip, chain.offset) if chain is not None else []
    body = [s for i, s in enumerate(skinned) if i not in tip]
    textures = sorted({s.part.texture for s in body})
    meshes = [[(s, textures.index(s.part.texture)) for s in body]]
    slots = [textures]
    if tip:
        plain = sorted({skinned[i].part.texture for i in tip if i not in cut})
        face = sorted({skinned[i].part.texture for i in cut})
        if len(face) > 1:
            raise ValueError(f"the cut face spans textures {face}; it takes one material")
        first = (plain or face)[0]
        slots.append([first, (face or plain)[0], *plain[1:]])
        rest = [first, -1, *plain[1:]]
        meshes.append(
            [
                (skinned[i], 1 if i in cut else rest.index(skinned[i].part.texture))
                for i in sorted(tip)
            ]
        )
    out: list[pmo.Mesh] = []
    materials: list[pmo.Material] = []
    for parts, textures in zip(meshes, slots, strict=True):
        first = len(materials)
        materials += [pmo.Material(color=COLOR, shadow=SHADOW, texture=t) for t in textures]
        groups = [group(s, scale, k) for s, k in parts]
        out.append(
            pmo.Mesh(groups, list(range(first, len(materials))), lighting=LIGHTING, blend=BLEND)
        )
    # the engine's cull sphere (Pmo.clip): 0 culls the model as a point near its feet
    return pmo.Pmo(out, materials, scale, max(scale))


def group(skinned: Skinned, scale: Vec3, material: int = 0) -> pmo.Group:
    """One group of `build`: the part quantised against `scale`, its palette patched from slot 0."""
    return pmo.Group(_block(skinned, scale), material, _bones(skinned))


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
