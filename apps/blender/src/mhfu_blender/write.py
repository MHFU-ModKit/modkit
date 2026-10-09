# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""An edited monster written as a model PAC, and placed for the running game; no bpy.

What Blender holds comes in as plain data: the bind joints, each group's geometry and each slot's
clip. An MHFU model keeps every byte no edit reaches: a group whose vertex count and triangles are
unchanged is written in place, at the PMO's own size; any other is rebuilt and the PMO laid out
again. An MHP3rd model comes out as an MHFU PAC with geometry, skeleton and textures, no clips;
its tail tip, when its skeleton has one, in mesh record 1 (`mhfu_port.mesh.build`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import NamedTuple

import numpy as np
from mhfu import inject
from mhfu_port import constraints, fk, mesh, motion, rig
from mhfu_port.mesh import Part, Skinned
from mhfu_port.model import ANIMATION, MHFU, MODEL, SKELETON, MeshGroup, Model
from mhp_formats import fu, pmo
from mhp_formats.anim import Clip
from mhp_formats.pac import Pac
from mhp_formats.pmo import BoneSlot, Vec3
from mhp_formats.psp.vtype import Row, Vertices, quantize, quantize_weights
from mhp_formats.skeleton import FU_MAGIC, Skeleton
from mhp_formats.tmh import Tmh
from numpy.typing import ArrayLike, NDArray

Floats = NDArray[np.float64]

BIND_EPS = 1e-3
"""Game units a bind joint may drift (Blender keeps float32) and still count as unmoved."""
WEIGHT_EPS = 1e-3
PAD = 0x800
"""A monster PAC's size is a multiple of this."""
EMPTY_ANIM = fu.Anim([[]])
"""The animation entry of an MHP3rd model's PAC: one stream, no slots."""


class ExportError(ValueError):
    """The edit cannot be written."""


@dataclass
class Geometry:
    """One group as Blender holds it, in model units."""

    positions: Floats
    """`(vertices, 3)`."""
    triangles: NDArray[np.int32]
    """`(faces, 3)` vertex numbers, wound as drawn."""
    normals: Floats
    """`(vertices, 3)`, unit length; NaN for a vertex on no face."""
    turned: NDArray[np.bool_]
    """Per vertex, whether its normal was edited: a group written in place keeps the others'
    stored normals byte for byte."""
    uvs: Floats | None
    """`(faces, 3, 2)`, each corner's UV as the GE samples it; None without a UV layer."""
    influences: Sequence[Sequence[tuple[int, float]]]
    """Per vertex, (joint, weight)."""


@dataclass
class _Mesh:
    """A group as a PMO holds it: `Geometry` with one UV per vertex, NaN on a vertex on no face."""

    positions: Floats
    triangles: NDArray[np.int64]
    normals: Floats
    turned: NDArray[np.bool_]
    uvs: Floats | None
    influences: list[Sequence[tuple[int, float]]]


@dataclass
class Written:
    pac: bytes
    results: list[constraints.Result]
    """What the validator found that does not stop the export: warnings, and errors the source
    has too."""
    rebuilt: list[int] = field(default_factory=list)
    """Groups laid out again, which changes the PAC's size."""
    notes: list[str] = field(default_factory=list)


class Placed(NamedTuple):
    path: str
    file_id: int
    """The extracted file the PAC replaces; the engine's id is one more."""
    grown: bool
    """Larger than the source: the relocate path, not an overwrite in place."""

    @property
    def lua(self) -> str:
        """The framework call a Lua mod makes to load it."""
        where = f"ms0:/PSP/{inject.INJECT_SUBDIR}/"
        engine = self.file_id + 1
        if self.grown:
            orig = where + inject.orig_filename(self.file_id)
            name = where + inject.relocate_filename(self.file_id)
            return f'mhfu.inject_relocate({engine}, "{name}", "{orig}")'
        return f'mhfu.inject_register({engine}, "{where}{inject.inject_filename(self.file_id)}")'


def skeleton(model: Model, joints: ArrayLike) -> Skeleton:
    """`model`'s skeleton with its bind joints at `joints` (`(joints, 3)`, model space); a joint
    within `BIND_EPS` of its stored offset keeps it."""
    heads = np.asarray(joints, dtype=np.float64).reshape(-1, 3)
    source = model.skeleton
    if len(heads) != len(source.bones):
        raise ExportError(f"{len(heads)} joints for the skeleton's {len(source.bones)}")
    parents = model.rig.parents
    local = heads - np.where(parents[:, None] >= 0, heads[parents], 0.0)
    bones = []
    for bone, offset in zip(source.bones, local, strict=True):
        if np.abs(offset - bone.position).max() > BIND_EPS:
            x, y, z = (float(np.float32(c)) for c in offset)
            bone = replace(bone, position=(x, y, z))
        bones.append(bone)
    return replace(source, bones=bones)


def write(
    model: Model,
    skeleton: Skeleton,
    groups: Mapping[int, Geometry],
    clips: Mapping[int, Clip],
) -> Written:
    """The model PAC: `skeleton` as `skeleton()` gives it, each group's geometry (a group missing
    here draws nothing) and each slot's whole-rig clip (`animation.to_clip`); a slot missing here
    keeps its clip. Raises ExportError where it cannot be written or breaks an engine rule the
    source keeps."""
    if model.pac is None:
        raise ExportError(f"{model.name}: no source PAC")
    unknown = sorted(set(groups) - set(range(len(model.groups))))
    if unknown:
        raise ExportError(f"the model has no group {few(unknown)}")
    if model.game == MHFU:
        return _mhfu(model, skeleton, groups, clips)
    return _donor(model, skeleton, groups)


def place(data: bytes, model: Model, root: str | None = None, file_id: int | None = None) -> Placed:
    """Write `data`, an export of `model`, into the memory stick's inject folder (`root` as
    `inject.memstick` takes it): the source's size replaces the file in place, a larger one goes
    the relocate path. The file id comes from the source's `file_NNNNN` name unless given."""
    if model.game != MHFU or model.pac is None:
        raise ExportError("an MHP3rd model reaches the game as a port: build it from a manifest")
    if len(data) < len(model.pac):
        raise ExportError(f"{len(data)} bytes, under the source's {len(model.pac)}")
    try:
        fid = file_id if file_id is not None else inject.file_id_from_name(model.name)
        folder = inject.default_inject_dir(True, root)
    except (ValueError, FileNotFoundError) as e:
        raise ExportError(str(e)) from None
    grown = len(data) > len(model.pac)
    put = inject.write_relocate_bytes if grown else inject.write_inject_bytes
    return Placed(put(data, fid, folder, orig=model.pac), fid, grown)


# MHFU


def _mhfu(
    model: Model, skeleton: Skeleton, groups: Mapping[int, Geometry], clips: Mapping[int, Clip]
) -> Written:
    assert model.pac is not None and model.anim is not None
    source = Pac.from_bytes(model.pac)
    entries = list(source.entries)
    out = pmo.Pmo.from_bytes(entries[MODEL])
    notes: list[str] = []
    rebuilt = _geometry(out, model.groups, groups, notes)
    anim = model.anim
    for slot, clip in sorted(clips.items()):
        try:
            anim = motion.put(anim, slot, clip, skeleton)
        except ValueError as e:
            raise ExportError(str(e)) from None
    before = constraints.validate(
        pmo.Pmo.from_bytes(entries[MODEL]), model.skeleton, model.anim, model.skeleton
    )
    results = _judge(constraints.validate(out, skeleton, anim, model.skeleton), before)
    if rebuilt:
        entries[MODEL] = out.to_bytes()
    else:
        entries[MODEL], moved = out.to_bytes_inplace(entries[MODEL])
        assert not moved, "an in-place group moved"
    entries[SKELETON] = skeleton.to_bytes()
    entries[ANIMATION] = anim.to_bytes()
    body = Pac(entries, source.align).to_bytes()
    if len(body) + len(source.tail) == len(model.pac):
        data = body + source.tail
    else:
        data = _padded(body, len(model.pac))
    return Written(data, results, rebuilt, notes)


def _geometry(
    out: pmo.Pmo, groups: Sequence[MeshGroup], edits: Mapping[int, Geometry], notes: list[str]
) -> list[int]:
    """Write `edits` into `out`'s groups; the groups rebuilt."""
    table = out.groups()
    palettes = [out.palette(g) for g in range(len(table))]
    rebuilt, cleared = [], []
    for g, group in enumerate(table):
        edit = edits.get(g)
        if edit is None:
            group.block.clear(keep_layout=True)
            cleared.append(g)
            continue
        geo = _split(g, edit, notes)
        _bounds(geo, out.scale_of(g), g)
        same = len(geo.positions) == groups[g].n_vertices and np.array_equal(
            geo.triangles, groups[g].triangles
        )
        if not (same and _in_place(out, g, geo, palettes[g])):
            _rebuild(out, g, geo)
            rebuilt.append(g)
    if cleared:
        notes.append(f"groups {few(cleared)} have no object: they draw nothing")
    if rebuilt:
        _repatch(table, palettes, set(rebuilt))
    return rebuilt


def _in_place(out: pmo.Pmo, g: int, geo: _Mesh, palette: list[int]) -> bool:
    """Write `geo` into group `g`'s own vertices; False where its weights need joints the
    group's palette lacks, which only a rebuild gives them."""
    vertices = out.groups()[g].block.vertices
    vt = vertices.vtype
    lay = vt.layout
    if vt.morph_count != 1 or vt.through:
        return False
    weights = _new_weights(vertices, geo.influences, palette, g)
    if weights is None:
        return False
    for i, row in weights.items():
        vertices.weight[i] = row
    vertices.position = quantize(geo.positions.tolist(), out.scale_of(g), lay.position)
    if lay.normal is not None:
        turned = np.asarray(geo.turned & np.isfinite(geo.normals).all(axis=1))
        normals = geo.normals[turned].tolist()
        _rows(vertices.normal, quantize(normals, (1.0, 1.0, 1.0), lay.normal), turned)
    if lay.texture is not None and geo.uvs is not None:
        kept = np.asarray(~np.isnan(geo.uvs).any(axis=1))
        uvs = (geo.uvs[kept] / _uv_scale(out, g)).tolist()
        _rows(vertices.texture, quantize(uvs, (1.0, 1.0), lay.texture), kept)
    return True


def _new_weights(
    vertices: Vertices,
    influences: Sequence[Sequence[tuple[int, float]]],
    palette: list[int],
    g: int,
) -> dict[int, tuple[float, ...]] | None:
    """The weight rows of the vertices whose weights changed, over the palette's slots; None
    where a joint is in no slot. A rigid group's vertices must stay on its one joint."""
    vt = vertices.vtype
    if not vt.weight:
        rigid = _weights([(palette[0], 1.0)] if palette else [])
        same = all(_close(_weights(row), rigid) for row in influences)
        return {} if same else None
    lay = vt.layout.weight
    assert lay is not None
    slots = (palette + [-1] * vt.weight_count)[: vt.weight_count]
    out = {}
    for i, (stored, row) in enumerate(zip(vertices.weights(), influences, strict=True)):
        new = _weights(row)
        if _close(_weights(list(zip(slots, stored, strict=True))), new):
            continue
        _weighted(g, [new])
        weights = [0.0] * vt.weight_count
        for joint, w in new.items():
            if joint not in slots:
                return None
            weights[slots.index(joint)] += w
        out[i] = quantize_weights([weights], lay)[0]
    return out


def _rebuild(out: pmo.Pmo, g: int, geo: _Mesh) -> None:
    group = out.groups()[g]
    part = _part(g, geo, 0, _uv_scale(out, g))
    try:
        new = mesh.group(Skinned(part, part.influences), out.scale_of(g), group.material)
    except ValueError as e:
        raise ExportError(f"group {g}: {e}") from None
    group.block, group.bones = new.block, new.bones


def _repatch(table: list[pmo.Group], palettes: list[list[int]], rebuilt: set[int]) -> None:
    """Give each group not rebuilt the palette slots it had: the palette runs on from group to
    group, and a rebuilt group's patch changes what the ones after it inherit."""
    running: dict[int, int] = {}
    for g, group in enumerate(table):
        running.update(group.bones)
        if g in rebuilt:
            continue
        vt = group.block.vertices.vtype
        had = palettes[g]
        wanted = range(min(vt.weight_count if vt.weight else 1, len(had)))
        fix = [BoneSlot(s, had[s]) for s in wanted if had[s] >= 0 and running.get(s) != had[s]]
        if fix:
            group.bones = [*group.bones, *fix]
            running.update(fix)


# MHP3rd


def _donor(model: Model, skeleton: Skeleton, groups: Mapping[int, Geometry]) -> Written:
    assert model.pac is not None
    entries = Pac.from_bytes(model.pac).entries
    scale = mesh.donor(model.pac, model.geometry).scale
    kept = [g for g in range(len(model.groups)) if g in groups]
    notes: list[str] = []
    skinned = []
    for g in kept:
        part = _part(g, _split(g, groups[g], notes), model.groups[g].material or 0, (1.0, 1.0))
        skinned.append(Skinned(part, part.influences))
    over = mesh.overflow([s.part for s in skinned], scale)
    if over:
        o = over[0]
        raise ExportError(
            f"{len(over)} positions outside the model's {_box(scale)} box, first group "
            f"{kept[o.group]} vertex {o.vertex}"
        )
    bones = [replace(b, name=None) for b in skeleton.bones]
    fu_skeleton = Skeleton(bones, list(skeleton.params), magic=FU_MAGIC)
    try:
        out = mesh.build(skinned, scale, tip=rig.tip_of(fu_skeleton))
    except ValueError as e:
        raise ExportError(str(e)) from None
    notes[:0] = ["no clips: an in-game monster from an MHP3rd one is a port, built from a manifest"]
    results = _judge(constraints.validate(out, fu_skeleton), [])
    tmh = next((e for e in entries if Tmh.sniff(e)), b"")
    pac = [fu_skeleton.to_bytes(), out.to_bytes(), tmh, EMPTY_ANIM.to_bytes()]
    dropped = [g for g in range(len(model.groups)) if g not in groups]
    if dropped:
        notes.append(f"groups {few(dropped)} have no object: left out")
    return Written(_padded(Pac(pac).to_bytes(), 0), results, kept, notes)


# shared


def _split(g: int, geo: Geometry, notes: list[str]) -> _Mesh:
    """`geo` with one UV per vertex: a vertex whose corners carry several keeps its first corner's
    and gets a copy, appended, for each other UV."""
    triangles = np.asarray(geo.triangles, dtype=np.int64).reshape(-1, 3)
    influences = list(geo.influences)
    if geo.uvs is None:
        return _Mesh(geo.positions, triangles, geo.normals, geo.turned, None, influences)
    corners = np.asarray(geo.uvs, dtype=np.float64).reshape(-1, 2)
    n = len(geo.positions)
    uvs = np.full((n, 2), np.nan)
    claimed = np.zeros(n, dtype=bool)
    target: dict[tuple[int, float, float], int] = {}
    copies: list[int] = []
    flat = triangles.ravel().copy()
    for c, (v, (u, w)) in enumerate(zip(flat.tolist(), corners.tolist(), strict=True)):
        to = target.get((v, u, w))
        if to is None:
            if claimed[v]:
                to = n + len(copies)
                copies.append(c)
            else:
                to, claimed[v] = v, True
                uvs[v] = u, w
            target[v, u, w] = to
        flat[c] = to
    if not copies:
        return _Mesh(geo.positions, triangles, geo.normals, geo.turned, uvs, influences)
    source = triangles.ravel()[copies]
    notes.append(f"group {g}: {len(copies)} vertices split along UV seams")
    return _Mesh(
        np.vstack([geo.positions, geo.positions[source]]),
        flat.reshape(-1, 3),
        np.vstack([geo.normals, geo.normals[source]]),
        np.concatenate([geo.turned, geo.turned[source]]),
        np.vstack([uvs, corners[copies]]),
        influences + [influences[v] for v in source.tolist()],
    )


def _part(g: int, geo: _Mesh, texture: int, uv_scale: tuple[float, float]) -> Part:
    """Group `g`'s `geo` as a part to build a group from; a value Blender lacks is zero."""
    normals = np.nan_to_num(geo.normals)
    uvs = np.zeros((len(geo.positions), 2)) if geo.uvs is None else np.nan_to_num(geo.uvs)
    influences = _weighted(g, [_weights(row) for row in geo.influences])
    return Part(
        _vec3(geo.positions),
        _vec3(normals),
        [(u, v) for u, v in (uvs / uv_scale).tolist()],
        [],
        [(a, b, c) for a, b, c in np.asarray(geo.triangles).tolist()],
        [sorted(row.items()) for row in influences],
        texture,
    )


def _weighted(g: int, rows: list[dict[int, float]]) -> list[dict[int, float]]:
    """`rows`; raises where a vertex has no weight, which collapses it to the origin."""
    bare = [i for i, row in enumerate(rows) if not row]
    if bare:
        raise ExportError(f"group {g}: {len(bare)} vertices have no weight; weight them")
    return rows


def _weights(row: Sequence[tuple[int, float]]) -> dict[int, float]:
    """Joint to weight, normalised; slots of no joint and weights the import drops left out."""
    out: dict[int, float] = {}
    for joint, w in row:
        if joint >= 0 and w > fk.WEIGHT_EPS:
            out[joint] = out.get(joint, 0.0) + float(w)
    total = sum(out.values())
    return {j: w / total for j, w in out.items()} if total > 0 else {}


def _close(a: Mapping[int, float], b: Mapping[int, float]) -> bool:
    return all(abs(a.get(j, 0.0) - b.get(j, 0.0)) <= WEIGHT_EPS for j in {*a, *b})


def _rows(target: list[Row], rows: list[Row], mask: NDArray[np.bool_]) -> None:
    for i, row in zip(np.flatnonzero(mask), rows, strict=True):
        target[i] = row


def _bounds(geo: _Mesh, scale: Vec3, g: int) -> None:
    part = Part(_vec3(geo.positions), [], [], [], [], [], 0)
    over = mesh.overflow([part], scale)
    if over:
        raise ExportError(
            f"group {g}: {len(over)} positions outside the model's {_box(scale)} box, "
            f"first vertex {over[0].vertex}"
        )


def _uv_scale(out: pmo.Pmo, g: int) -> tuple[float, float]:
    su, sv = out.mesh_of(g).uv_scale
    return su or 1.0, sv or 1.0


def _judge(
    after: list[constraints.Result], before: list[constraints.Result]
) -> list[constraints.Result]:
    """`after` less its errors; raises on an error `before` lacks."""
    known = set(before)
    errors = [r for r in after if r.level == "error" and r not in known]
    if errors:
        raise ExportError("\n".join(map(str, errors)))
    return after


def _padded(body: bytes, size: int) -> bytes:
    """`body` padded with zeros to `size`, or past it to the next `PAD`."""
    end = size if len(body) <= size else -(-len(body) // PAD) * PAD
    return body + bytes(end - len(body))


def _vec3(rows: Floats) -> list[Vec3]:
    return [(x, y, z) for x, y, z in np.asarray(rows, dtype=np.float64).reshape(-1, 3).tolist()]


def _box(scale: Vec3) -> str:
    return "x".join(f"{2 * s:g}" for s in scale)


def few(items: Sequence[int], limit: int = 8) -> str:
    """`items` for a message: the first `limit`, and how many more."""
    shown = ", ".join(map(str, items[:limit]))
    return shown + (f" (+{len(items) - limit})" if len(items) > limit else "")
