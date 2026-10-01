# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""MHFU stage file `st<NNN>.pac`: a PAC of terrain, textures, props, environment, parameter
blocks and the `HITS` collision.

Inside a `HITS` chunk every offset is relative to the chunk start + `BASE`, except the cell-list
words, which are `tri_index * 56` into the triangle array. Cell `(ix, iz)` is grid entry
`ix * nz + iz`; the engine applies no origin and no bounds check. At load the engine turns the
two header offsets, every grid word and every list word into pointers.
"""

import struct
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import NamedTuple, Self

from construct import Array, Const, Float32l, GreedyBytes, Int8ul, Int16ul, Int32sl, Int32ul
from construct_typed import DataclassMixin, DataclassStruct, csfield, csfield_noinit

from .._base import FormatError
from ..pac import Pac

Vec3 = tuple[float, float, float]

TRI_SIZE = 56
TERM = 0xFFFFFFFF
"""Ends every cell list."""

BASE = 8
"""Offsets inside a `HITS` chunk count from its start + BASE."""
GRID_POINTER = 0x20
"""The header word holding the grid's offset."""
TRIS_POINTER = 0x24
"""The header word holding the triangle array's offset."""
GRID_AT = 0x28
"""Where the grid starts in a chunk: one cell-list offset per cell."""

_ROLES = 6

# construct parses and builds these ~60x slower than struct (14 us per triangle, ~300k
# triangles over the retail stages), so triangle records and u32 runs go through struct.
_TRI = struct.Struct("<BBH13f")
_F32 = struct.Struct("<f")

SURFACE_BITS: dict[int, str] = {
    0x01: "contact flag",
    0x02: "actor flag",
    0x10: "query hit",
    0x20: "sink: the surface sits above the ground",
    0x40: "exclude: with 0x20, some queries drop the triangle",
    0x80: "wade: some actor types stand on the surface, not the ground",
}
"""Bits of a stage overlay's surface-property entry, which `TriFlags.surface_id` indexes."""


@dataclass
class _Header(DataclassMixin):
    tag: bytes | None = csfield_noinit(Const(b"HITS"))
    size: int = csfield(Int32ul)
    cell_x: int = csfield(Int32ul)
    cell_z: int = csfield(Int32ul)
    nx: int = csfield(Int32ul)
    nz: int = csfield(Int32ul)
    origin_x: int = csfield(Int32sl)
    origin_z: int = csfield(Int32sl)
    grid: int = csfield(Int32ul)
    tris: int = csfield(Int32ul)


_HEADER = DataclassStruct(_Header)


@dataclass
class Environment(DataclassMixin):
    """Entry 3."""

    kind: int = csfield(Int16ul)
    fog_rgba: list[int] = csfield(Array(4, Int8ul))
    fog_range: list[float] = csfield(Array(2, Float32l))
    """(start, end)-shaped: 0 or +-100..2000, then 8000..220000."""
    rest: bytes = csfield(GreedyBytes)
    """From 0x0E: directions, colours and floats not yet told apart."""

    @staticmethod
    def sniff(data: bytes) -> bool:
        return len(data) >= 14 and data[:2] == b"\x02\x00"

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        if len(data) < 14:
            raise FormatError("environment block shorter than its known fields")
        return DataclassStruct(cls).parse(data)

    def to_bytes(self) -> bytes:
        return bytes(DataclassStruct(type(self)).build(self))


@dataclass
class TriFlags:
    """The first word of a triangle."""

    surface_id: int = 0
    """Index into the stage overlay's surface-property table (`SURFACE_BITS`)."""
    material: int = 0
    """Footstep/effect class; 9 or 10 on a near-vertical triangle is a climbable wall."""
    exclude: int = 0
    """Query mask: a query sharing a bit skips the triangle."""

    @classmethod
    def from_word(cls, word: int) -> Self:
        return cls(word & 0xFF, (word >> 8) & 0xFF, word >> 16)

    @property
    def word(self) -> int:
        return self.surface_id | self.material << 8 | self.exclude << 16


@dataclass
class Tri:
    """One 56-byte collision triangle: `dot(normal, v) + plane_d == 0`."""

    flags: TriFlags
    v0: Vec3
    v1: Vec3
    v2: Vec3
    normal: Vec3
    plane_d: float

    @classmethod
    def from_verts(cls, v0: Vec3, v1: Vec3, v2: Vec3, flags: TriFlags | None = None) -> Self:
        """Derive the normal and plane, rounded to the float32 the file holds."""
        a = [v1[k] - v0[k] for k in range(3)]
        b = [v2[k] - v0[k] for k in range(3)]
        n = (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
        length = sum(c * c for c in n) ** 0.5
        if length < 1e-9:
            raise ValueError(f"degenerate triangle: {v0} {v1} {v2}")
        unit = _f32v(tuple(c / length for c in n))
        p0 = _f32v(v0)
        d = _f32(-sum(unit[k] * p0[k] for k in range(3)))
        return cls(flags or TriFlags(), p0, _f32v(v1), _f32v(v2), unit, d)

    def to_bytes(self) -> bytes:
        f = self.flags
        return _TRI.pack(
            f.surface_id,
            f.material,
            f.exclude,
            *self.v0,
            *self.v1,
            *self.v2,
            *self.normal,
            self.plane_d,
        )

    def vertical(self) -> bool:
        """The xz projection is a line; retail cell lists may leave these out."""
        (ax, az), (bx, bz), (cx, cz) = _xz(self)
        return abs((bx - ax) * (cz - az) - (cx - ax) * (bz - az)) < 1e-3


class PlaneCheck(NamedTuple):
    ok: int
    bad: int

    @property
    def frac(self) -> float:
        return self.ok / (self.ok + self.bad) if self.ok + self.bad else 0.0


class GridCheck(NamedTuple):
    """Stored cell lists against the exact triangle-vs-cell overlap."""

    entries: int
    missing: int
    """Overlaps no list names; must be 0 for anything walkable."""
    missing_vertical: int
    """The part of `missing` that `Tri.vertical` explains."""
    extra: int
    unlisted: int
    """Triangles in no list at all."""


@dataclass
class Hits:
    """One `HITS` chunk: an xz broadphase grid over its triangles."""

    grid: tuple[int, int] = (0, 0)
    """(nx, nz)."""
    cell: tuple[int, int] = (501, 501)
    origin: tuple[int, int] = (0, 0)
    cells: list[list[int]] = field(default_factory=list)
    """Triangle indices per grid cell. Stored: retail lists are a superset of the overlap."""
    tris: list[Tri] = field(default_factory=list)

    @staticmethod
    def sniff(data: bytes) -> bool:
        return data[:4] == b"HITS"

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        if len(data) < GRID_AT or not cls.sniff(data):
            raise FormatError("not a HITS chunk")
        h = _HEADER.parse(data)
        ncells = h.nx * h.nz
        lists_at = GRID_AT + 4 * ncells
        tri_at = BASE + h.tris
        if h.size != len(data) or h.grid != GRID_AT - BASE or not lists_at <= tri_at <= len(data):
            raise FormatError("HITS header does not describe the chunk")
        if (len(data) - tri_at) % TRI_SIZE:
            raise FormatError("HITS triangle array is not whole 56-byte records")
        ntri = (len(data) - tri_at) // TRI_SIZE
        heads = struct.unpack_from(f"<{ncells}I", data, GRID_AT)
        words = struct.unpack_from(f"<{(tri_at - lists_at) // 4}I", data, lists_at)
        cells, pos = [], 0
        for head in heads:
            if BASE + head != lists_at + 4 * pos:
                raise FormatError("HITS cell lists are not packed in cell order")
            try:
                end = words.index(TERM, pos)
            except ValueError:
                raise FormatError("HITS cell list has no terminator") from None
            run = words[pos:end]
            if any(w % TRI_SIZE or w // TRI_SIZE >= ntri for w in run):
                raise FormatError("HITS cell list names no triangle")
            cells.append([w // TRI_SIZE for w in run])
            pos = end + 1
        if lists_at + 4 * pos != tri_at:
            raise FormatError("HITS cell lists do not end at the triangle array")
        tris = [
            Tri(TriFlags(s, m, e), (x0, y0, z0), (x1, y1, z1), (x2, y2, z2), (nx, ny, nz), d)
            for s, m, e, x0, y0, z0, x1, y1, z1, x2, y2, z2, nx, ny, nz, d in _TRI.iter_unpack(
                data[tri_at:]
            )
        ]
        return cls((h.nx, h.nz), (h.cell_x, h.cell_z), (h.origin_x, h.origin_z), cells, tris)

    def to_bytes(self) -> bytes:
        nx, nz = self.grid
        if len(self.cells) != nx * nz:
            raise ValueError(f"{len(self.cells)} cell lists for a {nx}x{nz} grid")
        if any(not 0 <= t < len(self.tris) for run in self.cells for t in run):
            raise ValueError("a cell list names a triangle the chunk does not have")
        heads = self.heads()
        words = [w for run in self.cells for w in (*(t * TRI_SIZE for t in run), TERM)]
        header = _Header(
            size=self.tri_offset + TRI_SIZE * len(self.tris),
            cell_x=self.cell[0],
            cell_z=self.cell[1],
            nx=nx,
            nz=nz,
            origin_x=self.origin[0],
            origin_z=self.origin[1],
            grid=GRID_AT - BASE,
            tris=self.tri_offset - BASE,
        )
        out = bytearray(_HEADER.build(header))
        out += struct.pack(f"<{len(heads)}I", *heads)
        out += struct.pack(f"<{len(words)}I", *words)
        out += b"".join(t.to_bytes() for t in self.tris)
        return bytes(out)

    def heads(self) -> list[int]:
        """The grid as `to_bytes` writes it: each cell list's offset from the chunk + BASE."""
        out, at = [], GRID_AT - BASE + 4 * len(self.cells)
        for run in self.cells:
            out.append(at)
            at += 4 * (len(run) + 1)
        return out

    @property
    def tri_offset(self) -> int:
        """Where the triangle array starts in the chunk; triangle t is TRI_SIZE * t past it."""
        return GRID_AT + 4 * (len(self.cells) + sum(len(run) + 1 for run in self.cells))

    def cell_of(self, x: float, z: float) -> int | None:
        """Grid index holding a world xz, or None outside the lattice."""
        ix = int((x - self.origin[0]) // self.cell[0])
        iz = int((z - self.origin[1]) // self.cell[1])
        if not (0 <= ix < self.grid[0] and 0 <= iz < self.grid[1]):
            return None
        return ix * self.grid[1] + iz

    def verify(self) -> PlaneCheck:
        """Plane self-check: `|normal| == 1` and v0 on the plane, both within 2%."""
        ok = 0
        for t in self.tris:
            length = sum(c * c for c in t.normal) ** 0.5
            resid = abs(sum(a * b for a, b in zip(t.normal, t.v0, strict=True)) + t.plane_d)
            scale = max(max(abs(c) for c in t.v0), 1.0)
            ok += abs(length - 1.0) < 0.02 and resid / scale < 0.02
        return PlaneCheck(ok, len(self.tris) - ok)

    def grid_check(self) -> GridCheck:
        have = [set(run) for run in self.cells]
        want = cell_map(self.tris, self.grid, self.cell, self.origin)
        missing = [t for h, w in zip(have, want, strict=True) for t in w - h]
        return GridCheck(
            entries=sum(len(h) for h in have),
            missing=len(missing),
            missing_vertical=sum(self.tris[t].vertical() for t in missing),
            extra=sum(len(h - w) for h, w in zip(have, want, strict=True)),
            unlisted=len(self.tris) - len(set().union(*have)),
        )


@dataclass
class Collision:
    """Entry 5: a PAC of `HITS` chunks; retail ships [0] walls and ceilings, [1] floor."""

    chunks: list[Hits] = field(default_factory=list)
    align: int = 4
    tail: bytes = b""

    @staticmethod
    def sniff(data: bytes) -> bool:
        if len(data) < 12 or not 0 < int.from_bytes(data[:4], "little") <= 256:
            return False
        off = int.from_bytes(data[4:8], "little")
        return Hits.sniff(data[off : off + 4])

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        pac = Pac.from_bytes(data)
        return cls([Hits.from_bytes(e) for e in pac.entries], pac.align, pac.tail)

    def to_bytes(self) -> bytes:
        return self._pac().to_bytes()

    def table(self) -> list[tuple[int, int]]:
        """The `(offset, size)` of each chunk where `to_bytes` puts it."""
        return self._pac().table()

    def _pac(self) -> Pac:
        return Pac([c.to_bytes() for c in self.chunks], self.align, self.tail)

    def verify(self) -> PlaneCheck:
        checks = [c.verify() for c in self.chunks]
        return PlaneCheck(sum(c.ok for c in checks), sum(c.bad for c in checks))


@dataclass
class Stage:
    """`st<NNN>.pac`. A placeholder stage has every entry empty."""

    terrain: bytes = b""
    """PMO."""
    textures: bytes = b""
    """TMH."""
    props: bytes = b""
    """PMO: props, water, sky; empty in five stages."""
    environment: Environment | None = None
    params: bytes = b""
    """The `02 01` parameter block, fixed up with pointers at load."""
    collision: Collision | None = None
    extra: list[bytes] = field(default_factory=list)
    """Entries past the sixth (eight stages carry one or two)."""
    align: int = 16
    tail: bytes = b""
    """Bytes after the last entry up to the end of the file."""

    @staticmethod
    def sniff(data: bytes) -> bool:
        if _placeholder(data):
            return True
        count = int.from_bytes(data[:4], "little")
        if not _ROLES <= count <= 256 or len(data) < 4 + 8 * count:
            return False
        table = struct.unpack_from(f"<{2 * count}I", data, 4)
        terrain, (coll, size) = table[0], table[10:12]
        return data[terrain : terrain + 4] == b"pmo\0" and Collision.sniff(data[coll : coll + size])

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        pac = Pac.from_bytes(data)
        e = pac.entries
        if len(e) < _ROLES:
            raise FormatError(f"a stage has at least {_ROLES} entries, not {len(e)}")
        return cls(
            terrain=e[0],
            textures=e[1],
            props=e[2],
            environment=Environment.from_bytes(e[3]) if e[3] else None,
            params=e[4],
            collision=Collision.from_bytes(e[5]) if e[5] else None,
            extra=e[_ROLES:],
            align=pac.align,
            tail=pac.tail,
        )

    def to_bytes(self) -> bytes:
        return self._pac().to_bytes()

    def table(self) -> list[tuple[int, int]]:
        """The `(offset, size)` of each entry where `to_bytes` puts it: [0] terrain .. [5]
        collision, then `extra`."""
        return self._pac().table()

    def _pac(self) -> Pac:
        entries = [
            self.terrain,
            self.textures,
            self.props,
            self.environment.to_bytes() if self.environment else b"",
            self.params,
            self.collision.to_bytes() if self.collision else b"",
            *self.extra,
        ]
        return Pac(entries, self.align, self.tail)

    def verify(self) -> PlaneCheck:
        return self.collision.verify() if self.collision else PlaneCheck(0, 0)


def _placeholder(data: bytes) -> bool:
    """A stage with no entries at all: 20 of the 282 ship so."""
    count = int.from_bytes(data[:4], "little")
    head = 4 + 8 * count
    return _ROLES <= count <= 256 and len(data) >= head and not any(data[4:head])


def _f32(x: float) -> float:
    value: float = _F32.unpack(_F32.pack(x))[0]
    return value


def _f32v(v: Sequence[float]) -> Vec3:
    return _f32(v[0]), _f32(v[1]), _f32(v[2])


def _xz(t: Tri) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    return (t.v0[0], t.v0[2]), (t.v1[0], t.v1[2]), (t.v2[0], t.v2[2])


def _tri_hits_rect(
    p: tuple[float, float],
    q: tuple[float, float],
    r: tuple[float, float],
    x0: float,
    x1: float,
    z0: float,
    z1: float,
) -> bool:
    """2D separating-axis test of a triangle against an axis-aligned rectangle.

    Only the triangle's edges are tried: callers pass rectangles that overlap its bounding box.
    """
    corners = ((x0, z0), (x1, z0), (x1, z1), (x0, z1))
    cen = ((p[0] + q[0] + r[0]) / 3.0, (p[1] + q[1] + r[1]) / 3.0)
    for a, b in ((p, q), (q, r), (r, p)):
        nx, nz = b[1] - a[1], a[0] - b[0]
        d = [nx * (c[0] - a[0]) + nz * (c[1] - a[1]) for c in corners]
        inside = nx * (cen[0] - a[0]) + nz * (cen[1] - a[1])
        if (inside >= 0 and max(d) < 0) or (inside < 0 and min(d) > 0):
            return False
    return True


def cells_for_tri(
    tri: Tri,
    grid: tuple[int, int],
    cell: tuple[int, int],
    origin: tuple[int, int] = (0, 0),
    pad: float = 0.0,
) -> list[int]:
    """Every grid index the triangle's xz projection overlaps; `pad` widens each cell."""
    nx, nz = grid
    cx, cz = cell
    p, q, r = _xz(tri)
    lox = min(p[0], q[0], r[0]) - origin[0] - pad
    hix = max(p[0], q[0], r[0]) - origin[0] + pad
    loz = min(p[1], q[1], r[1]) - origin[1] - pad
    hiz = max(p[1], q[1], r[1]) - origin[1] + pad
    out = []
    for ix in range(max(0, int(lox // cx)), min(nx - 1, int(hix // cx)) + 1):
        for iz in range(max(0, int(loz // cz)), min(nz - 1, int(hiz // cz)) + 1):
            x0, z0 = origin[0] + ix * cx, origin[1] + iz * cz
            if _tri_hits_rect(p, q, r, x0 - pad, x0 + cx + pad, z0 - pad, z0 + cz + pad):
                out.append(ix * nz + iz)
    return out


def cell_map(
    tris: Sequence[Tri],
    grid: tuple[int, int],
    cell: tuple[int, int],
    origin: tuple[int, int] = (0, 0),
    pad: float = 0.0,
) -> list[set[int]]:
    """Triangle indices per grid cell, from the exact overlap."""
    out: list[set[int]] = [set() for _ in range(grid[0] * grid[1])]
    for t, tri in enumerate(tris):
        for i in cells_for_tri(tri, grid, cell, origin, pad):
            out[i].add(t)
    return out


def fit_grid(
    tris: Iterable[Tri],
    cell: tuple[int, int] = (501, 501),
    origin: tuple[int, int] = (0, 0),
    cap: int = 256,
) -> tuple[int, int]:
    """Smallest (nx, nz) covering every triangle, clamped to `cap`."""
    tris = list(tris)
    hix = max((v[0] for t in tris for v in (t.v0, t.v1, t.v2)), default=0.0) - origin[0]
    hiz = max((v[2] for t in tris for v in (t.v0, t.v1, t.v2)), default=0.0) - origin[1]
    return (
        max(1, min(cap, int(hix // cell[0]) + 1)),
        max(1, min(cap, int(hiz // cell[1]) + 1)),
    )


def build_hits(
    tris: Iterable[Tri],
    grid: tuple[int, int] | None = None,
    cell: tuple[int, int] = (501, 501),
    origin: tuple[int, int] = (0, 0),
    pad: float = 0.0,
) -> Hits:
    """A chunk over these triangles, its cell lists from the exact overlap."""
    tris = list(tris)
    grid = grid or fit_grid(tris, cell, origin)
    cells = [sorted(c) for c in cell_map(tris, grid, cell, origin, pad)]
    return Hits(grid, cell, origin, cells, tris)
