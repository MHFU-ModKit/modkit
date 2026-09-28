# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
# SPDX-FileCopyrightText: 2013 Seth VanHeulen
"""TMH, the texture bank of both games: a list of images, each GE pixel data plus, for an indexed
format, its own CLUT. Colours decode the way PPSSPP's GE texture decoder does."""

from __future__ import annotations

import sys
from array import array
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Self

from construct import (
    Array,
    Bytes,
    Check,
    Const,
    ConstructError,
    GreedyBytes,
    If,
    Int16ul,
    Int32ul,
    OneOf,
    Rebuild,
    Struct,
    len_,
    this,
)

from ._base import FormatError
from .psp.color import Color, Rgba, from_rgba, to_rgba
from .psp.swizzle import BLOCK_ROWS, swizzle, unswizzle

MAGIC = b".TMH0.14"


# GE texture formats: 0-3 direct 5650/5551/4444/8888, 4-7 CLUT indices, 8-10 DXT1/DXT3/DXT5.
# A CLUT is stored in one of the four direct formats.
_BPP = (16, 16, 16, 32, 4, 8, 16, 32, 4, 8, 8)
_DXT1 = 8


def _whole_entries(mode: int, data: bytes) -> bool:
    return len(data) * 8 % _BPP[mode] == 0


def _entries(mode: int, data: bytes) -> int:
    return len(data) * 8 // _BPP[mode]


def _image_size(c: Any) -> int:
    clut = c.clut
    return 32 + len(c.pixels) + (16 + len(clut["data"]) if clut is not None else 0)


_CLUT = Struct(
    "size" / Rebuild(Int32ul, lambda c: 16 + len(c.data)),
    Const(2, Int32ul),  # block tag: CLUT
    "mode" / OneOf(Int32ul, range(4)),
    "entries" / Rebuild(Int32ul, lambda c: _entries(c.mode, c.data)),
    "data" / Bytes(this.size - 16),
    Check(lambda c: _whole_entries(c.mode, c.data) and c.entries == _entries(c.mode, c.data)),
)
_IMAGE = Struct(
    "size" / Rebuild(Int32ul, _image_size),
    "reserved" / Int32ul,
    Const(1, Int32ul),  # 1 in every shipped image
    "has_clut" / Rebuild(OneOf(Int32ul, (0, 1)), lambda c: int(c.clut is not None)),
    "pixel_size" / Rebuild(Int32ul, lambda c: 16 + len(c.pixels)),
    Const(1, Int32ul),  # block tag: pixels
    "mode" / OneOf(Int32ul, range(len(_BPP))),
    "width" / Int16ul,
    "height" / Int16ul,
    "pixels" / Bytes(this.pixel_size - 16),
    "clut" / If(this.has_clut == 1, _CLUT),
    Check(lambda c: c.size == _image_size(c)),
)
_BANK = Struct(
    Const(MAGIC),
    "count" / Rebuild(Int32ul, len_(this.images)),
    "reserved" / Int32ul,
    "images" / Array(this.count, _IMAGE),
    "tail" / GreedyBytes,
)


@dataclass
class Clut:
    """A palette of `entries` colours in GE format `mode` (0-3)."""

    mode: int
    data: bytes

    @property
    def entries(self) -> int:
        return _entries(self.mode, self.data)

    def colours(self) -> list[Rgba]:
        c = to_rgba(self.data, Color(self.mode))
        return [(c[i], c[i + 1], c[i + 2], c[i + 3]) for i in range(0, len(c), 4)]


@dataclass
class TmhImage:
    mode: int
    """GE texture format, 0-10."""
    width: int
    height: int
    pixels: bytes
    """As stored: swizzled when `swizzled`, and possibly padded past the image."""
    clut: Clut | None = None
    reserved: int = 0
    """Image header +0x04, 0 in every shipped image."""

    @property
    def swizzled(self) -> bool:
        """The bank has no swizzle flag: an image is swizzled exactly when its rows are whole
        16-byte blocks, which every shipped image bears out."""
        return self.mode < _DXT1 and self.width > 0 and self.width * _bpp(self.mode) % 128 == 0

    def palette_colours(self, palette: int = 0) -> list[Rgba]:
        """The colours of one palette; a CLUT may hold several of 16 or 256, one after another."""
        c = self._palette(palette)
        return [(c[i], c[i + 1], c[i + 2], c[i + 3]) for i in range(0, len(c), 4)]

    def decode(self, palette: int = 0) -> bytes:
        """RGBA8, `width * height * 4` bytes, top row first, through CLUT palette `palette`."""
        need = self._stored_size()
        if self.mode >= _DXT1:
            return self._dxt(need)
        stream = self._unswizzled()[:need] if self.swizzled else self.pixels[:need]
        if self.mode < 4:
            return to_rgba(stream, Color(self.mode))
        indices = _indices(self.mode, stream)[: self.width * self.height]
        return _lookup(indices, self._palette(palette))

    def encode(
        self, rgba: bytes, colours: Sequence[Rgba] | None = None, palette: int = 0
    ) -> TmhImage:
        """This image with its pixels replaced from RGBA8, at its own mode and every size kept.

        An indexed image is quantised to a median-cut palette of its own, or mapped onto
        `colours`, and that palette is written over CLUT palette `palette`."""
        self._stored_size()
        if len(rgba) != 4 * self.width * self.height:
            raise ValueError(f"{self.width}x{self.height} takes {4 * self.width * self.height} B")
        if self.mode >= _DXT1:
            raise ValueError("DXT is not encoded")
        clut = self.clut
        if self.mode < 4:
            stream = from_rgba(rgba, Color(self.mode))
        else:
            n = len(self._palette(palette)) // 4
            assert clut is not None  # _palette raised otherwise
            indices, pal = quantize(rgba, n, colours)
            stream = _pack_indices(self.mode, indices)
            size = _BPP[clut.mode] // 8
            start, end = palette * n * size, (palette + 1) * n * size
            packed = from_rgba(bytes(v for c in pal for v in c), Color(clut.mode))
            clut = Clut(clut.mode, clut.data[:start] + packed + clut.data[end:])
        if self.swizzled:
            # bytes a short last band stores past the image stay as they were
            pitch = self.width * _BPP[self.mode] // 8
            stream = swizzle(stream + self._unswizzled()[len(stream) :], pitch)
        pixels = stream[: len(self.pixels)] + self.pixels[len(stream) :]
        return replace(self, pixels=pixels, clut=clut)

    def _palette(self, palette: int) -> bytes:
        """One palette of the CLUT as RGBA8."""
        if self.clut is None:
            raise FormatError(f"format {self.mode} has no CLUT to index")
        n = 1 << _BPP[self.mode] if self.mode in (4, 5) else self.clut.entries
        if palette < 0 or palette * n >= self.clut.entries:
            raise ValueError(f"the image has no palette {palette}")
        return to_rgba(self.clut.data, Color(self.clut.mode))[
            palette * n * 4 : (palette + 1) * n * 4
        ]

    def _stored_size(self) -> int:
        """Bytes of `pixels` the image needs; a swizzled image may stop inside its last band."""
        bpp = _bpp(self.mode)
        if self.mode >= _DXT1:
            need = -(-self.width // 4) * -(-self.height // 4) * 2 * bpp
        else:
            need = -(-self.width * self.height * bpp // 8)
        if len(self.pixels) < need:
            raise FormatError(f"{len(self.pixels)} pixel bytes, the image needs {need}")
        return need

    def _unswizzled(self) -> bytes:
        """The stored blocks as rows, in whole bands: the GE reads a short last band on past the
        data, into what is not the image's, and that reads as zeros here."""
        pitch = self.width * _BPP[self.mode] // 8
        size = pitch * _round8(self.height)
        return unswizzle(self.pixels[:size].ljust(size, b"\0"), pitch)

    def _dxt(self, need: int) -> bytes:
        if self.mode != _DXT1:
            raise NotImplementedError("DXT3 and DXT5 are not decoded: neither game ships them")
        across = -(-self.width // 4)
        pitch = across * 16
        out = bytearray(pitch * 4 * -(-self.height // 4))
        for at in range(0, need, 8):
            colours = _dxt1_colours(self.pixels, at)
            down, left = divmod(at // 8, across)
            for y in range(4):
                bits = self.pixels[at + y]
                row = (down * 4 + y) * pitch + left * 16
                out[row : row + 16] = b"".join(colours[bits >> s & 3] for s in (0, 2, 4, 6))
        return b"".join(out[y * pitch : y * pitch + self.width * 4] for y in range(self.height))


@dataclass
class Tmh:
    images: list[TmhImage] = field(default_factory=list)
    reserved: int = 0
    """Bank header +0x0C, 0 in every shipped bank."""
    tail: bytes = b""
    """Bytes after the last image: a standalone bank runs on to the end of its 2048-byte sector."""

    @staticmethod
    def sniff(data: bytes) -> bool:
        return data[:8] == MAGIC

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        try:
            bank = _BANK.parse(data)
        except ConstructError as e:
            raise FormatError(f"not a TMH bank: {e}") from e
        images = [
            TmhImage(
                i.mode,
                i.width,
                i.height,
                i.pixels,
                Clut(i.clut.mode, i.clut.data) if i.clut is not None else None,
                i.reserved,
            )
            for i in bank.images
        ]
        return cls(images, bank.reserved, bank.tail)

    def to_bytes(self) -> bytes:
        images = [
            {
                "reserved": i.reserved,
                "mode": i.mode,
                "width": i.width,
                "height": i.height,
                "pixels": i.pixels,
                "clut": {"mode": i.clut.mode, "data": i.clut.data} if i.clut else None,
            }
            for i in self.images
        ]
        try:
            return _BANK.build({"reserved": self.reserved, "images": images, "tail": self.tail})
        except ConstructError as e:
            raise FormatError(f"not a TMH bank: {e}") from e


def quantize(
    rgba: bytes, n: int, colours: Sequence[Rgba] | None = None
) -> tuple[list[int], list[Rgba]]:
    """RGBA8 -> (an index per pixel, `n` colours).

    Without `colours`, a median cut over the distinct colours weighted by how often each occurs,
    so a mostly flat texture does not spend half its palette on one colour."""
    if len(rgba) % 4:
        raise ValueError("RGBA8 comes in 4-byte pixels")
    words = memoryview(rgba).cast("I")
    hist = Counter(words)
    rgbas = {w: _word_rgba(w) for w in hist}
    if colours is None:
        colours = _median_cut([(rgbas[w], k) for w, k in hist.items()], n)
    palette = [(int(c[0]), int(c[1]), int(c[2]), int(c[3])) for c in colours]
    if len(palette) > n:
        raise ValueError(f"{len(palette)} colours, the mode holds {n}")
    exact = {c: i for i, c in enumerate(palette)}
    # matched once per distinct colour, not per pixel
    nearest = {w: exact[c] if c in exact else _nearest(palette, c) for w, c in rgbas.items()}
    return list(map(nearest.__getitem__, words)), palette + [(0, 0, 0, 0)] * (n - len(palette))


def _nearest(palette: list[Rgba], c: Rgba) -> int:
    return min(
        range(len(palette)),
        key=lambda i: sum((p - v) ** 2 for p, v in zip(palette[i], c, strict=True)),
    )


def _median_cut(weighted: list[tuple[Rgba, int]], n: int) -> list[Rgba]:
    if len(weighted) <= n:
        return sorted(c for c, _ in weighted)
    boxes = [weighted]
    while len(boxes) < n:
        spreads = [_spread(b) if len(b) > 1 else (-1, 0) for b in boxes]
        widest = max(range(len(boxes)), key=lambda i: spreads[i][0])
        if spreads[widest][0] < 0:
            break
        channel = spreads[widest][1]
        box = sorted(boxes.pop(widest), key=lambda t: t[0][channel])
        total, acc, cut = sum(k for _, k in box), 0, 1
        for j, (_, k) in enumerate(box):
            acc += k
            if acc * 2 >= total:
                cut = max(1, min(j + 1, len(box) - 1))
                break
        boxes += [box[:cut], box[cut:]]
    out = []
    for box in boxes:
        total = sum(k for _, k in box)
        r, g, b, a = (round(sum(c[j] * k for c, k in box) / total) for j in range(4))
        out.append((r, g, b, a))
    return out


def _spread(box: list[tuple[Rgba, int]]) -> tuple[int, int]:
    """(widest channel range, that channel)."""
    ranges = [max(c[j] for c, _ in box) - min(c[j] for c, _ in box) for j in range(4)]
    widest = max(range(4), key=ranges.__getitem__)
    return ranges[widest], widest


def _bpp(mode: int) -> int:
    if not 0 <= mode < len(_BPP):
        raise FormatError(f"texture format {mode} is not a GE format")
    return _BPP[mode]


def _round8(rows: int) -> int:
    return -(-rows // BLOCK_ROWS) * BLOCK_ROWS


def _word_rgba(w: int) -> Rgba:
    b = w.to_bytes(4, sys.byteorder)
    return (b[0], b[1], b[2], b[3])


def _le(typecode: str, data: bytes | list[int]) -> array[int]:
    """A little-endian array: `data` as bytes to read, as ints to write."""
    a = array(typecode, data)
    if sys.byteorder == "big":
        a.byteswap()
    return a


_LOW = bytes(v & 15 for v in range(256))
_HIGH = bytes(v >> 4 for v in range(256))


def _indices(mode: int, stream: bytes) -> bytes | array[int]:
    if mode == 4:
        out = bytearray(2 * len(stream))
        out[0::2] = stream.translate(_LOW)
        out[1::2] = stream.translate(_HIGH)
        return bytes(out)
    if mode == 5:
        return stream
    return _le("H" if mode == 6 else "I", stream)


def _pack_indices(mode: int, indices: list[int]) -> bytes:
    if mode == 4:
        low = bytes(indices[0::2])
        high = bytes(indices[1::2]).ljust(len(low), b"\0")
        return bytes(a | b << 4 for a, b in zip(low, high, strict=True))
    if mode == 5:
        return bytes(indices)
    return _le("H" if mode == 6 else "I", indices).tobytes()


def _lookup(indices: bytes | array[int], palette: bytes) -> bytes:
    """Palette indices -> RGBA8, `palette` being RGBA8 too."""
    if indices and max(indices) >= len(palette) // 4:
        raise FormatError(f"a pixel indexes colour {max(indices)} of {len(palette) // 4}")
    if isinstance(indices, bytes):
        out = bytearray(4 * len(indices))
        for c in range(4):
            out[c::4] = indices.translate(palette[c::4].ljust(256, b"\0"))
        return bytes(out)
    return b"".join(palette[4 * i : 4 * i + 4] for i in indices)


def _dxt1_colours(data: bytes, at: int) -> list[bytes]:
    """A DXT1 block's four colours. The PSP stores the index lines first, then the two ends, and
    widens the ends by a plain shift, not by bit replication."""
    ends = [int.from_bytes(data[at + k : at + k + 2], "little") for k in (4, 6)]
    e1, e2 = ([c >> 8 & 0xF8, c >> 3 & 0xFC, c << 3 & 0xF8] for c in ends)
    if ends[0] > ends[1]:
        c3 = bytes((2 * a + b) // 3 for a, b in zip(e1, e2, strict=True)) + b"\xff"
        c4 = bytes((a + 2 * b) // 3 for a, b in zip(e1, e2, strict=True)) + b"\xff"
    else:
        c3 = bytes((a + b) // 2 for a, b in zip(e1, e2, strict=True)) + b"\xff"
        c4 = bytes(4)
    return [bytes(e1) + b"\xff", bytes(e2) + b"\xff", c3, c4]
