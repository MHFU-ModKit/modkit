# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""PMO, the model format of both games. This module reads MHFU's version `1.0`; MHP3rd's `102`
differs only where `mhp_formats.p3rd.pmo` says.

A 0x40 header, then five tables, each 16-aligned: meshes, groups, the material remap, the bone
palette and the materials. The geometry region follows: one block per group, a GE display list
and the vertex and index buffers it draws, each part aligned to `ALIGN`, the region padded to 16.
A mesh owns a run of groups and a run of the remap; a group draws with material
`materials[mesh.materials[group.material]]`, and each group's `bones` patch the running bone
palette whose slots the vertex weights index.
"""

import struct
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import ClassVar, NamedTuple, Self, cast

from construct import Array, Bytes, ConstructError, Float32l, Int8ul, Int16ul, Int32ul
from construct_typed import (
    DataclassMixin,
    DataclassStruct,
    csfield,
    csfield_const,
    csfield_default,
)

from ._base import FormatError
from .psp.color import Color, pack
from .psp.ge import Command, DisplayList, Op, Prim, origins
from .psp.strip import Stripper
from .psp.vtype import COLOR_5650, VertexType, Vertices, quantize

Vec3 = tuple[float, float, float]
Triangle = tuple[int, int, int]
Influence = tuple[int, float]
"""(skeleton bone, weight)."""

MAGIC = b"pmo\0"
_HEADER_SIZE = 0x40
_TABLE_ALIGN = 16
_INDEX = {1: "B", 2: "H", 3: "I"}


def _up(n: int, align: int) -> int:
    return -(-n // align) * align


class BoneSlot(NamedTuple):
    """A bone-palette patch: matrix `slot` now holds skeleton bone `bone`."""

    slot: int
    bone: int


@dataclass
class Material(DataclassMixin):
    color: int = csfield_default(Int32ul, default=0xFFFFFFFF)
    """RGBA, red in the low byte; MHFU does not draw a group whose material has alpha 0."""
    shadow: int = csfield_default(Int32ul, default=0x7F7F7F)
    """The ambient colour, laid out like `color`."""
    texture: int = csfield_default(Int8ul, default=0)
    """TMH image index; 0xFF draws untextured."""
    _pad: bytes = csfield_const(Bytes(3), b"\0\0\0")
    reserved: int = csfield_default(Int32ul, default=0)
    """Leftover memory on some MHFU models, zero elsewhere."""


class Budget(NamedTuple):
    prims: int
    slots: int
    """Indices the primitives draw."""
    triangles: int
    """Triangles they hold with every strip filled to its length."""
    vertices: int
    """Vertex slots no other primitive draws: the most vertices a packed mesh can have."""


class Packed(NamedTuple):
    prims: int
    used: int
    """Primitives written."""
    triangles: int
    skipped: int
    """Primitives left alone because they share a vertex (positions-only packing)."""
    left: int
    """Triangles that did not fit."""
    vertices_used: int
    vertices_free: int
    ran_out: bool
    """Packing stopped for want of a vertex slot."""


@dataclass
class Block:
    """A group's display list and the buffers it draws from.

    The layout writes the arguments of VTYPE (from `vertices`), VADDR and IADDR; decoded, a VADDR
    argument is a vertex number in `vertices` and an IADDR argument an index number in `indices`.
    """

    commands: list[Command]
    vertices: Vertices
    indices: list[int] = field(default_factory=list)
    """Empty when the vertex type has no index format."""

    @classmethod
    def build(cls, vertices: Vertices, triangles: Sequence[Triangle]) -> Self:
        """A block drawing `triangles` as strips, indexed u8 up to 256 vertices, else u16."""
        n = len(vertices)
        if n > 0x10000:
            raise ValueError(f"{n} vertices do not fit 16-bit indices")
        if any(not 0 <= i < n for tri in triangles for i in tri):
            raise ValueError(f"a triangle indexes past the {n} vertices")
        vtype = replace(vertices.vtype, index=1 if n <= 0x100 else 2)
        commands = [Command(Op.ORIGIN), Command(Op.BASE), Command(Op.IADDR), Command(Op.VADDR)]
        commands += [Command(Op.VTYPE, vtype.to_word()), Command(Op.FFACE)]
        indices: list[int] = []
        stripper = Stripper(triangles)
        while strip := stripper.strip(0xFFFF):
            commands.append(Command.prim(Prim.TRIANGLE_STRIP, len(strip)))
            indices += strip
        commands += [Command(Op.OFFSETADDR), Command(Op.RET)]
        return cls(commands, _copy(vertices, vtype), indices)

    def triangles(self) -> list[Triangle]:
        """Every triangle drawn, as vertex numbers in `vertices`; degenerate ones included."""
        places = _places(self, 0, 4)
        mem = _emit(self, places, with_vertices=False)
        stride = self.vertices.vtype.stride
        out: list[Triangle] = []
        for draw in DisplayList(_resolve(self, places)).draws(mem, 0):
            first = (draw.vertex_addr - places.vertices) // stride
            out += [(a + first, b + first, c + first) for a, b, c in draw.triangles()]
        return out

    def clear(self, keep_layout: bool = False) -> None:
        """Draw nothing. `keep_layout` turns the PRIMs into NOPs, so no command moves and the
        vertices stay; otherwise the PRIMs, the indices and the vertices go."""
        self.indices = []
        if keep_layout:
            self.commands = [Command(Op.NOP) if c.op == Op.PRIM else c for c in self.commands]
            return
        self.commands = [c for c in self.commands if c.op != Op.PRIM]
        self.vertices = Vertices(self.vertices.vtype)

    def clear_prims(self, prims: Iterable[int]) -> int:
        """Collapse the numbered primitives onto their first index, which draws nothing and moves
        no byte of the layout. Returns how many collapsed."""
        spans = self._spans()
        n = 0
        for p in sorted(_chosen(prims, len(spans))):
            lo, count = spans[p]
            if count:
                self.indices[lo : lo + count] = [self.indices[lo]] * count
                n += 1
        return n

    def budget(self, prims: Iterable[int] | None = None) -> Budget:
        """What `pack` can fit into the numbered primitives (all when None)."""
        spans, kinds = self._spans(), self._kinds()
        chosen = _chosen(prims, len(spans))
        return Budget(
            prims=len(chosen),
            slots=sum(spans[p][1] for p in chosen),
            triangles=sum(
                c - 2 if kinds[p] & 0xFF == Prim.TRIANGLE_STRIP else c // 3
                for p in chosen
                if (c := spans[p][1]) >= 3
            ),
            vertices=len(self.vertices) - len(self._drawn(spans, set(range(len(spans))) - chosen)),
        )

    def pack(
        self,
        positions: Sequence[Vec3],
        triangles: Sequence[Triangle],
        scale: Vec3,
        prims: Iterable[int] | None = None,
        *,
        reindex: bool = True,
        collapse: bool = True,
        uvs: Sequence[tuple[float, float]] | None = None,
        color: tuple[int, int, int, int] | None = None,
    ) -> Packed:
        """Fit a mesh into the numbered primitives (all when None) without adding a command.

        `positions` are model units (the `scale` that `Vertices.positions` takes), `uvs` 0..1 per
        source vertex (None keeps each slot's), `color` RGBA for every vertex written.
        `reindex` points the chosen primitives' indices at vertex slots no other primitive draws;
        without it only positions are written, and a primitive sharing a vertex is skipped.
        `collapse` makes the chosen primitives left over draw nothing.
        """
        vt = self.vertices.vtype
        if vt.through:
            raise ValueError("pack needs transformed vertices")
        spans, kinds = self._spans(), self._kinds()
        chosen = sorted(_chosen(prims, len(spans)))
        writer = _Writer(vt, positions, scale, uvs, color)
        source = list(self.indices)
        keep = self._drawn(spans, set(range(len(spans))) - set(chosen))
        run = _Pack(self, spans, kinds, chosen, triangles, writer, reindex, collapse)
        result = run(keep)
        # a vertex may be written only if no primitive keeping its indices draws it; which of
        # the chosen ones keep theirs is known only after packing, so widen and repack
        while True:  # `keep` only grows, so this ends
            after = {p for p in chosen if result.last is None or p > result.last}
            extra = {i for p in after for i in source[spans[p][0] : sum(spans[p])]}
            if extra <= keep:
                break
            keep |= extra
            self.indices = list(source)
            result = run(keep)
        writer.commit(self.vertices)
        return result.packed

    def _spans(self) -> list[tuple[int, int]]:
        """(first index, count) of each primitive in `indices`."""
        if not self.vertices.vtype.index:
            raise ValueError("the block draws without indices")
        spans, at = [], 0
        for c in self.commands:
            if c.op == Op.PRIM:
                spans.append((at, c.arg & 0xFFFF))
                at += c.arg & 0xFFFF
        return spans

    def _kinds(self) -> list[int]:
        """Per primitive, its type plus 256 when FFACE 1 is in force."""
        face_order, out = 0, []
        for c in self.commands:
            if c.op == Op.FFACE:
                face_order = c.arg & 1
            elif c.op == Op.PRIM:
                out.append(c.arg >> 16 & 7 | face_order << 8)
        return out

    def _drawn(self, spans: list[tuple[int, int]], prims: set[int]) -> set[int]:
        return {i for p in prims for i in self.indices[spans[p][0] : sum(spans[p])]}


class _Result(NamedTuple):
    packed: Packed
    last: int | None
    """The last primitive rewritten."""


class _Writer:
    """Vertex writes of one packing pass, stored by `commit`."""

    def __init__(
        self,
        vt: VertexType,
        positions: Sequence[Vec3],
        scale: Vec3,
        uvs: Sequence[tuple[float, float]] | None,
        color: tuple[int, int, int, int] | None,
    ) -> None:
        lay = vt.layout
        self.positions = quantize(positions, scale, lay.position)
        self.uvs: list[tuple[float, ...]] | None = None
        self.color: int | None = None
        if uvs is not None:
            if lay.texture is None:
                raise ValueError("the vertex type has no texture coordinates")
            self.uvs = quantize(uvs, (1.0, 1.0), lay.texture)
        if color is not None:
            if lay.color is None:
                raise ValueError("the vertex type has no colour")
            self.color = pack(color, Color(vt.color - COLOR_5650))
        self.writes: dict[int, int] = {}

    def commit(self, vertices: Vertices) -> None:
        morphs = vertices.vtype.morph_count
        for slot, source in self.writes.items():
            for row in range(slot * morphs, (slot + 1) * morphs):
                vertices.position[row] = self.positions[source]
                if self.uvs is not None:
                    vertices.texture[row] = self.uvs[source]
                if self.color is not None:
                    vertices.color[row] = self.color


@dataclass
class _Pack:
    """One packing pass over a block, from a set of vertices it must not write."""

    block: Block
    spans: list[tuple[int, int]]
    kinds: list[int]
    chosen: list[int]
    triangles: Sequence[Triangle]
    writer: _Writer
    reindex: bool
    collapse: bool

    def __call__(self, keep: set[int]) -> _Result:
        self.writer.writes = {}
        keep = set(keep)
        pool = [i for i in range(len(self.block.vertices)) if i not in keep][::-1]
        slot_of: dict[int, int] = {}
        stripper = Stripper(self.triangles)
        indices = self.block.indices
        placed = skipped = used = 0
        ran_out = False
        last: int | None = None

        def slot(src: int) -> int | None:
            s = slot_of.get(src)
            if s is None and pool:
                s = slot_of[src] = pool.pop()
                self.writer.writes[s] = src
            return s

        for p in self.chosen:
            lo, count = self.spans[p]
            slots = indices[lo : lo + count]
            if count < 3:
                continue
            if not len(stripper):
                if not self.collapse:
                    continue
                if self.reindex:
                    s = slot(0)
                    if s is None:
                        ran_out = True
                        break
                    indices[lo : lo + count] = [s] * count
                elif keep.intersection(slots):
                    skipped += 1
                    continue
                else:
                    self.writer.writes.update(dict.fromkeys(slots, 0))
                used, last = used + 1, p
                continue
            if not self.reindex and (keep.intersection(slots) or len(set(slots)) != count):
                skipped += 1  # checked before taking triangles, or the skip would drop them
                continue
            face_order = self.kinds[p] >> 8
            if self.kinds[p] & 0xFF == Prim.TRIANGLE_STRIP:
                strip = stripper.strip(count, face_order)
                src = strip + strip[-1:] * (count - len(strip))
                made = max(0, len(strip) - 2)
            else:
                loose = stripper.loose(count // 3, face_order)
                src = [i for tri in loose for i in tri]
                src += src[-1:] * (count - len(src))
                made = len(loose)
            if self.reindex:
                row = [slot(i) for i in src]
                if None in row:
                    ran_out = True
                    break
                indices[lo : lo + count] = [s for s in row if s is not None]
            else:
                self.writer.writes.update(zip(slots, src, strict=True))
                keep.update(slots)
            placed, used, last = placed + made, used + 1, p
        packed = Packed(
            prims=len(self.chosen),
            used=used,
            triangles=placed,
            skipped=skipped,
            left=len(stripper),
            vertices_used=len(slot_of),
            vertices_free=len(pool),
            ran_out=ran_out,
        )
        return _Result(packed, last)


def _chosen(prims: Iterable[int] | None, count: int) -> set[int]:
    if prims is None:
        return set(range(count))
    chosen = set(prims)
    if any(not 0 <= p < count for p in chosen):
        raise ValueError(f"a primitive number is outside 0..{count - 1}")
    return chosen


def _copy(vertices: Vertices, vtype: VertexType) -> Vertices:
    v = vertices
    return Vertices(
        vtype, [*v.weight], [*v.texture], [*v.color], [*v.normal], [*v.position], v.padding
    )


@dataclass
class Group:
    """One entry of the group table: a block drawn with one material and a palette patch."""

    block: Block
    material: int = 0
    """Index into the owning mesh's `materials`."""
    bones: list[BoneSlot] = field(default_factory=list)


@dataclass
class Mesh:
    groups: list[Group] = field(default_factory=list)
    materials: list[int] = field(default_factory=list)
    """The mesh's run of the remap table: indices into `Pmo.materials`."""
    uv_scale: tuple[float, float] = (1.0, 1.0)
    lighting: int = 0x80000003
    """Bit 0 lighting, 1 fog, 2 alpha blending, 31 enable."""
    blend: int = 0
    """A GE BLENDMODE command word, or 0."""


@dataclass
class _Header(DataclassMixin):
    magic: bytes = csfield_const(Bytes(4), MAGIC)
    version: bytes = csfield(Bytes(4))
    size: int = csfield(Int32ul)
    clip: float = csfield(Float32l)
    scale: list[float] = csfield(Array(3, Float32l))
    mesh_count: int = csfield(Int16ul)
    material_count: int = csfield(Int16ul)
    meshes: int = csfield(Int32ul)
    groups: int = csfield(Int32ul)
    remaps: int = csfield(Int32ul)
    """0 when there is no remap table (MHP3rd)."""
    bones: int = csfield(Int32ul)
    materials: int = csfield(Int32ul)
    geometry: int = csfield(Int32ul)
    _reserved: bytes = csfield_const(Bytes(8), bytes(8))


@dataclass
class _MeshCounts(DataclassMixin):
    """The tail of a mesh record, derived from the tables."""

    material_count: int = csfield(Int8ul)
    _pad: bytes = csfield_const(Bytes(1), b"\0")
    material_start: int = csfield(Int16ul)
    group_count: int = csfield(Int16ul)
    group_start: int = csfield(Int16ul)


@dataclass
class _MeshRecord(DataclassMixin):
    uv_scale: list[float] = csfield(Array(2, Float32l))
    lighting: int = csfield(Int32ul)
    blend: int = csfield(Int32ul)
    counts: _MeshCounts = csfield(DataclassStruct(_MeshCounts))


@dataclass
class _GroupRecord(DataclassMixin):
    material: int = csfield(Int8ul)
    bone_count: int = csfield(Int8ul)
    bone_start: int = csfield(Int16ul)
    list_offset: int = csfield(Int32ul)
    vertex_offset: int = csfield(Int32ul)
    index_offset: int = csfield(Int32ul)
    """0 when the list draws without indices."""


_HEADER = DataclassStruct(_Header)
_GROUP = DataclassStruct(_GroupRecord)
_MATERIAL = DataclassStruct(Material)
_BONE = struct.Struct("<2B")


@dataclass
class Pmo:
    meshes: list[Mesh] = field(default_factory=list)
    materials: list[Material] = field(default_factory=list)
    scale: Vec3 = (1.0, 1.0, 1.0)
    """Multiplies the dequantised positions."""
    clip: float = 0.0
    """Clipping distance; MHFU sets it to the largest scale component."""
    tail: bytes = b""
    """Bytes after the size the header declares (a standalone file pads to 0x800)."""

    VERSION: ClassVar[bytes] = b"1.0\0"
    ALIGN: ClassVar[int] = 16
    """Alignment of each list and buffer in the geometry region."""
    _MESH: ClassVar[DataclassStruct] = DataclassStruct(_MeshRecord)  # type: ignore[type-arg]

    @staticmethod
    def sniff(data: bytes) -> bool:
        return _sniff(data, Pmo.VERSION)

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        return cls._read(data, None)

    def to_bytes(self) -> bytes:
        tables, region = self._layout()
        return bytes(tables) + region + self.tail

    # views -----------------------------------------------------------------------------------

    def groups(self) -> list[Group]:
        """Every group in table order; `g` below is a position here."""
        return [group for mesh in self.meshes for group in mesh.groups]

    def mesh_of(self, g: int) -> Mesh:
        at = g
        for mesh in self.meshes:
            if 0 <= at < len(mesh.groups):
                return mesh
            at -= len(mesh.groups)
        raise IndexError(f"no group {g}")

    def scale_of(self, g: int) -> Vec3:
        """The scale `positions` applies to group `g`."""
        return self.scale

    def positions(self, g: int) -> list[Vec3]:
        rows = self.groups()[g].block.vertices.positions(self.scale_of(g))
        return [(x, y, z) for x, y, z in rows]

    def triangles(self, g: int) -> list[Triangle]:
        return self.groups()[g].block.triangles()

    def material(self, g: int) -> Material | None:
        """None when the remap points past the material table."""
        remap, index = self.mesh_of(g).materials, self.groups()[g].material
        at = remap[index] if index < len(remap) else -1
        return self.materials[at] if 0 <= at < len(self.materials) else None

    def palette(self, g: int) -> list[int]:
        """The skeleton bone in each matrix slot once group `g` has patched it; -1 for unset."""
        slots: dict[int, int] = {}
        for group in self.groups()[: g + 1]:
            slots.update(group.bones)
        return [slots.get(s, -1) for s in range(max(slots, default=-1) + 1)]

    def influences(self, g: int) -> list[list[Influence]]:
        """Each vertex's (bone, weight) pairs; without weights, weight 1 on slot 0's bone."""
        palette = self.palette(g)
        vertices = self.groups()[g].block.vertices
        if not vertices.vtype.weight:
            rigid = [(palette[0], 1.0)] if palette else []
            return [list(rigid) for _ in vertices.position]
        return [
            [(palette[k] if k < len(palette) else -1, w) for k, w in enumerate(row)]
            for row in vertices.weights()
        ]

    # resident writes -------------------------------------------------------------------------

    def to_bytes_inplace(self, original: bytes) -> tuple[bytes, list[int]]:
        """Lay the region out over `original`, the PMO this one was read from: a block that
        still fits stays at its offset (what it no longer uses keeps its old bytes), one that
        grew moves into the first hole that holds it. Same length as `original`.

        Returns the bytes and the groups whose block moved. The engine reads the group table
        while it draws, so a PMO written into a resident PAC wants that list empty.
        """
        if not self.sniff(original):
            raise ValueError("the original is not this kind of PMO")
        head = _HEADER.parse(original)
        if head.size > len(original):
            raise ValueError("the original keeps its geometry in a companion file")
        groups = self.groups()
        count = sum(counts.group_count for _, counts in self._meshes(original, head))
        records = _read_groups(original, head, count) if count == len(groups) else []
        old: dict[int, int] = {}
        for group, rec in zip(groups, records, strict=False):
            old.setdefault(id(group.block), rec.list_offset)
        blocks = _unique_blocks(groups)
        starts = sorted(set(old.values()))
        if not records or len(starts) != len(blocks):
            raise ValueError("an in-place layout needs the original's groups and blocks")
        region_size = head.size - head.geometry
        extent = dict(zip(starts, [*starts[1:], region_size], strict=True))
        placed: dict[int, _Places] = {}
        holes: list[list[int]] = []
        grown: list[Block] = []
        for block in blocks:
            lo = old[id(block)]
            places = _places(block, lo, self.ALIGN)
            if places.end <= extent[lo]:
                placed[id(block)] = places
                holes.append([places.end, extent[lo]])
            else:
                grown.append(block)
                holes.append([lo, extent[lo]])
        holes.sort()
        for block in grown:
            for hole in holes:
                places = _places(block, hole[0], self.ALIGN)
                if places.end <= hole[1]:
                    placed[id(block)] = places
                    hole[0] = places.end
                    break
            else:
                raise ValueError("a grown block fits no hole; clear another group to make one")
        region = bytearray(original[head.geometry : head.size])
        for block in blocks:
            places = placed[id(block)]
            words = DisplayList(_resolve(block, places)).to_bytes()
            region[places.list : places.list + len(words)] = words
            vertices = block.vertices.to_bytes()
            region[places.vertices : places.vertices + len(vertices)] = vertices
            indices = _index_bytes(block)
            region[places.indices : places.indices + len(indices)] = indices
        tables = self._tables(groups, placed, region_size)
        if len(tables) != head.geometry:
            raise ValueError("an in-place layout needs tables of the original's size")
        moved = {id(b) for b in grown}
        out = bytes(tables) + region + original[head.size :]
        return out, [g for g, group in enumerate(groups) if id(group.block) in moved]

    # reading and writing ---------------------------------------------------------------------

    @classmethod
    def _read(cls, data: bytes, geometry: bytes | None) -> Self:
        if not cls.sniff(data):
            raise FormatError(f"not a PMO {cls.VERSION!r}")
        try:
            head = _HEADER.parse(data)
            if head.size <= len(data):
                mem, base, tail, geometry_tail = data, head.geometry, data[head.size :], None
            elif geometry is not None and head.geometry == len(data):
                mem, base, tail = geometry, 0, b""
                geometry_tail = geometry[head.size - head.geometry :]
            else:
                raise FormatError(f"size {head.size:#x} runs past the {len(data):#x} bytes")
            records = cls._meshes(data, head)
            groups = _read_groups(data, head, sum(counts.group_count for _, counts in records))
            materials = list(Array(head.material_count, _MATERIAL).parse(data[head.materials :]))
        except ConstructError as e:
            raise FormatError(f"PMO tables: {e}") from None
        remap_size = sum(counts.material_count for _, counts in records)
        palette_size = sum(g.bone_count for g in groups)
        if head.remaps + remap_size > len(data) or head.bones + 2 * palette_size > len(data):
            raise FormatError("the remap table or the bone palette runs past the data")
        remap = data[head.remaps : head.remaps + remap_size] if head.remaps else range(remap_size)
        palette = [BoneSlot(*p) for p in _BONE.iter_unpack(data[head.bones :][: 2 * palette_size])]
        meshes: list[Mesh] = []
        blocks: dict[int, Block] = {}
        at_group = at_material = at_bone = 0
        for i, (mesh, counts) in enumerate(records):
            if (counts.group_start, counts.material_start) != (at_group, at_material):
                raise FormatError(f"mesh {i} does not start where the one before ends")
            mesh.materials = list(remap[at_material : at_material + counts.material_count])
            for g in groups[at_group : at_group + counts.group_count]:
                if g.bone_start != at_bone:
                    raise FormatError(f"a group's bones start at {g.bone_start}, not {at_bone}")
                block = blocks.get(g.list_offset)
                if block is None:
                    block = blocks[g.list_offset] = _read_block(mem, base, g)
                bones = palette[at_bone : at_bone + g.bone_count]
                mesh.groups.append(Group(block, g.material, bones))
                at_bone += g.bone_count
            meshes.append(mesh)
            at_group += counts.group_count
            at_material += counts.material_count
        scale = (head.scale[0], head.scale[1], head.scale[2])
        pmo = cls(meshes, materials, scale, head.clip, tail)
        pmo._store(head.remaps != 0, geometry_tail)
        return pmo

    @classmethod
    def _meshes(cls, data: bytes, head: _Header) -> list[tuple[Mesh, _MeshCounts]]:
        return [cls._mesh(r) for r in Array(head.mesh_count, cls._MESH).parse(data[head.meshes :])]

    @classmethod
    def _mesh(cls, record: DataclassMixin) -> tuple[Mesh, _MeshCounts]:
        rec = cast(_MeshRecord, record)
        mesh = Mesh(
            uv_scale=(rec.uv_scale[0], rec.uv_scale[1]), lighting=rec.lighting, blend=rec.blend
        )
        return mesh, rec.counts

    def _mesh_record(self, mesh: Mesh, counts: _MeshCounts) -> DataclassMixin:
        return _MeshRecord(list(mesh.uv_scale), mesh.lighting, mesh.blend, counts)

    def _store(self, remap_table: bool, geometry_tail: bytes | None) -> None:
        """Where the file keeps its remap and geometry; MHFU has the table and no companion."""
        if not remap_table or geometry_tail is not None:
            raise FormatError("a 1.0 PMO without a remap table or with external geometry")

    def _remap_table(self) -> bool:
        return True

    def _layout(self) -> tuple[bytearray, bytes]:
        """The header and tables, and the geometry region they point into."""
        groups = self.groups()
        region = bytearray()
        placed: dict[int, _Places] = {}
        for block in _unique_blocks(groups):
            places = placed[id(block)] = _places(block, len(region), self.ALIGN)
            region += _emit(block, places, len(region))
        region += bytes(_up(len(region), 16) - len(region))
        return self._tables(groups, placed, len(region)), bytes(region)

    def _tables(
        self, groups: list[Group], placed: dict[int, "_Places"], region_size: int
    ) -> bytearray:
        """Header and tables, up to the geometry offset."""
        out = bytearray(_HEADER_SIZE)
        try:
            records: list[DataclassMixin] = []
            remap: list[int] = []
            at_group = 0
            for mesh in self.meshes:
                counts = _MeshCounts(len(mesh.materials), len(remap), len(mesh.groups), at_group)
                records.append(self._mesh_record(mesh, counts))
                remap += mesh.materials
                at_group += len(mesh.groups)
            if not self._remap_table() and remap != list(range(len(remap))):
                raise ValueError("without a remap table every mesh's materials run in order")
            meshes_at = len(out)
            out += b"".join(self._MESH.build(r) for r in records)
            out += bytes(_up(len(out), _TABLE_ALIGN) - len(out))
            groups_at = len(out)
            bones: list[BoneSlot] = []
            for group in groups:
                places = placed[id(group.block)]
                rec = _GroupRecord(
                    group.material,
                    len(group.bones),
                    len(bones),
                    places.list,
                    places.vertices,
                    places.indices,
                )
                out += _GROUP.build(rec)
                bones += group.bones
            remaps_at = 0
            if self._remap_table():
                remaps_at = len(out)
                out += bytes(remap)
                out += bytes(_up(len(out), _TABLE_ALIGN) - len(out))
            bones_at = len(out)
            out += b"".join(_BONE.pack(*b) for b in bones)
            out += bytes(_up(len(out), _TABLE_ALIGN) - len(out))
            materials_at = len(out)
            out += b"".join(_MATERIAL.build(m) for m in self.materials)
            head = _Header(
                version=self.VERSION,
                size=len(out) + region_size,
                clip=self.clip,
                scale=list(self.scale),
                mesh_count=len(self.meshes),
                material_count=len(self.materials),
                meshes=meshes_at,
                groups=groups_at,
                remaps=remaps_at,
                bones=bones_at,
                materials=materials_at,
                geometry=len(out),
            )
            out[:_HEADER_SIZE] = _HEADER.build(head)
        except (ConstructError, struct.error) as e:
            raise ValueError(f"a PMO table value does not fit: {e}") from None
        return out


def _sniff(data: bytes, version: bytes) -> bool:
    return len(data) >= _HEADER_SIZE and data[:4] == MAGIC and data[4:8] == version


def _read_groups(data: bytes, head: _Header, count: int) -> list[_GroupRecord]:
    return list(Array(count, _GROUP).parse(data[head.groups :]))


def _unique_blocks(groups: list[Group]) -> list[Block]:
    seen: dict[int, Block] = {}
    for group in groups:
        seen.setdefault(id(group.block), group.block)
    return list(seen.values())


def _read_block(mem: bytes, base: int, rec: _GroupRecord) -> Block:
    at = base + rec.list_offset
    dl = DisplayList.from_bytes(mem, at)
    vtypes = {c.arg for c in dl.commands if c.op == Op.VTYPE}
    if len(vtypes) != 1:
        raise FormatError(f"the list at {rec.list_offset:#x} sets {len(vtypes)} vertex types")
    vtype = VertexType.from_word(vtypes.pop())
    stride = vtype.stride
    vertex_at, index_at = base + rec.vertex_offset, base + rec.index_offset
    count = 0
    indices: list[int] = []
    for draw in dl.draws(mem, at):
        first, rest = divmod(draw.vertex_addr - vertex_at, stride)
        if rest or first < 0:
            raise FormatError(f"a draw of the list at {rec.list_offset:#x} misses its vertices")
        count = max(count, first + (draw.count if draw.index_addr is None else draw.vertex_count))
        indices += draw.indices if draw.index_addr is not None else ()
    commands = []
    for (op, arg), origin in zip(dl.commands, origins(dl.commands), strict=True):
        if op in (Op.VADDR, Op.IADDR):
            if origin is None:
                raise FormatError(f"the list at {rec.list_offset:#x} addresses without ORIGIN")
            if op == Op.VADDR:
                arg, rest = divmod(at + origin + arg - vertex_at, stride)
            elif vtype.index:
                arg, rest = divmod(at + origin + arg - index_at, vtype.index_size)
            else:
                raise FormatError(f"the list at {rec.list_offset:#x} sets IADDR, draws unindexed")
            if rest or not 0 <= arg < 1 << 24:
                raise FormatError(f"the list at {rec.list_offset:#x} points outside its buffers")
        commands.append(Command(op, arg))
    return Block(commands, Vertices.from_bytes(mem, vtype, count, vertex_at), indices)


class _Places(NamedTuple):
    """Where `_emit` puts a block's parts; `indices` is 0 without an index format."""

    list: int
    vertices: int
    indices: int
    end: int


def _places(block: Block, at: int, align: int) -> _Places:
    vt = block.vertices.vtype
    list_at = _up(at, align)
    vertex_at = _up(list_at + 4 * len(block.commands), align)
    end = vertex_at + len(block.vertices) * vt.stride
    if not vt.index:
        if block.indices:
            raise ValueError("indices on a vertex type without an index format")
        return _Places(list_at, vertex_at, 0, end)
    index_at = _up(end, align)
    return _Places(list_at, vertex_at, index_at, index_at + len(block.indices) * vt.index_size)


def _resolve(block: Block, places: _Places) -> list[Command]:
    """The block's commands with the arguments the layout writes."""
    vt = block.vertices.vtype
    out = []
    for (op, arg), origin in zip(block.commands, origins(block.commands), strict=True):
        if op == Op.VTYPE:
            arg = vt.to_word()
        elif op in (Op.VADDR, Op.IADDR):
            if origin is None:
                raise ValueError("VADDR and IADDR need an ORIGIN before them")
            if op == Op.VADDR:
                arg = places.vertices + arg * vt.stride - places.list - origin
            elif vt.index:
                arg = places.indices + arg * vt.index_size - places.list - origin
            else:
                raise ValueError("IADDR on a vertex type without an index format")
        out.append(Command(op, arg))
    return out


def _index_bytes(block: Block) -> bytes:
    code = _INDEX.get(block.vertices.vtype.index)
    if code is None:
        return b""
    try:
        return struct.pack(f"<{len(block.indices)}{code}", *block.indices)
    except struct.error as e:
        raise ValueError(f"an index does not fit {code}: {e}") from None


def _emit(block: Block, places: _Places, start: int = 0, with_vertices: bool = True) -> bytes:
    """The block laid out at `places`, from address `start` (zeros before its list)."""
    out = bytearray(places.list - start)
    out += DisplayList(_resolve(block, places)).to_bytes()
    out += bytes(places.vertices - start - len(out))
    if with_vertices:
        out += block.vertices.to_bytes()
    else:
        out += bytes(len(block.vertices) * block.vertices.vtype.stride)
    if places.indices:
        out += bytes(places.indices - start - len(out))
        out += _index_bytes(block)
    return bytes(out)
