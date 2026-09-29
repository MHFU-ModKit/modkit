# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A structural diff of two MHFU monster PACs: what differs in the model, and what differs only
in how the bytes are laid out.

PMO groups are matched by index. Inside a group a vertex is identified by its position: two
vertices within `STEPS` quantisation steps are the same point, so a re-ordered or re-indexed
vertex buffer is not a difference. Old and new vertices of one point pair up by their other
attributes; triangles compare as multisets of points, so re-packed strips are not a difference
either.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from itertools import zip_longest
from typing import Any

import numpy as np
from mhp_formats import FormatError, fu, pmo
from mhp_formats.anim import Clip, Keyframe, Stream, Track
from mhp_formats.pac import Pac
from mhp_formats.psp.ge import Op
from mhp_formats.psp.vtype import Vertices
from mhp_formats.skeleton import Skeleton
from numpy.typing import NDArray

KINDS = (
    "pac.entries",
    "pac.align",
    "pac.tail",
    "pac.entry",
    "skeleton.bones",
    "skeleton.parent",
    "skeleton.position",
    "skeleton.stream",
    "skeleton.kind",
    "skeleton.links",
    "skeleton.bind",
    "skeleton.params",
    "skeleton.tail",
    "pmo.groups",
    "pmo.mesh",
    "pmo.vertices",
    "pmo.positions",
    "pmo.normals",
    "pmo.uvs",
    "pmo.colors",
    "pmo.weights",
    "pmo.triangles.missing",
    "pmo.triangles.added",
    "pmo.triangles.mirrored",
    "pmo.triangles.duplicated",
    "pmo.material",
    "pmo.texture",
    "pmo.layout",
    "tmh",
    "anim.streams",
    "anim.clip.missing",
    "anim.clip.added",
    "anim.tracks",
    "anim.channels",
    "anim.keyframes",
    "anim.loop",
    "anim.loop_start",
    "anim.layout",
)
"""Every finding kind. `missing` is in OLD only, `added` in NEW only."""

SKELETON, MODEL, TEXTURES, ANIMATION = range(4)
"""The monster PAC's entries this diff decodes; the rest compare as bytes."""

STEPS = 2.5
"""Positions this many quantisation steps apart (per axis) are the same point."""

Floats = NDArray[np.float64]


@dataclass(frozen=True)
class Finding:
    kind: str
    """One of `KINDS`."""
    where: tuple[int, ...] = ()
    """Bone; group; `(stream, slot)`; entry; empty for a whole part."""
    count: int = 1
    """How many items differ: vertices, triangles, keyframes, bones."""
    detail: str = ""
    layout: bool = False
    """Only the bytes differ, not the model they decode to."""

    def place(self) -> str:
        part = self.kind.split(".")[0]
        if not self.where:
            return part
        mark = {"skeleton": "b", "pmo": "g", "anim": "s", "pac": "e"}.get(part, "")
        return mark + "/".join(map(str, self.where))


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.findings)

    def counts(self) -> dict[str, int]:
        """Findings per kind."""
        return dict(Counter(f.kind for f in self.findings))

    def groups(self) -> dict[int, list[Finding]]:
        """PMO findings per group."""
        out: dict[int, list[Finding]] = {}
        for f in self.findings:
            if f.kind.startswith("pmo.") and f.where:
                out.setdefault(f.where[0], []).append(f)
        return out

    def clips(self) -> dict[tuple[int, int], list[Finding]]:
        """Animation findings per `(stream, slot)`."""
        out: dict[tuple[int, int], list[Finding]] = {}
        for f in self.findings:
            if f.kind.startswith("anim.") and len(f.where) >= 2:
                out.setdefault((f.where[0], f.where[1]), []).append(f)
        return out

    def text(self, limit: int = 12) -> str:
        """Per kind: findings, items, and up to `limit` places with their detail, a whole part's
        first."""
        model = [f for f in self.findings if not f.layout]
        if not self.findings:
            return "no differences"
        lines = [f"{len(model)} model findings, {len(self.findings) - len(model)} layout-only"]
        for layout in (False, True):
            by_kind: dict[str, list[Finding]] = {}
            for f in self.findings:
                if f.layout == layout:
                    by_kind.setdefault(f.kind, []).append(f)
            if by_kind:
                lines.append("layout only:" if layout else "model:")
            for kind, found in by_kind.items():
                items = sum(f.count for f in found)
                lines.append(f"  {kind}: {len(found)} findings, {items} items")
                for f in sorted(found, key=lambda f: bool(f.where))[:limit]:
                    lines.append(f"    {f.place()}: {f.detail}" if f.detail else f"    {f.place()}")
                if len(found) > limit:
                    lines.append(f"    ... {len(found) - limit} more")
        return "\n".join(lines)

    def to_json(self) -> dict[str, Any]:
        return {"counts": self.counts(), "findings": [asdict(f) for f in self.findings]}


def diff(old: bytes, new: bytes) -> Report:
    """Two monster PACs, entry by entry: skeleton, PMO, TMH, animation, the rest as bytes."""
    a, b = Pac.from_bytes(old), Pac.from_bytes(new)
    out: list[Finding] = []
    if len(a.entries) != len(b.entries):
        out.append(Finding("pac.entries", detail=f"{len(a.entries)} -> {len(b.entries)}"))
    if a.align != b.align:
        out.append(Finding("pac.align", detail=f"{a.align} -> {b.align}"))
    if a.tail != b.tail:
        out.append(Finding("pac.tail", detail=f"{len(a.tail)} -> {len(b.tail)} bytes"))
    parts: dict[int, Callable[[bytes, bytes], list[Finding]]] = {
        SKELETON: _skeleton,
        MODEL: _model,
        TEXTURES: _textures,
        ANIMATION: _animation,
    }
    for i, (x, y) in enumerate(zip(a.entries, b.entries, strict=False)):
        if x == y:
            continue
        compare = parts.get(i)
        if compare is None:
            out.append(Finding("pac.entry", (i,), detail=f"{len(x)} -> {len(y)} bytes"))
            continue
        try:
            found = compare(x, y)
        except FormatError as e:
            found = [Finding("pac.entry", (i,), detail=f"does not decode: {e}")]
        out += found or [Finding("pac.entry", (i,), detail="bytes differ", layout=True)]
    return Report(out)


def _textures(old: bytes, new: bytes) -> list[Finding]:
    return [Finding("tmh", detail=f"{len(old)} -> {len(new)} bytes")]


# skeleton ------------------------------------------------------------------------------------


def _skeleton(old: bytes, new: bytes) -> list[Finding]:
    a, b = Skeleton.from_bytes(old), Skeleton.from_bytes(new)
    out = []
    if len(a.bones) != len(b.bones):
        out.append(Finding("skeleton.bones", detail=f"{len(a.bones)} -> {len(b.bones)}"))
    for i, (x, y) in enumerate(zip(a.bones, b.bones, strict=False)):
        fields = {
            "skeleton.parent": (x.parent, y.parent),
            "skeleton.position": (x.position, y.position),
            "skeleton.stream": (x.stream, y.stream),
            "skeleton.kind": (x.kind, y.kind),
            "skeleton.links": ((x.child, x.sibling, x.link), (y.child, y.sibling, y.link)),
            "skeleton.bind": ((x.rotation, x.scale, x.name), (y.rotation, y.scale, y.name)),
        }
        for kind, (u, v) in fields.items():
            if u != v:
                out.append(Finding(kind, (i,), detail=f"{u} -> {v}"))
    head = (a.params, a.params_count, a.magic), (b.params, b.params_count, b.magic)
    if head[0] != head[1]:
        out.append(Finding("skeleton.params", detail=f"{head[0]} -> {head[1]}"))
    if a.tail != b.tail:
        out.append(Finding("skeleton.tail", detail=f"{len(a.tail)} -> {len(b.tail)} bytes"))
    return out


# model ---------------------------------------------------------------------------------------


@dataclass
class _Group:
    """One group's vertices as arrays, and its triangles."""

    positions: Floats
    step: Floats
    normals: Floats
    uvs: Floats
    colors: Floats
    weights: list[dict[int, float]]
    triangles: list[tuple[int, int, int]]
    layout: dict[str, object]

    @classmethod
    def read(cls, model: pmo.Pmo, g: int) -> _Group:
        group, mesh = model.groups()[g], model.mesh_of(g)
        vertices = group.block.vertices
        vt = vertices.vtype
        n = len(vertices)
        positions = _rows(model.positions(g), n)
        if vt.layout.position.codes[0] == "f":
            step = np.full(3, 1e-6) * np.maximum(np.abs(positions).max(initial=1.0), 1.0)
        else:
            unit = Vertices(vt, position=[(1, 1, 1)]).positions(model.scale_of(g))[0]
            step = np.abs(np.array(unit, dtype=np.float64))
        weights: list[dict[int, float]] = []
        for vi in model.influences(g):
            w: dict[int, float] = {}
            for joint, weight in vi:
                if weight and joint >= 0:
                    w[joint] = w.get(joint, 0.0) + weight
            weights.append(w)
        prims = [c for c in group.block.commands if c.op == Op.PRIM]
        return cls(
            positions=positions,
            step=step,
            normals=_rows(vertices.normals(), n),
            uvs=_rows(vertices.uvs(), n) * (mesh.uv_scale if vertices.texture else 1.0),
            colors=_rows(vertices.colors(), n),
            weights=weights,
            triangles=model.triangles(g),
            layout={
                "vtype": f"{vt.to_word():#08x}",
                "prims": len(prims),
                "prim types": sorted({c.arg >> 16 & 7 for c in prims}),
                "indices": len(group.block.indices),
                "vertex bytes": n * vt.stride,
                "commands": len(group.block.commands),
                "uv scale": mesh.uv_scale,
                "palette": len(group.bones),
            },
        )


def _rows(rows: Sequence[Sequence[float]], n: int) -> Floats:
    """`n` rows as an array, `(n, 0)` for an attribute the vertices lack."""
    a = np.array(rows, dtype=np.float64)
    return a.reshape(n, -1) if a.size else np.zeros((n, 0))


def _model(old: bytes, new: bytes) -> list[Finding]:
    a, b = pmo.Pmo.from_bytes(old), pmo.Pmo.from_bytes(new)
    out = []
    ga, gb = a.groups(), b.groups()
    if len(ga) != len(gb):
        out.append(
            Finding("pmo.groups", count=abs(len(ga) - len(gb)), detail=f"{len(ga)} -> {len(gb)}")
        )
    for g in range(min(len(ga), len(gb))):
        out += _group(a, b, g)
    layout = []
    for name, u, v in (
        ("bytes", len(old), len(new)),
        ("scale", a.scale, b.scale),
        ("clip", a.clip, b.clip),
        ("meshes", len(a.meshes), len(b.meshes)),
        ("materials", len(a.materials), len(b.materials)),
        ("canonical", _canonical(a, old), _canonical(b, new)),
    ):
        if u != v:
            layout.append(f"{name} {u} -> {v}")
    if layout:
        out.append(Finding("pmo.layout", detail=", ".join(layout), layout=True))
    return out


def _canonical(model: pmo.Pmo, data: bytes) -> bool:
    """Laid out as mhp-formats writes it: lists and buffers 16-aligned."""
    return model.to_bytes() == data


def _group(a: pmo.Pmo, b: pmo.Pmo, g: int) -> list[Finding]:
    x, y = _Group.read(a, g), _Group.read(b, g)
    out = []
    where = (g,)
    ma, mb = a.material(g), b.material(g)
    if _material(ma) != _material(mb):
        out.append(Finding("pmo.material", where, detail=f"{_material(ma)} -> {_material(mb)}"))
    ta, tb = (None if m is None else m.texture for m in (ma, mb))
    if ta != tb:
        out.append(Finding("pmo.texture", where, detail=f"{ta} -> {tb}"))
    mesh_a, mesh_b = a.mesh_of(g), b.mesh_of(g)
    if (mesh_a.lighting, mesh_a.blend) != (mesh_b.lighting, mesh_b.blend):
        detail = f"lighting/blend {mesh_a.lighting:#x}/{mesh_a.blend:#x} -> "
        out.append(
            Finding("pmo.mesh", where, detail=detail + f"{mesh_b.lighting:#x}/{mesh_b.blend:#x}")
        )

    tol = STEPS * np.maximum(x.step, y.step)
    point, pairs = _match(x, y, tol)
    na, nb = len(x.positions), len(y.positions)
    paired = len(pairs)
    if na != nb or paired != na:
        detail = f"{na} -> {nb}, {na - paired} old and {nb - paired} new without a match"
        out.append(Finding("pmo.vertices", where, max(na, nb) - paired, detail))
    if pairs:
        i = np.array([p for p, _ in pairs])
        j = np.array([q for _, q in pairs])
        out += _positions(x, y, i, j, where)
        out += _vectors("pmo.normals", x.normals, y.normals, i, j, where)
        out += _vectors("pmo.uvs", x.uvs, y.uvs, i, j, where)
        out += _vectors("pmo.colors", x.colors, y.colors, i, j, where)
        out += _weights(x, y, pairs, where)
    out += _triangles(x, y, point, where)
    if x.layout != y.layout:
        changed = [
            f"{k} {x.layout[k]} -> {y.layout[k]}" for k in x.layout if x.layout[k] != y.layout[k]
        ]
        out.append(Finding("pmo.layout", where, detail=", ".join(changed), layout=True))
    return out


def _material(m: pmo.Material | None) -> str:
    return "none" if m is None else f"{m.color:08x}/{m.shadow:08x}"


def _match(x: _Group, y: _Group, tol: Floats) -> tuple[NDArray[np.intp], list[tuple[int, int]]]:
    """The point of every vertex (old ones first, then new ones), and old-new vertex pairs."""
    na = len(x.positions)
    point = _points(np.concatenate([x.positions, y.positions]), tol)
    candidates = []
    for p in range(na):
        for q in np.flatnonzero(point[na:] == point[p]):
            candidates.append((_distance(x, y, p, int(q)), abs(p - int(q)), p, int(q)))
    pairs = _greedy(candidates)
    # a vertex that moved further keeps its partner when everything else about it agrees
    left_a = set(range(na)) - {p for p, _ in pairs}
    left_b = set(range(len(y.positions))) - {q for _, q in pairs}
    for p in sorted(left_a & left_b):
        if _distance(x, y, p, p) == 0.0:
            pairs.append((p, p))
            point[point == point[na + p]] = point[p]
    return point, sorted(pairs)


def _points(positions: Floats, tol: Floats) -> NDArray[np.intp]:
    """Each position's point: positions within `tol` per axis, chained, share one, labelled by
    its lowest member. A sweep along x, so a large group does not need a square matrix."""
    parent = list(range(len(positions)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    order = np.argsort(positions[:, 0], kind="stable")
    xs = positions[order, 0]
    ends = np.searchsorted(xs, xs + tol[0], side="right")
    for k, i in enumerate(order):
        rest = order[k + 1 : ends[k]]
        close = rest[(np.abs(positions[rest, 1:] - positions[i, 1:]) <= tol[1:]).all(axis=1)]
        for j in close:
            a, b = root(int(i)), root(int(j))
            parent[max(a, b)] = min(a, b)
    return np.array([root(i) for i in range(len(positions))], dtype=np.intp)


def _distance(x: _Group, y: _Group, p: int, q: int) -> float:
    d = float(np.abs(x.uvs[p] - y.uvs[q]).sum()) if x.uvs.size and y.uvs.size else 0.0
    if x.normals.size and y.normals.size:
        d += float(np.abs(x.normals[p] - y.normals[q]).sum())
    wa, wb = x.weights[p], y.weights[q]
    d += sum(abs(wa.get(k, 0.0) - wb.get(k, 0.0)) for k in wa.keys() | wb.keys())
    if x.colors.size and y.colors.size:
        d += float(np.abs(x.colors[p] - y.colors[q]).sum()) / 255
    return d


def _greedy(candidates: list[tuple[float, int, int, int]]) -> list[tuple[int, int]]:
    used_a: set[int] = set()
    used_b: set[int] = set()
    pairs = []
    for _, _, p, q in sorted(candidates):
        if p not in used_a and q not in used_b:
            used_a.add(p)
            used_b.add(q)
            pairs.append((p, q))
    return pairs


def _positions(
    x: _Group, y: _Group, i: NDArray[np.intp], j: NDArray[np.intp], where: tuple[int, ...]
) -> list[Finding]:
    delta = np.abs(x.positions[i] - y.positions[j])
    moved = int((delta > 0).any(axis=1).sum())
    if not moved:
        return []
    norm = np.linalg.norm(x.positions[i], axis=1)
    rel = np.linalg.norm(delta, axis=1) / np.where(norm > 0, norm, 1.0)
    steps = float((delta / np.maximum(x.step, y.step)).max())
    detail = (
        f"{moved} of {len(i)} vertices, max {delta.max():.4g} ({steps:.2f} steps), "
        f"relative {rel.max():.3g}"
    )
    return [Finding("pmo.positions", where, moved, detail)]


def _vectors(
    kind: str,
    a: Floats,
    b: Floats,
    i: NDArray[np.intp],
    j: NDArray[np.intp],
    where: tuple[int, ...],
) -> list[Finding]:
    if a.shape[1] != b.shape[1]:
        return [Finding(kind, where, len(i), f"{a.shape[1]} -> {b.shape[1]} components")]
    if not a.shape[1]:
        return []
    delta = np.abs(a[i] - b[j])
    changed = int((delta > 0).any(axis=1).sum())
    if not changed:
        return []
    return [Finding(kind, where, changed, f"{changed} of {len(i)} vertices, max {delta.max():.4g}")]


def _weights(
    x: _Group, y: _Group, pairs: list[tuple[int, int]], where: tuple[int, ...]
) -> list[Finding]:
    joints = value = 0
    worst = 0.0
    for p, q in pairs:
        wa, wb = x.weights[p], y.weights[q]
        if wa.keys() != wb.keys():
            joints += 1
        elif wa != wb:
            value += 1
            worst = max(worst, max(abs(wa[k] - wb[k]) for k in wa))
    if not joints + value:
        return []
    detail = f"{joints} vertices ride other joints, {value} other weights (max {worst:.4g})"
    return [Finding("pmo.weights", where, joints + value, detail)]


def _triangles(
    x: _Group, y: _Group, point: NDArray[np.intp], where: tuple[int, ...]
) -> list[Finding]:
    na = len(x.positions)
    old = _oriented(point[list(t)] for t in x.triangles)
    new = _oriented(point[[na + v for v in t]] for t in y.triangles)
    keys = {k for k, _ in old} | {k for k, _ in new}
    missing = added = mirrored = duplicated = 0
    for key in keys:
        o = (old[key, True], old[key, False])
        n = (new[key, True], new[key, False])
        if o == n:
            continue
        if not sum(n):
            missing += sum(o)
        elif not sum(o):
            added += sum(n)
        else:
            if sum(o) != sum(n):
                duplicated += abs(sum(o) - sum(n))
            if (o[0] > 0, o[1] > 0) != (n[0] > 0, n[1] > 0):
                mirrored += min(sum(o), sum(n))
    out = []
    for kind, count in (
        ("pmo.triangles.missing", missing),
        ("pmo.triangles.added", added),
        ("pmo.triangles.mirrored", mirrored),
        ("pmo.triangles.duplicated", duplicated),
    ):
        if count:
            total = f"{sum(old.values())} -> {sum(new.values())} triangles"
            out.append(Finding(kind, where, count, f"{count}; {total}"))
    return out


def _oriented(triangles: Iterable[NDArray[np.intp]]) -> Counter[tuple[tuple[int, int, int], bool]]:
    """(corner points sorted, winding) of each non-degenerate triangle."""
    out: Counter[tuple[tuple[int, int, int], bool]] = Counter()
    for t in triangles:
        a, b, c = (int(v) for v in t)
        if a == b or b == c or a == c:
            continue
        key = tuple(sorted((a, b, c)))
        out[(key[0], key[1], key[2]), (a, b, c) in _rotations(key)] += 1
    return out


def _rotations(t: Sequence[int]) -> tuple[tuple[int, int, int], ...]:
    a, b, c = t
    return (a, b, c), (b, c, a), (c, a, b)


# animation -----------------------------------------------------------------------------------


def _animation(old: bytes, new: bytes) -> list[Finding]:
    a, b = fu.Anim.from_bytes(old), fu.Anim.from_bytes(new)
    out = []
    if len(a.streams) != len(b.streams):
        out.append(Finding("anim.streams", detail=f"{len(a.streams)} -> {len(b.streams)} streams"))
    for s, (x, y) in enumerate(zip(a.streams, b.streams, strict=False)):
        if len(x) != len(y):
            out.append(Finding("anim.streams", (s,), detail=f"{len(x)} -> {len(y)} slots"))
        both = {i for i, (u, v) in enumerate(zip(x, y, strict=False)) if u and v}
        if _sharing(x, both) != _sharing(y, both):
            out.append(Finding("anim.layout", (s,), detail="slots share other clips", layout=True))
        for slot, (u, v) in enumerate(zip_longest(x, y)):
            if u is None and v is None:
                continue
            if v is None:
                out.append(Finding("anim.clip.missing", (s, slot)))
            elif u is None:
                out.append(Finding("anim.clip.added", (s, slot)))
            else:
                out += _clip(u, v, (s, slot))
    if a.tail != b.tail:
        out.append(
            Finding("anim.layout", detail=f"tail {len(a.tail)} -> {len(b.tail)} bytes", layout=True)
        )
    return out


def _sharing(stream: Stream, slots: set[int]) -> dict[int, int]:
    """For each of `slots`, the first of them holding the same clip object."""
    first: dict[int, int] = {}
    return {i: first.setdefault(id(stream[i]), i) for i in sorted(slots)}


def _clip(a: Clip, b: Clip, where: tuple[int, int]) -> list[Finding]:
    out = []
    if len(a.tracks) != len(b.tracks):
        out.append(
            Finding(
                "anim.tracks",
                where,
                abs(len(a.tracks) - len(b.tracks)),
                f"{len(a.tracks)} -> {len(b.tracks)}",
            )
        )
    if a.loop != b.loop:
        out.append(Finding("anim.loop", where, detail=f"{a.loop} -> {b.loop}"))
    if a.loop_start != b.loop_start:
        out.append(Finding("anim.loop_start", where, detail=f"{a.loop_start} -> {b.loop_start}"))
    channels: list[int] = []
    keys: list[int] = []
    count = 0
    worst = 0
    order = False
    for t, (x, y) in enumerate(zip(a.tracks, b.tracks, strict=False)):
        ca, cb = _channels(x), _channels(y)
        if ca.keys() != cb.keys():
            channels.append(t)
        differ = False
        for bit in ca.keys() & cb.keys():
            n, delta = _keyframes(ca[bit], cb[bit])
            if n:
                count += n
                worst = max(worst, delta)
                differ = True
        if differ:
            keys.append(t)
        order |= ca == cb and [c.bit for c in x.channels] != [c.bit for c in y.channels]
    if channels:
        out.append(Finding("anim.channels", where, len(channels), f"tracks {_ranges(channels)}"))
    if keys:
        detail = f"tracks {_ranges(keys)}, max value change {worst}"
        out.append(Finding("anim.keyframes", where, count, detail))
    if order:
        out.append(Finding("anim.layout", where, detail="channel order", layout=True))
    return out


def _channels(track: Track) -> dict[int, list[Keyframe]]:
    return {c.bit: c.keyframes for c in track.channels}


def _keyframes(a: list[Keyframe], b: list[Keyframe]) -> tuple[int, int]:
    """Keyframes that differ, and the largest raw value change between matching ones."""
    n = abs(len(a) - len(b))
    worst = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            n += 1
            worst = max(worst, abs(x.value - y.value))
    return n, worst


def _ranges(values: list[int]) -> str:
    """`[1, 2, 3, 7]` as `1-3,7`."""
    runs: list[list[int]] = []
    for v in values:
        if runs and v == runs[-1][-1] + 1:
            runs[-1].append(v)
        else:
            runs.append([v])
    return ",".join(str(r[0]) if len(r) == 1 else f"{r[0]}-{r[-1]}" for r in runs)
