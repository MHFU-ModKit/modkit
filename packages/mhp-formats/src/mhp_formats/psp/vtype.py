# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The GE vertex type (the VTYPE command) and the vertex buffers it describes.

Layout and normalisation follow PPSSPP's `VertexDecoderCommon.cpp`: attributes in the order
weights, texture, colour, normal, position, each aligned to its component size, the vertex padded
to its largest alignment, and `morph_count` such vertices back to back per vertex.
"""

import struct
from dataclasses import dataclass, field
from functools import cached_property, lru_cache
from itertools import chain
from typing import Self

from .._base import FormatError

NONE, BITS8, BITS16, FLOAT = 0, 1, 2, 3
"""Component format of weights, texture, normal and position (8/16-bit are fixed point)."""
COLOR_5650, COLOR_5551, COLOR_4444, COLOR_8888 = 4, 5, 6, 7
"""Colour formats; 1 to 3 are reserved and take no bytes."""

_SPARE = (1 << 13) | (1 << 17) | (1 << 21) | (1 << 22)
_TEXTURE = {BITS8: "BB", BITS16: "HH", FLOAT: "ff"}
_COLOR = {COLOR_5650: "H", COLOR_5551: "H", COLOR_4444: "H", COLOR_8888: "I"}
_NORMAL = {BITS8: "bbb", BITS16: "hhh", FLOAT: "fff"}
# position 0 is not a format, but PPSSPP still reserves 3 bytes for it (and culls the vertex)
_POSITION = {NONE: "BBB", BITS8: "bbb", BITS16: "hhh", FLOAT: "fff"}
_THROUGH = {NONE: "BBB", BITS8: "bbB", BITS16: "hhH", FLOAT: "fff"}
_WEIGHT = {BITS8: "B", BITS16: "H", FLOAT: "f"}
_INDEX = {BITS8: "B", BITS16: "H", FLOAT: "I"}
_UNIT = {"b": 128.0, "B": 128.0, "h": 32768.0, "H": 32768.0, "f": 1.0}
_ATTRIBUTES = ("weight", "texture", "color", "normal", "position")

Row = tuple[float, ...]
"""One attribute of one vertex: ints for fixed-point formats, floats for float formats."""


def _up(n: int, align: int) -> int:
    return -(-n // align) * align


@dataclass(frozen=True)
class Field:
    """One attribute inside a vertex: a struct code per component, starting at `offset`."""

    offset: int
    codes: str

    @property
    def size(self) -> int:
        return struct.calcsize("<" + self.codes)


@dataclass(frozen=True)
class Layout:
    """Where each attribute sits in one vertex; `None` for an attribute the type lacks."""

    weight: Field | None
    texture: Field | None
    color: Field | None
    normal: Field | None
    position: Field
    size: int
    """One vertex, padded to its largest component alignment (one morph target)."""


@dataclass(frozen=True)
class VertexType:
    """The argument of a VTYPE command. Enum fields hold the raw bit values."""

    texture: int = NONE
    color: int = NONE
    normal: int = NONE
    position: int = BITS16
    weight: int = NONE
    index: int = NONE
    """0 none, 1 u8, 2 u16, 3 u32."""
    weight_count: int = 1
    morph_count: int = 1
    through: bool = False
    """Screen-space vertices: no transform, raw 16-bit positions and texture coordinates."""
    spare: int = 0
    """Argument bits no field uses, kept so `to_word` gives back what was read."""

    def __post_init__(self) -> None:
        enums = (self.texture, self.normal, self.position, self.weight, self.index)
        if not all(0 <= e <= 3 for e in enums) or not 0 <= self.color <= 7:
            raise ValueError(f"vertex type field out of range: {self}")
        if not (1 <= self.weight_count <= 8 and 1 <= self.morph_count <= 8):
            raise ValueError(f"weight and morph counts are 1..8: {self}")
        if self.spare & ~_SPARE:
            raise ValueError(f"spare bits overlap a field: {self.spare:#x}")

    @classmethod
    def from_word(cls, word: int) -> Self:
        """Decode a VTYPE argument (the low 24 bits of the command)."""
        if not 0 <= word < 1 << 24:
            raise FormatError(f"a VTYPE argument has 24 bits, not {word:#x}")
        return cls(
            texture=word & 3,
            color=word >> 2 & 7,
            normal=word >> 5 & 3,
            position=word >> 7 & 3,
            weight=word >> 9 & 3,
            index=word >> 11 & 3,
            weight_count=(word >> 14 & 7) + 1,
            morph_count=(word >> 18 & 7) + 1,
            through=bool(word >> 23 & 1),
            spare=word & _SPARE,
        )

    def to_word(self) -> int:
        return (
            self.texture
            | self.color << 2
            | self.normal << 5
            | self.position << 7
            | self.weight << 9
            | self.index << 11
            | (self.weight_count - 1) << 14
            | (self.morph_count - 1) << 18
            | int(self.through) << 23
            | self.spare
        )

    @cached_property
    def layout(self) -> Layout:
        size = biggest = 0

        def place(codes: str) -> Field:
            nonlocal size, biggest
            align = struct.calcsize("<" + codes[0])
            size = _up(size, align)
            biggest = max(biggest, align)
            placed = Field(size, codes)
            size += placed.size
            return placed

        weight = place(_WEIGHT[self.weight] * self.weight_count) if self.weight else None
        texture = place(_TEXTURE[self.texture]) if self.texture else None
        color = place(_COLOR[self.color]) if self.color in _COLOR else None
        normal = place(_NORMAL[self.normal]) if self.normal else None
        position = place((_THROUGH if self.through else _POSITION)[self.position])
        return Layout(weight, texture, color, normal, position, _up(size, biggest))

    @property
    def stride(self) -> int:
        """Bytes per vertex, all morph targets included: what a PRIM advances VADDR by."""
        return self.layout.size * self.morph_count

    @property
    def index_code(self) -> str:
        """The struct code of one index, `""` when the draws are not indexed."""
        return _INDEX.get(self.index, "")

    @property
    def index_size(self) -> int:
        return (0, 1, 2, 4)[self.index]


@dataclass
class Vertices:
    """A vertex buffer, one row per attribute per vertex; with morphing, `morph_count` rows per
    vertex, targets of a vertex adjacent. An attribute the type lacks is an empty list."""

    vtype: VertexType
    weight: list[Row] = field(default_factory=list)
    texture: list[Row] = field(default_factory=list)
    color: list[int] = field(default_factory=list)
    normal: list[Row] = field(default_factory=list)
    position: list[Row] = field(default_factory=list)
    padding: bytes = b""
    """The alignment bytes of every row in order, or `b""` when they are all zero."""

    @classmethod
    def from_bytes(cls, data: bytes, vtype: VertexType, count: int, offset: int = 0) -> Self:
        """Decode `count` vertices at `offset`."""
        codec = _codec(vtype)
        end = offset + count * vtype.stride
        if count < 0 or offset < 0 or end > len(data):
            raise FormatError(f"{count} vertices at {offset:#x} run past {len(data):#x}")
        region = memoryview(data)[offset:end]
        rows = list(codec.values.iter_unpack(region))
        out = cls(vtype)
        for name, lo, hi in codec.columns:
            if name == "color":
                out.color = [row[lo] for row in rows]
            else:
                setattr(out, name, [row[lo:hi] for row in rows])
        if codec.gaps:
            padding = b"".join(chain.from_iterable(codec.pads.iter_unpack(region)))
            if padding.count(0) != len(padding):
                out.padding = padding
        if codec.floats and out.to_bytes() != region:
            raise FormatError("a float component is a signalling NaN, which Python cannot keep")
        return out

    def to_bytes(self) -> bytes:
        codec = _codec(self.vtype)
        columns: list[list[Row]] = []
        for name, _, _ in codec.columns:
            columns.append([(c,) for c in self.color] if name == "color" else getattr(self, name))
        rows = len(self.position)
        if any(len(column) != rows for column in columns):
            raise ValueError("every attribute of the vertex type needs one row per vertex")
        try:
            out = bytearray().join(
                codec.values.pack(*chain.from_iterable(parts))
                for parts in zip(*columns, strict=True)
            )
        except struct.error as e:
            raise ValueError(f"a row does not fit the vertex type: {e}") from e
        if self.padding:
            per = sum(n for _, n in codec.gaps)
            if len(self.padding) != per * rows:
                raise ValueError(f"padding is {per} bytes a row, not {len(self.padding)}/{rows}")
            at = 0
            for row in range(0, len(out), codec.values.size):
                for start, n in codec.gaps:
                    out[row + start : row + start + n] = self.padding[at : at + n]
                    at += n
        return bytes(out)

    def __len__(self) -> int:
        return len(self.position) // self.vtype.morph_count

    def positions(self, scale: tuple[float, float, float] = (1.0, 1.0, 1.0)) -> list[Row]:
        """Model-space positions times `scale`; in through mode screen x, y and depth."""
        vt = self.vtype
        if vt.position == NONE:
            raise FormatError("the vertex type has no position")
        sx, sy, sz = scale
        if vt.through:
            if vt.position == BITS8:  # the GE reads 8-bit through-mode positions as zero
                return [(0.0, 0.0, 0.0)] * len(self.position)
            return [(x * sx, y * sy, min(max(z, 0.0), 65535.0) * sz) for x, y, z in self.position]
        unit = _UNIT[vt.layout.position.codes[0]]
        sx, sy, sz = sx / unit, sy / unit, sz / unit
        return [(x * sx, y * sy, z * sz) for x, y, z in self.position]

    def normals(self) -> list[Row]:
        return _scaled(self.normal, self.vtype.layout.normal)

    def uvs(self) -> list[Row]:
        """Texture coordinates; through mode keeps 16-bit ones in texels, as the GE does."""
        if self.vtype.through and self.vtype.texture == BITS16:
            return [(float(u), float(v)) for u, v in self.texture]
        return _scaled(self.texture, self.vtype.layout.texture)

    def weights(self) -> list[Row]:
        """Blend weights, 1.0 at the fixed-point unit (the GE allows up to 2.0)."""
        return _scaled(self.weight, self.vtype.layout.weight)

    def colors(self) -> list[tuple[int, int, int, int]]:
        """RGBA, 0..255 each."""
        return [unpack_color(c, self.vtype.color) for c in self.color]


@dataclass(frozen=True)
class _Codec:
    values: struct.Struct
    pads: struct.Struct
    columns: tuple[tuple[str, int, int], ...]
    gaps: tuple[tuple[int, int], ...]
    """(offset, length) of each run of alignment bytes in a row."""
    floats: bool


# struct, not construct: a vertex buffer is a homogeneous run, and the games ship millions
@lru_cache(maxsize=256)
def _codec(vtype: VertexType) -> _Codec:
    layout = vtype.layout
    values = pads = "<"
    columns, gaps = [], []
    at = index = 0
    for name in _ATTRIBUTES:
        fld: Field | None = getattr(layout, name)
        if fld is None:
            continue
        if fld.offset > at:
            gaps.append((at, fld.offset - at))
            values, pads = values + f"{fld.offset - at}x", pads + f"{fld.offset - at}s"
        values, pads = values + fld.codes, pads + f"{fld.size}x"
        columns.append((name, index, index + len(fld.codes)))
        index += len(fld.codes)
        at = fld.offset + fld.size
    if layout.size > at:
        gaps.append((at, layout.size - at))
        values, pads = values + f"{layout.size - at}x", pads + f"{layout.size - at}s"
    floats = "f" in values
    return _Codec(struct.Struct(values), struct.Struct(pads), tuple(columns), tuple(gaps), floats)


def _scaled(rows: list[Row], fld: Field | None) -> list[Row]:
    if fld is None:
        return []
    unit = _UNIT[fld.codes[0]]
    return [tuple(c / unit for c in row) for row in rows]


def unpack_color(value: int, fmt: int) -> tuple[int, int, int, int]:
    """A packed colour of VTYPE colour format `fmt` as RGBA, 0..255 (red in the low bits)."""
    if fmt == COLOR_8888:
        return value & 0xFF, value >> 8 & 0xFF, value >> 16 & 0xFF, value >> 24 & 0xFF
    if fmt == COLOR_5650:
        r, g, b = value & 0x1F, value >> 5 & 0x3F, value >> 11 & 0x1F
        return r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2, 0xFF
    if fmt == COLOR_5551:
        r, g, b = value & 0x1F, value >> 5 & 0x1F, value >> 10 & 0x1F
        return r << 3 | r >> 2, g << 3 | g >> 2, b << 3 | b >> 2, 0xFF * (value >> 15 & 1)
    if fmt == COLOR_4444:
        r, g, b, a = value & 0xF, value >> 4 & 0xF, value >> 8 & 0xF, value >> 12 & 0xF
        return r * 0x11, g * 0x11, b * 0x11, a * 0x11
    raise ValueError(f"no colour format {fmt}")


def pack_color(rgba: tuple[int, int, int, int], fmt: int) -> int:
    """RGBA, 0..255 each, as VTYPE colour format `fmt`: truncates, so it inverts `unpack_color`."""
    r, g, b, a = rgba
    if fmt == COLOR_8888:
        return r | g << 8 | b << 16 | a << 24
    if fmt == COLOR_5650:
        return r >> 3 | g >> 2 << 5 | b >> 3 << 11
    if fmt == COLOR_5551:
        return r >> 3 | g >> 3 << 5 | b >> 3 << 10 | a >> 7 << 15
    if fmt == COLOR_4444:
        return r >> 4 | g >> 4 << 4 | b >> 4 << 8 | a >> 4 << 12
    raise ValueError(f"no colour format {fmt}")
