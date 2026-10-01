# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""File edits: a stage PAC that changes size, for a repacked game rather than a running one."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace

import numpy as np
from mhp_formats.fu.stage import Stage, Tri, build_hits
from mhp_formats.psp.ge import Command, Op, Prim
from mhp_formats.psp.strip import Stripper
from mhp_formats.psp.vtype import BITS8, BITS16, quantize

from .file import TERRAIN, StageFile
from .obj import ObjMesh

Vec3 = tuple[float, float, float]


def rebuild(
    stage: Stage,
    extra: Sequence[Tri] = (),
    chunks: Iterable[int] | None = None,
    *,
    pad: float = 0.0,
    refit: bool = False,
) -> Stage:
    """The stage with the broadphase of `chunks` (all when None) rebuilt from the exact
    overlap, `extra` added to each; `refit` sizes the lattice to the triangles."""
    coll = stage.collision
    if coll is None:
        raise ValueError("the stage has no collision")
    which = set(range(len(coll.chunks)) if chunks is None else chunks)
    if not which <= set(range(len(coll.chunks))):
        raise ValueError(f"no collision chunk {sorted(which)}")
    out = []
    for k, c in enumerate(coll.chunks):
        if k in which:
            tris = [*c.tris, *extra]
            c = build_hits(tris, None if refit else c.grid, c.cell, c.origin, pad)
        out.append(c)
    return replace(stage, collision=replace(coll, chunks=out))


def grow(
    stage: StageFile, sub: int, mesh: ObjMesh, group: int | None = None, *, solid: int | None = None
) -> tuple[Stage, str]:
    """The stage with `mesh` added to a group (the nearest when None): new vertices, each lit
    like its nearest neighbour, drawn by one triangle list after the group's last PRIM (before
    the list's closing OFFSETADDR, past which a draw finds no vertices). `solid` adds the same
    triangles to that collision chunk."""
    pmo = stage.pmo(sub)
    if pmo is None:
        raise ValueError(f"{stage.label} sub {sub} holds no PMO")
    if not mesh.triangles:
        raise ValueError("the mesh has no faces")
    groups = pmo.groups()
    if group is None:
        centre = np.mean(np.asarray(mesh.positions), axis=0)
        drawn = [g for g, grp in enumerate(groups) if len(grp.block.vertices)]
        group = min(drawn, key=lambda g: _nearest(pmo.positions(g), centre)[1])
    block = groups[group].block
    if sum(grp.block is block for grp in groups) > 1:
        raise ValueError(f"group {group} shares its block with another group")
    v = block.vertices
    if not len(v) or v.vtype.index not in (BITS8, BITS16):
        raise ValueError(f"group {group} draws no indexed vertices to grow")
    have = pmo.positions(group)
    base = len(v)
    rows = quantize(mesh.positions, pmo.scale, v.vtype.layout.position)
    uvs = None
    if mesh.uvs is not None and v.vtype.layout.texture is not None:
        uvs = quantize(mesh.uvs, (1.0, 1.0), v.vtype.layout.texture)
    per = len(v.padding) // base
    for k, p in enumerate(mesh.positions):
        src = _nearest(have, p)[0]
        for name in ("weight", "texture", "normal"):
            column = getattr(v, name)
            if column:
                column.append(column[src])
        if v.color:
            v.color.append(v.color[src])
        v.position.append(rows[k])
        if uvs is not None:
            v.texture[-1] = uvs[k]
        if per:
            v.padding += v.padding[src * per : (src + 1) * per]
    if len(v) > 0x100 and v.vtype.index == BITS8:
        v.vtype = replace(v.vtype, index=BITS16)
    if len(v) > 0x10000:
        raise ValueError(f"group {group} would hold {len(v)} vertices, past 16-bit indices")
    prims = [k for k, c in enumerate(block.commands) if c.op == Op.PRIM]
    at = prims[-1] + 1 if prims else len(block.commands) - 1
    face = next((c.arg & 1 for c in reversed(block.commands[:at]) if c.op == Op.FFACE), 0)
    shifted = [(a + base, b + base, c + base) for a, b, c in mesh.triangles]
    tris = Stripper(shifted).loose(len(shifted), face)
    block.commands.insert(at, Command.prim(Prim.TRIANGLES, 3 * len(tris)))
    block.indices += [i for t in tris for i in t]
    data = pmo.to_bytes()
    st = stage.stage
    edited = replace(st, terrain=data) if sub == TERRAIN else replace(st, props=data)
    line = f"{stage.label} sub {sub} g{group}: {base} -> {len(v)} vertices, +{len(tris)} faces"
    if solid is not None:
        extra = [Tri.from_verts(*c) for c in mesh.corners()]
        edited = rebuild(edited, extra, [solid])
        line += f", +{len(extra)} collision triangles in chunk {solid}"
    return edited, line


def _nearest(points: Sequence[Vec3], p: Sequence[float]) -> tuple[int, float]:
    d = ((np.asarray(points, np.float64) - np.asarray(p, np.float64)) ** 2).sum(axis=1)
    k = int(np.argmin(d))
    return k, float(d[k])
