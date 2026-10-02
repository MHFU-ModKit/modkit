# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""One stage as a render-agnostic scene: decode only, the writers live in `stage/`.

Vertex colour is the whole of the lighting (stages carry no normals). Objects below the
vertex group are connected components welded by position: surface patches, not authored
objects. Mesh and collision share one world frame; the hunter stands 140 above his floor.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt
from mhfu import stage as S
from mhfu.files import Extracted
from mhp_formats.fu.stage import Hits, Tri
from mhp_formats.pmo import Block, Budget, Material, Pmo
from mhp_formats.psp.ge import Op, triangles
from mhp_formats.tmh import Tmh

from mhfu_studio.stage.collision import Plan
from mhfu_studio.stage.file import MESHES, TEXTURES, StageFile
from mhfu_studio.stage.textures import UNTEXTURED

from .atlas import stage_name, stage_title

Array = npt.NDArray[Any]
Point = Sequence[float] | Array
"""A position: three numbers, or an array of them."""
Vec3 = tuple[float, float, float]
Key = tuple[int, int]
"""(sub, group)."""

STANDING_HEIGHT = 140.0
"""The hunter's position sits this far above the floor he stands on."""
CLIMB_MATERIALS = (9, 10)
CLIMB_MAX_UP = 0.34
"""A triangle with |n.y| under this is near-vertical: a wall, climbable with a CLIMB material."""
BACKDROP_MARGIN = 5000.0
"""A group reaching this far past the floor's box is backdrop: dimmed, never framed."""
SURF_SINK = 0x20
SURF_WADE = 0x80
TURN = 0x10000
"""An exit's yaw: a full turn."""

FLOOR, WALL, CLIMB, SINK, WADE = "floor", "wall", "climb", "sink", "wade"
CLASSES = (FLOOR, WALL, CLIMB, SINK, WADE)


class SceneError(RuntimeError):
    """The stage cannot be made into a scene at all."""


@dataclass
class TextureImage:
    """One slot of the bank; `rgba` is (h, w, 4) uint8, row 0 the top."""

    index: int
    width: int
    height: int
    rgba: Array
    mode: int
    colours: int | None
    """Palette entries; None for a direct-colour image."""


@dataclass
class MeshGroup:
    """One vertex group of the terrain (sub 0) or the props (sub 2): the unit that carries a
    material and the unit a `pack` budget is counted in."""

    sub: int
    vg_rec: int
    """The group index every op names."""
    block: Block
    material: int | None
    """Index into the PMO's materials; None when the remap points past the table."""
    texture: int | None
    """Bank slot; None for untextured (`untextured`) or unresolved."""
    untextured: bool
    positions: Array
    """(v, 3) float32, world space."""
    triangles: Array
    """(f, 3) int32, degenerate ones left out."""
    uvs: Array | None
    colours: Array | None
    """(v, 4) uint8: the lighting."""
    backdrop: bool
    components: Array = field(default_factory=lambda: np.zeros(0, np.int32))
    """Welded component per vertex."""
    n_components: int = 0
    prims: list[tuple[int, int]] = field(default_factory=list)
    """(type, index count) per primitive."""
    prim_of_face: Array = field(default_factory=lambda: np.zeros(0, np.int32))

    @property
    def n_vertices(self) -> int:
        return len(self.positions)

    @property
    def n_faces(self) -> int:
        return len(self.triangles)

    @property
    def key(self) -> Key:
        return (self.sub, self.vg_rec)

    @property
    def label(self) -> str:
        return f"sub{self.sub}.g{self.vg_rec}"

    @property
    def budget(self) -> Budget:
        """What `pack` can fit into every primitive of the group."""
        return self.block.budget() if self.prims else Budget(0, 0, 0, 0)

    def bounds(self) -> tuple[Array, Array]:
        if not len(self.positions):
            z = np.zeros(3, np.float32)
            return z, z
        return self.positions.min(0), self.positions.max(0)

    def component_vertices(self, cid: int) -> Array:
        return np.nonzero(self.components == cid)[0]

    def component_faces(self, cid: int) -> Array:
        if not len(self.triangles):
            return np.zeros(0, np.int64)
        return np.nonzero((self.components[self.triangles] == cid).all(1))[0]

    def prims_of_vertices(self, ids: Array) -> Array:
        """Primitives whose every drawn face lies inside `ids`."""
        if not len(self.triangles) or not len(ids):
            return np.zeros(0, np.int64)
        inside = np.asarray(np.isin(self.triangles, ids).all(1))
        pf = self.prim_of_face
        out = [int(p) for p in np.unique(pf[inside]) if inside[pf == p].all()]
        return np.array(out, np.int64)

    def prim_faces(self, prims: Iterable[int]) -> Array:
        """The faces those primitives draw now."""
        if not len(self.prim_of_face):
            return np.zeros(0, np.int64)
        return np.nonzero(np.isin(self.prim_of_face, np.asarray(list(prims), np.int64)))[0]

    def free_prims(self) -> Array:
        """Primitives that draw nothing (cleared, or degenerate as shipped)."""
        drawn = np.unique(self.prim_of_face)
        big = np.array([p for p, (_, c) in enumerate(self.prims) if c >= 3], np.int64)
        return np.setdiff1d(big, drawn)

    def prim_capacity(self, prims: Iterable[int]) -> int:
        """Triangles a `pack` can put into those primitives."""
        chosen = [int(p) for p in prims if 0 <= int(p) < len(self.prims)]
        return self.block.budget(chosen).triangles if chosen else 0


@dataclass
class CollisionChunk:
    """One `HITS` chunk as arrays; chunk 1 is the walkable floor, 0 the walls."""

    index: int
    verts: Array
    """(t, 3, 3) float32."""
    normals: Array
    plane_d: Array
    flags: Array
    """(t,) uint32: the whole word."""
    surface_id: Array
    material: Array
    exclude: Array
    klass: Array
    """(t,) str: one of `CLASSES`."""
    grid: tuple[int, int]
    cell: tuple[int, int]
    origin: tuple[int, int]
    cells: list[list[int]]
    alive: Array
    """False for a triangle an edit deleted."""
    n_shipped: int
    """Triangles in the file; later ones are those the edit list added, in order."""

    @property
    def n_triangles(self) -> int:
        return len(self.verts)

    @property
    def n_alive(self) -> int:
        return int(self.alive.sum())

    def climbable(self) -> Array:
        return np.nonzero((self.klass == CLIMB) & self.alive)[0]

    def cells_of(self, tri: int) -> list[int]:
        return [i for i, members in enumerate(self.cells) if tri in members]

    def vertical(self, tri: int) -> bool:
        return bool(abs(float(self.normals[tri][1])) < CLIMB_MAX_UP)


@dataclass
class ExitRecord:
    """Walk into the cylinder, land at `dest` in `target` facing `yaw` (0x4000 = 90 deg)."""

    index: int
    target: int
    target_name: str
    flag: int
    trigger: Vec3
    radius: float
    height: float
    dest: Vec3
    yaw: int

    @property
    def yaw_degrees(self) -> float:
        return self.yaw * 360.0 / TURN


@dataclass
class Sphere:
    """A positioned sphere of the overlay (the supply box's prompt is one); read at load."""

    id: int
    pos: Vec3
    radius: float


@dataclass
class MapScene:
    stage: int
    name: str
    file: StageFile
    fog_rgba: tuple[int, int, int, int]
    groups: list[MeshGroup]
    materials: dict[int, list[Material]]
    pmo_scale: dict[int, Vec3]
    """A vertex lies within +-scale: the range an edit must stay inside."""
    textures: list[TextureImage]
    collision: list[CollisionChunk]
    exits: list[ExitRecord]
    spheres: list[Sphere]
    surface_table: list[int] | None
    sub_sizes: list[int]
    notes: list[str] = field(default_factory=list)

    @property
    def terrain(self) -> list[MeshGroup]:
        return [g for g in self.groups if g.sub == 0]

    @property
    def props(self) -> list[MeshGroup]:
        return [g for g in self.groups if g.sub == 2]

    @property
    def floor(self) -> CollisionChunk | None:
        return next((c for c in self.collision if c.index == 1), None)

    @property
    def walls(self) -> CollisionChunk | None:
        return next((c for c in self.collision if c.index == 0), None)

    def group(self, sub: int, vg_rec: int) -> MeshGroup:
        for g in self.groups:
            if g.sub == sub and g.vg_rec == vg_rec:
                return g
        raise KeyError((sub, vg_rec))

    def chunk(self, index: int) -> CollisionChunk:
        for c in self.collision:
            if c.index == index:
                return c
        raise KeyError(index)

    def texture(self, slot: int | None) -> TextureImage | None:
        return next((t for t in self.textures if t.index == slot), None)

    def playable_bounds(self) -> tuple[Array, Array]:
        """The floor's box, else the near-field groups'; backdrop never counts."""
        f = self.floor
        if f is not None and f.n_triangles:
            v = f.verts.reshape(-1, 3)
            return v.min(0), v.max(0)
        near = [g for g in self.groups if not g.backdrop and g.n_vertices]
        if not near:
            return np.zeros(3, np.float32), np.zeros(3, np.float32)
        lo = np.min([g.positions.min(0) for g in near], 0)
        hi = np.max([g.positions.max(0) for g in near], 0)
        return lo, hi

    def climbable(self) -> list[tuple[int, int]]:
        """(chunk, tri) of every climbable triangle."""
        return [(c.index, int(t)) for c in self.collision for t in c.climbable()]

    def floor_height(self, x: float, z: float) -> float | None:
        """The highest walkable floor under (x, z), or None."""
        f = self.floor
        if f is None or not f.n_triangles:
            return None
        v = f.verts
        p = np.array([x, z], np.float64)
        a, b, c = v[:, 0, [0, 2]], v[:, 1, [0, 2]], v[:, 2, [0, 2]]
        d0, d1, d2 = _edge(a, b, p), _edge(b, c, p), _edge(c, a, p)
        inside = ((d0 >= 0) & (d1 >= 0) & (d2 >= 0)) | ((d0 <= 0) & (d1 <= 0) & (d2 <= 0))
        idx = np.nonzero(inside & f.alive)[0]
        n, d = f.normals[idx].astype(np.float64), f.plane_d[idx].astype(np.float64)
        ok = np.abs(n[:, 1]) > 1e-6
        if not ok.any():
            return None
        y = -(n[ok, 0] * x + n[ok, 2] * z + d[ok]) / n[ok, 1]
        return float(y.max())

    # re-derived from rebuilt bytes by the edit session

    def replace_sub(self, sub: int, blob: bytes) -> list[MeshGroup]:
        """Re-decode one PMO and swap its groups in place (same order, same keys)."""
        groups, mats, scale = _decode_pmo(sub, blob, self._playable(), self.notes)
        self.materials[sub], self.pmo_scale[sub] = mats, scale
        by_key = {g.key: g for g in groups}
        self.groups = [by_key.get(g.key, g) if g.sub == sub else g for g in self.groups]
        self._resolve_textures(groups)
        return groups

    def replace_textures(self, blob: bytes) -> None:
        self.textures = _decode_textures(blob, self.notes)

    def replace_collision(self, plan: Plan | None) -> None:
        """The file's collision, or the file's with a plan applied."""
        if plan is None:
            self.collision = _decode_collision(self.file.chunks, self.surface_table)
            return
        shipped = [len(c.tris) for c in self.file.chunks]
        self.collision = _decode_collision(
            plan.collision().chunks, self.surface_table, shipped, set(plan.deleted)
        )

    def _playable(self) -> tuple[Array, Array] | None:
        f = self.floor
        if f is None or not f.n_shipped:
            return None
        v = f.verts[: f.n_shipped].reshape(-1, 3)
        return v.min(0), v.max(0)

    def _resolve_textures(self, groups: Iterable[MeshGroup]) -> None:
        slots = {t.index for t in self.textures}
        for g in groups:
            if g.texture is not None and g.texture not in slots:
                self.notes.append(f"{g.label} names texture slot {g.texture}, not in the bank")
                g.texture = None

    def summary(self) -> str:
        lines = [
            stage_title(self.stage),
            f"  fog {self.fog_rgba}   subs {self.sub_sizes}",
        ]
        for sub, label in ((0, "terrain"), (2, "props")):
            gs = [g for g in self.groups if g.sub == sub]
            lines.append(
                f"  {label}: {len(gs)} groups, {sum(g.n_vertices for g in gs)} verts,"
                f" {sum(g.n_faces for g in gs)} faces,"
                f" pack budget {sum(g.budget.triangles for g in gs)} triangles"
            )
        for c in self.collision:
            kinds = " ".join(
                f"{k}={int((c.klass == k).sum())}" for k in CLASSES if (c.klass == k).any()
            )
            lines.append(f"  collision chunk {c.index}: {c.n_triangles} triangles  {kinds}")
        lines.append(f"  textures: {len(self.textures)}")
        for e in self.exits:
            lines.append(f"  exit {e.index} -> {stage_title(e.target)}")
        lines += [f"  note: {n}" for n in self.notes]
        return "\n".join(lines)


def _edge(a: Array, b: Array, p: Array) -> Array:
    out: Array = (b[:, 0] - a[:, 0]) * (p[1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (p[0] - a[:, 0])
    return out


def faces(block: Block) -> tuple[Array, Array, list[tuple[int, int]]]:
    """The triangles a block draws (degenerate ones left out), the primitive each comes from,
    and (type, index count) per primitive."""
    vt = block.vertices.vtype
    face_order = first = at = 0
    tris: list[tuple[int, int, int]] = []
    owner: list[int] = []
    prims: list[tuple[int, int]] = []
    for c in block.commands:
        if c.op == Op.FFACE:
            face_order = c.arg & 1
        elif c.op == Op.VADDR:
            first = c.arg
        elif c.op == Op.PRIM:
            prim, count = c.arg >> 16 & 7, c.arg & 0xFFFF
            p = len(prims)
            prims.append((prim, count))
            if vt.index:
                idx: Sequence[int] = block.indices[at : at + count]
                at += count
            else:
                idx = range(count)
            for a, b, d in triangles(prim, idx, face_order):
                if a != b and b != d and a != d:
                    tris.append((a + first, b + first, d + first))
                    owner.append(p)
            if not vt.index:
                first += count
    if not tris:
        return np.zeros((0, 3), np.int32), np.zeros(0, np.int32), prims
    return np.array(tris, np.int32), np.array(owner, np.int32), prims


def weld_components(positions: Array, tris: Array) -> tuple[Array, int]:
    """Connected components after welding vertices that share a position; a vertex no face
    draws is its own. Returns (component per vertex, count)."""
    n = len(positions)
    if n == 0:
        return np.zeros(0, np.int32), 0
    _, weld = np.unique(np.round(positions, 3), axis=0, return_inverse=True)
    weld = weld.reshape(-1).astype(np.int64)
    parent = np.arange(int(weld.max()) + 1, dtype=np.int64)

    def find(a: Array) -> Array:
        while True:  # path halving, vectorised until stable
            p = parent[a]
            gp = parent[p]
            if np.array_equal(p, gp):
                return p
            parent[a] = gp
            a = gp

    if len(tris):
        edges = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]], 0)
        edges = weld[edges]
        edges = edges[edges[:, 0] != edges[:, 1]]
        while len(edges):
            ra, rb = find(edges[:, 0]), find(edges[:, 1])
            lo, hi = np.minimum(ra, rb), np.maximum(ra, rb)
            changed = lo != hi
            if not changed.any():
                break
            np.minimum.at(parent, hi[changed], lo[changed])
    _, comp = np.unique(find(weld), return_inverse=True)
    comp = comp.reshape(-1).astype(np.int32)
    return comp, int(comp.max()) + 1


def _decode_pmo(
    sub: int, blob: bytes, playable: tuple[Array, Array] | None, notes: list[str]
) -> tuple[list[MeshGroup], list[Material], Vec3]:
    if not Pmo.sniff(blob):
        return [], [], (1.0, 1.0, 1.0)
    pmo = Pmo.from_bytes(blob)
    out, seen = [], set()
    for g, group in enumerate(pmo.groups()):
        if id(group.block) in seen:
            notes.append(f"sub{sub} g{g} shares its GE block with an earlier group")
            continue
        seen.add(id(group.block))
        out.append(_decode_group(sub, g, pmo, playable, notes))
    return out, pmo.materials, pmo.scale


def _decode_group(
    sub: int, g: int, pmo: Pmo, playable: tuple[Array, Array] | None, notes: list[str]
) -> MeshGroup:
    block = pmo.groups()[g].block
    v = block.vertices
    vt = v.vtype
    n = len(v)
    step = vt.morph_count
    if n and not vt.through:
        pos = np.asarray(v.positions(pmo.scale), np.float32).reshape(-1, 3)[::step]
    else:
        pos = np.zeros((n, 3), np.float32)
        if n:
            notes.append(f"sub{sub} g{g}: screen-space positions, not decoded")
    uvs = np.asarray(v.uvs(), np.float32).reshape(-1, 2)[::step] if n and vt.texture else None
    cols = np.asarray(v.colors(), np.uint8).reshape(-1, 4)[::step] if n and v.color else None
    tris, owner, prims = faces(block)
    mat = pmo.material(g)
    material = next((k for k, m in enumerate(pmo.materials) if m is mat), None)
    if material is None:
        notes.append(f"sub{sub} g{g}: its material remap points past the table")
    texture = mat.texture if mat is not None else None
    untextured = texture == UNTEXTURED
    backdrop = False
    if n and playable is not None:
        lo, hi = playable
        gl, gh = pos.min(0), pos.max(0)
        m = BACKDROP_MARGIN
        backdrop = bool(
            gl[0] < lo[0] - m or gh[0] > hi[0] + m or gl[2] < lo[2] - m or gh[2] > hi[2] + m
        )
    comp, ncomp = weld_components(pos, tris)
    return MeshGroup(
        sub,
        g,
        block,
        material,
        None if untextured else texture,
        untextured,
        pos,
        tris,
        uvs,
        cols,
        backdrop,
        comp,
        ncomp,
        prims,
        owner,
    )


def _decode_textures(blob: bytes, notes: list[str]) -> list[TextureImage]:
    if not Tmh.sniff(blob):
        return []
    out = []
    for k, img in enumerate(Tmh.from_bytes(blob).images):
        try:
            rgba = img.decode()
        except (NotImplementedError, ValueError) as e:
            notes.append(f"texture slot {k}: {e}")
            continue
        pixels = np.frombuffer(rgba, np.uint8).reshape(img.height, img.width, 4).copy()
        colours = img.clut.entries if img.clut else None
        out.append(TextureImage(k, img.width, img.height, pixels, img.mode, colours))
    return out


def classify(
    chunk: int, normals: Array, flags: Array, surface_table: Sequence[int] | None
) -> Array:
    """Per-triangle class. Sink and wade describe what the hunter stands in (flat surfaces),
    so they never outrank a wall; CLIMB is assigned last."""
    t = len(flags)
    sid = (flags & 0xFF).astype(np.uint8)
    mat = ((flags >> 8) & 0xFF).astype(np.uint8)
    klass = np.full(t, WALL, dtype="<U5")
    vertical = np.abs(normals[:, 1]) < CLIMB_MAX_UP if t else np.zeros(0, bool)
    if chunk == 1:
        klass[~vertical] = FLOOR
    if surface_table and t:
        table = list(surface_table)
        bits = np.array([table[s] if s < len(table) else 0 for s in sid], np.uint32)
        klass[~vertical & ((bits & SURF_WADE) != 0)] = WADE
        klass[~vertical & ((bits & SURF_SINK) != 0) & ((bits & SURF_WADE) == 0)] = SINK
    klass[vertical & np.isin(mat, CLIMB_MATERIALS)] = CLIMB
    return klass


def _decode_collision(
    chunks: Sequence[Hits],
    surface_table: Sequence[int] | None,
    shipped: Sequence[int] | None = None,
    deleted: set[tuple[int, int]] | None = None,
) -> list[CollisionChunk]:
    out = []
    for ci, hits in enumerate(chunks):
        tris: list[Tri] = hits.tris
        t = len(tris)
        verts = np.array([(x.v0, x.v1, x.v2) for x in tris], np.float32).reshape(-1, 3, 3)
        normals = np.array([x.normal for x in tris], np.float32).reshape(-1, 3)
        plane_d = np.array([x.plane_d for x in tris], np.float32)
        flags = np.array([x.flags.word for x in tris], np.uint32)
        alive = np.ones(t, bool)
        for c, k in deleted or ():
            if c == ci and k < t:
                alive[k] = False
        out.append(
            CollisionChunk(
                ci,
                verts,
                normals,
                plane_d,
                flags,
                (flags & 0xFF).astype(np.uint8),
                ((flags >> 8) & 0xFF).astype(np.uint8),
                (flags >> 16).astype(np.uint16),
                classify(ci, normals, flags, surface_table),
                hits.grid,
                hits.cell,
                hits.origin,
                [list(run) for run in hits.cells],
                alive,
                t if shipped is None else shipped[ci],
            )
        )
    return out


def build(
    file: StageFile,
    *,
    name: str | None = None,
    exits: Sequence[ExitRecord] = (),
    spheres: Sequence[Sphere] = (),
    surface_table: Sequence[int] | None = None,
    notes: Sequence[str] = (),
) -> MapScene:
    """A scene of a stage file and what its overlay says about it."""
    st = file.stage
    if st.collision is None and not st.terrain:
        raise SceneError(f"{file.label} is a placeholder ({len(file.data)} bytes)")
    env = st.environment
    fog = env.fog_rgba if env is not None else [0, 0, 0, 255]
    table = list(surface_table) if surface_table is not None else None
    collision = _decode_collision(file.chunks, table)
    floor = next((c for c in collision if c.index == 1 and c.n_triangles), None)
    playable = (
        None
        if floor is None
        else (floor.verts.reshape(-1, 3).min(0), floor.verts.reshape(-1, 3).max(0))
    )
    out: list[str] = list(notes)
    groups: list[MeshGroup] = []
    materials: dict[int, list[Material]] = {}
    scale: dict[int, Vec3] = {}
    for sub in MESHES:
        gs, mats, sc = _decode_pmo(sub, file.entry(sub), playable, out)
        groups += gs
        materials[sub], scale[sub] = mats, sc
    scene = MapScene(
        file.number,
        name or stage_name(file.number),
        file,
        (fog[0], fog[1], fog[2], fog[3]),
        groups,
        materials,
        scale,
        _decode_textures(file.entry(TEXTURES), out),
        collision,
        list(exits),
        list(spheres),
        table,
        [size for _, size in file.table],
        out,
    )
    scene._resolve_textures(groups)
    return scene


def open_stage(game: Extracted, stage: int) -> MapScene:
    """`st<NNN>.pac` and its overlay."""
    try:
        file = StageFile.read(game, stage)
        file.stage  # noqa: B018  # parse now, so a bad file fails here
    except (OSError, ValueError) as e:
        raise SceneError(f"st{stage:03d}: {e}") from None
    notes: list[str] = []
    exits: list[ExitRecord] = []
    spheres: list[Sphere] = []
    table = None
    try:
        ov = S.StageOverlay.read(game, stage)
        ids = [t.flags.surface_id for h in file.chunks for t in h.tris]
        table = ov.surface_table(count=max(ids) + 1 if ids else 1)
        exits = [
            ExitRecord(
                i,
                e.target,
                stage_name(e.target),
                e.flag,
                e.trigger,
                e.radius,
                e.height,
                e.dest,
                e.yaw,
            )
            for i, e in enumerate(ov.exits())
        ]
        spheres = [Sphere(s.id, s.position, s.radius) for s in ov.spheres()]
    except (OSError, ValueError) as e:
        notes.append(f"overlay: {e}")
    return build(file, exits=exits, spheres=spheres, surface_table=table, notes=notes)
