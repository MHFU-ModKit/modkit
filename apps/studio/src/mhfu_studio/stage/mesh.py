# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The mesh half of an edit list: a stage PMO with the list's mesh ops applied, resident-safe.

The engine compiles the visible mesh when an area loads but reads the group table while it
draws, so bytes for a resident PAC keep their length and move no block: ops only rewrite
vertices, indices and materials, and never add a GE command (a new PRIM does not draw).
Growing a group is a file edit (`rebuild.grow`).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from mhp_formats.pmo import Block, Pmo
from mhp_formats.psp.vtype import quantize

from mhfu_studio.shell.findings import Finding, Level

from . import ops as O
from .file import MESHES, StageFile
from .obj import ObjMesh, read_obj

Vec3 = tuple[float, float, float]


@dataclass
class MeshEdit:
    """`data` is entry `sub` of the stage with the ops applied, laid out over the shipped one."""

    stage: int
    sub: int
    original: bytes
    data: bytes
    applied: int = 0
    """Ops that acted on this PMO."""
    moved: list[int] = field(default_factory=list)
    """Groups whose block had to move: writing these bytes into a resident PAC corrupts it."""
    log: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def safe(self) -> bool:
        """Same length and no block moved, so the group table is the shipped one."""
        return len(self.data) == len(self.original) and not self.moved

    def runs(self, gap: int = 0) -> list[tuple[int, int]]:
        return diff_runs(self.data, self.original, gap)


def diff_runs(new: bytes, old: bytes, gap: int = 0) -> list[tuple[int, int]]:
    """(offset, length) where `new` differs from `old`, joining runs at most `gap` apart: a
    debugger write costs a round trip, so a few long writes beat many short ones."""
    if len(new) != len(old):
        raise ValueError(f"{len(new)} bytes against {len(old)}")
    a = np.frombuffer(new, np.uint8) != np.frombuffer(old, np.uint8)
    edges = np.flatnonzero(np.diff(np.concatenate(([0], a.view(np.int8), [0]))))
    out: list[tuple[int, int]] = []
    for lo, hi in zip(edges[::2].tolist(), edges[1::2].tolist(), strict=True):
        if out and lo - (out[-1][0] + out[-1][1]) <= gap:
            out[-1] = (out[-1][0], hi - out[-1][0])
        else:
            out.append((lo, hi - lo))
    return out


def transform_matrix(op: O.Op) -> np.ndarray:
    """The 4x4 of a `transform` op: its `matrix` (row-major), else
    T(by) T(pivot) R S T(-pivot) with R the rotations about x, then y, then z, in degrees."""
    if "matrix" in op:
        return np.array(op["matrix"], np.float64).reshape(4, 4)
    p = np.asarray(op.get("pivot", (0, 0, 0)), np.float64)
    rx, ry, rz = (math.radians(v) for v in op.get("rotate", (0, 0, 0)))
    cx, sx, cy, sy, cz, sz = (f(a) for a in (rx, ry, rz) for f in (math.cos, math.sin))
    r_x = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    r_y = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    r_z = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    a = r_z @ r_y @ r_x @ np.diag(np.asarray(op.get("scale", (1, 1, 1)), np.float64))
    m = np.eye(4)
    m[:3, :3] = a
    m[:3, 3] = np.asarray(op.get("by", (0, 0, 0)), np.float64) + p - a @ p
    return m


def select(op: O.Op, positions: Sequence[Vec3]) -> list[int]:
    """The vertices an op's selector names: explicit ids, or those inside its sphere or box."""
    if "vertices" in op:
        return [i for i in op["vertices"] if 0 <= i < len(positions)]
    if "sphere" in op:
        cx, cy, cz, r = op["sphere"]
        return [
            i
            for i, (x, y, z) in enumerate(positions)
            if (x - cx) ** 2 + (y - cy) ** 2 + (z - cz) ** 2 <= r * r
        ]
    if "box" in op:
        x0, y0, z0, x1, y1, z1 = op["box"]
        return [
            i
            for i, (x, y, z) in enumerate(positions)
            if x0 <= x <= x1 and y0 <= y <= y1 and z0 <= z <= z1
        ]
    return []


def edits(stage: StageFile, ops: Sequence[O.Op], base_dir: Path) -> dict[int, MeshEdit]:
    """An edit of each PMO the list touches."""
    return {
        sub: edit(stage, sub, ops, base_dir)
        for sub in MESHES
        if any(O.sub_of(op) == sub for op in ops)
    }


def edit(stage: StageFile, sub: int, ops: Sequence[O.Op], base_dir: Path) -> MeshEdit:
    """Apply the list's ops on PMO `sub`, in order. An op a writer refuses is skipped with an
    error finding; the rest still apply."""
    original = stage.entry(sub)
    pmo = stage.pmo(sub)
    out = MeshEdit(stage.number, sub, original, original)
    if pmo is None:
        out.findings.append(
            Finding("error", "no-pmo", f"sub {sub} holds no PMO", f"{stage.label} sub {sub}")
        )
        return out
    run = _Run(stage, pmo, out, base_dir)
    for i, op in enumerate(ops):
        if O.sub_of(op) == sub:
            run.apply(i, op)
    out.data, out.moved = pmo.to_bytes_inplace(original)
    if run.shapes != run.shape():
        out.moved = sorted(set(out.moved) | run.reshaped())
    if out.moved:
        out.findings.append(
            Finding(
                "error",
                "not-resident-safe",
                f"groups {out.moved} would move: the bytes cannot go into a running game",
                f"{stage.label} sub {sub}",
            )
        )
    return out


class _Run:
    """One pass of a list over one PMO; positions compose from op to op as floats."""

    def __init__(self, stage: StageFile, pmo: Pmo, out: MeshEdit, base_dir: Path) -> None:
        self.stage, self.pmo, self.out, self.base_dir = stage, pmo, out, base_dir
        self.groups = pmo.groups()
        self.current: dict[int, list[Vec3]] = {}
        self.shapes = self.shape()
        self.i = 0

    def shape(self) -> dict[int, tuple[int, int]]:
        """Per block, what places its buffers: the command and vertex counts."""
        return {id(g.block): (len(g.block.commands), len(g.block.vertices)) for g in self.groups}

    def reshaped(self) -> set[int]:
        now = self.shape()
        return {
            k for k, g in enumerate(self.groups) if now[id(g.block)] != self.shapes[id(g.block)]
        }

    def apply(self, i: int, op: O.Op) -> None:
        kind, g = op["op"], op["group"]
        self.i = i
        if why := O.malformed(op):
            self._say("error", "refused", f"{kind}: {why}")
            return
        if not 0 <= g < len(self.groups):
            self._say(
                "error", "refused", f"sub {self.out.sub} has no group {g} ({len(self.groups)})"
            )
            return
        block = self.groups[g].block
        if block.vertices.vtype.through or block.vertices.vtype.morph_count != 1:
            self._say("error", "refused", f"group {g} draws screen-space or morphing vertices")
            return
        try:
            line = getattr(self, f"_{kind}")(op, g, block)
        except (ValueError, OSError) as e:
            self._say("error", "refused", f"{kind} g{g}: {e}")
            return
        self.out.applied += 1
        self.out.log.append(f"{O.place(self.stage.label, i)}: {line}")

    def _say(self, level: Level, code: str, message: str) -> None:
        where, target = O.place(self.stage.label, self.i), (self.stage.number, self.i)
        self.out.findings.append(Finding(level, code, message, where, target))

    @property
    def scale(self) -> Vec3:
        return self.pmo.scale

    def positions(self, block: Block) -> list[Vec3]:
        if id(block) not in self.current:
            self.current[id(block)] = [
                (x, y, z) for x, y, z in block.vertices.positions(self.scale)
            ]
        return self.current[id(block)]

    def place(self, block: Block, moves: dict[int, Vec3]) -> None:
        """Write positions, keeping the floats for the next op."""
        rows = quantize(list(moves.values()), self.scale, block.vertices.vtype.layout.position)
        current = self.positions(block)
        for (i, xyz), row in zip(moves.items(), rows, strict=True):
            block.vertices.position[i] = row
            current[i] = xyz

    def _counts(self, block: Block, chosen: set[int]) -> str:
        """How many faces the selection holds whole, and how many it cuts (those stretch)."""
        faces = [t for t in block.triangles() if len(set(t)) == 3]
        inside = [sum(v in chosen for v in t) for t in faces]
        whole, cut = inside.count(3), sum(0 < n < 3 for n in inside)
        return f"{whole} faces whole, {cut} straddling" + ("  (these stretch)" if cut else "")

    def _select(self, op: O.Op, g: int, positions: Sequence[Vec3]) -> list[int]:
        chosen = select(op, positions)
        past = len(op["vertices"]) - len(chosen) if "vertices" in op else 0
        if past:
            n = len(positions)
            self._say("error", "vertex-range", f"{past} vertex ids past g{g}'s {n} vertices")
        return chosen

    def _transform(self, op: O.Op, g: int, block: Block) -> str:
        positions = self.positions(block)
        chosen = self._select(op, g, positions)
        m = transform_matrix(op)
        pts = np.asarray([positions[i] for i in chosen], np.float64).reshape(-1, 3)
        moved = pts @ m[:3, :3].T + m[:3, 3]
        self.place(
            block, {i: (x, y, z) for i, (x, y, z) in zip(chosen, moved.tolist(), strict=True)}
        )
        return f"transform g{g} {len(chosen)} vertices, {self._counts(block, set(chosen))}"

    def _move(self, op: O.Op, g: int, block: Block) -> str:
        positions = self.positions(block)
        chosen = self._select(op, g, positions)
        dx, dy, dz = op["by"]
        self.place(
            block,
            {i: (positions[i][0] + dx, positions[i][1] + dy, positions[i][2] + dz) for i in chosen},
        )
        return f"move g{g} {len(chosen)} vertices by {op['by']}, {self._counts(block, set(chosen))}"

    def _obj(self, op: O.Op) -> ObjMesh:
        mesh = read_obj(self.base_dir / op["obj"])
        if not mesh.triangles:
            raise ValueError(f"{op['obj']} has no faces")
        return mesh

    def _pack_into(
        self,
        op: O.Op,
        g: int,
        block: Block,
        prims: Iterable[int] | None,
        *,
        uv: str | None,
        reindex: bool,
        collapse: bool,
    ) -> str:
        """`uv` None takes the OBJ's UVs when it has them, else keeps the slots'."""
        mesh = self._obj(op)
        uvs = self._uvs(op, uv or ("obj" if mesh.uvs is not None else "keep"), mesh, block)
        colour = op.get("colour")
        color = (*colour, 255)[:4] if colour is not None else None
        chosen = sorted(set(prims)) if prims is not None else None
        r = block.pack(
            mesh.positions,
            mesh.triangles,
            self.scale,
            chosen,
            reindex=reindex,
            collapse=collapse,
            uvs=uvs,
            color=color,
        )
        self.current.pop(id(block), None)
        if r.left:
            n = len(mesh.triangles)
            self._say("error", "did-not-fit", f"{r.left} of {n} triangles did not fit g{g}")
        skipped = f", {r.skipped} primitives skipped (shared vertices)" if r.skipped else ""
        return (
            f"{op['op']} g{g} {r.triangles} triangles into {r.used} of {r.prims} primitives, "
            f"{r.left} left over{skipped}"
        )

    def _uvs(
        self, op: O.Op, uv: str, mesh: ObjMesh, block: Block
    ) -> list[tuple[float, float]] | None:
        if block.vertices.vtype.layout.texture is None or uv == "keep":
            return None
        if uv == "obj":
            if mesh.uvs is None:
                self._say("warning", "no-uv", f"{op['obj']} has no UVs; the slots keep theirs")
            return mesh.uvs
        # planar: the shape's two widest axes onto the box of texels the group already uses
        lo = [min(p[k] for p in mesh.positions) for k in range(3)]
        hi = [max(p[k] for p in mesh.positions) for k in range(3)]
        ext = [hi[k] - lo[k] for k in range(3)]
        a, b = sorted(range(3), key=lambda k: -ext[k])[:2]
        have = block.vertices.uvs()
        if not have:
            return [
                ((p[a] - lo[a]) / (ext[a] or 1.0), (p[b] - lo[b]) / (ext[b] or 1.0))
                for p in mesh.positions
            ]
        u0, u1 = min(u for u, _ in have), max(u for u, _ in have)
        v0, v1 = min(v for _, v in have), max(v for _, v in have)
        return [
            (
                u0 + (p[a] - lo[a]) / (ext[a] or 1.0) * (u1 - u0),
                v0 + (p[b] - lo[b]) / (ext[b] or 1.0) * (v1 - v0),
            )
            for p in mesh.positions
        ]

    def _pack(self, op: O.Op, g: int, block: Block) -> str:
        prims: Iterable[int] | None = op.get("prims")
        if prims is None and ("first" in op or "count" in op):
            n = len(block.spans())
            first = op.get("first", 0)
            count = op.get("count")
            prims = range(min(first, n), n if count is None else min(n, first + count))
        return self._pack_into(
            op,
            g,
            block,
            prims,
            uv=op.get("uv", "keep"),
            reindex=op.get("reindex", True),
            collapse=op.get("collapse", True),
        )

    def _sculpt(self, op: O.Op, g: int, block: Block) -> str:
        """Positions only, from primitive `first` on: no index or command word changes."""
        n = len(block.spans())
        return self._pack_into(
            op,
            g,
            block,
            range(min(op.get("first", 0), n), n),
            uv="keep",
            reindex=False,
            collapse=op.get("collapse", True),
        )

    def _replace(self, op: O.Op, g: int, block: Block) -> str:
        """The whole group becomes the OBJ, packed into its own primitives."""
        return self._pack_into(op, g, block, None, uv=op.get("uv"), reindex=True, collapse=True)

    def _clear(self, op: O.Op, g: int, block: Block) -> str:
        if op.get("prims") is not None:
            n = block.clear_prims(op["prims"])
            return f"clear g{g} {n} of {len(block.spans())} primitives collapsed"
        block.clear(keep_layout=True)
        return f"clear g{g} draws nothing"

    def _material(self, op: O.Op, g: int, block: Block) -> str:
        if "mat" in op:
            if not op["mat"] < len(self.pmo.materials):
                raise ValueError(f"material {op['mat']} is outside the {len(self.pmo.materials)}")
            mat = self.pmo.materials[op["mat"]]
        else:
            found = self.pmo.material(g)
            if found is None:
                raise ValueError("its material cannot be resolved; pass `mat`")
            mat = found
        if "rgba" in op:
            mat.color = int.from_bytes(bytes(op["rgba"]), "little")
        if "ambient" in op:
            mat.shadow = int.from_bytes(bytes(op["ambient"]), "little")
        if "texture" in op:
            mat.texture = op["texture"]
        index = next(k for k, m in enumerate(self.pmo.materials) if m is mat)
        return f"material g{g} -> record {index}: color {mat.color:08X} texture {mat.texture}"


def budget(stage: StageFile, sub: int) -> list[tuple[int, int, int, int, int]]:
    """Per group: (group, primitives, index slots, triangles `pack` can draw, free vertices)."""
    pmo = stage.pmo(sub)
    if pmo is None:
        return []
    return [(g, *grp.block.budget()) for g, grp in enumerate(pmo.groups())]


def remesh(stage: StageFile, sub: int, positions: dict[int, Sequence[Vec3]]) -> MeshEdit:
    """Every vertex of the named groups moved to a new position, in buffer order."""
    pmo = stage.pmo(sub)
    original = stage.entry(sub)
    if pmo is None:
        raise ValueError(f"{stage.label} sub {sub} holds no PMO")
    out = MeshEdit(stage.number, sub, original, original)
    for g, rows in positions.items():
        v = pmo.groups()[g].block.vertices
        if len(rows) != len(v):
            raise ValueError(f"group {g} has {len(v)} vertices, not {len(rows)}")
        v.position = quantize(rows, pmo.scale, v.vtype.layout.position)
        out.applied += 1
    out.data, out.moved = pmo.to_bytes_inplace(original)
    return out
