# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The container ('MHAI') an assembled AI script travels in, and the maps across it.

    box = Container.read(container(prog, img, load))
    box.rebase(native_address)       # where the native address is in the image
    box.native(image_address)        # and back

The layout is in docs/formats/ai_script.md; the framework loads the same bytes from the memory
stick.
"""

from __future__ import annotations

import hashlib
import struct
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum
from functools import cached_property

from . import asm, ir

MAGIC = b"MHAI"
VERSION = 1
HEAD = 0x40
IMAGE_OFF = HEAD
"""Where the image starts in a container."""

_ALIGN = 8
_HEAD = struct.Struct("<4sHH12I8x")
_ENTRY = (4, 8, 8, 16)
"""Bytes of a reloc, patch, origin and item entry."""


class ContainerError(ValueError):
    """Bytes that are no container this version reads."""


class Kind(IntEnum):
    """What an item of the container is."""

    ROUTINE = 0
    TABLE = 1
    DATA = 2
    HEADER = 3
    BLOB = 4


@dataclass(frozen=True)
class Item:
    """An item of the native region: where it was, where it is in the image and its size there."""

    native: int
    offset: int
    size: int
    kind: Kind


Span = tuple[int, int]


@dataclass(frozen=True)
class Head:
    """The container's first 0x40 bytes: who, where it loads and where each section is."""

    row: int
    id: int
    load: int
    image: Span
    """Offset and size."""
    relocs: Span
    """Offset and count; the same for each section after it."""
    patches: Span
    origin: Span
    items: Span

    @classmethod
    def parse(cls, data: bytes) -> Head:
        if len(data) < HEAD:
            raise ContainerError(f"{len(data)} bytes are shorter than the {HEAD}-byte header")
        magic, version, row, id_, load, *flat = _HEAD.unpack_from(data)
        if magic != MAGIC:
            raise ContainerError(f"it starts with {magic!r}, not {MAGIC!r}")
        if version != VERSION:
            raise ContainerError(f"it is version {version}, this reads {VERSION}")
        image, relocs, patches, origin, items = zip(flat[::2], flat[1::2], strict=True)
        return cls(row, id_, load, image, relocs, patches, origin, items)

    @property
    def extent(self) -> int:
        """The bytes the container spans, whole sections and 8-aligned."""
        spans = (self.relocs, self.patches, self.origin, self.items)
        ends = [off + n * size for (off, n), size in zip(spans, _ENTRY, strict=True)]
        end = max(HEAD, self.image[0] + self.image[1], *ends)
        return -(-end // _ALIGN) * _ALIGN

    @property
    def addresses(self) -> range:
        """Where the image sits."""
        return range(self.load, self.load + self.image[1])


@dataclass(frozen=True)
class Container:
    """A container read back; `rebase` and `native` map addresses across it. Addresses of the
    image are `load` plus an offset, so a stick container (load 0) answers in offsets."""

    head: Head
    image: bytes
    relocs: tuple[int, ...]
    patches: tuple[tuple[int, int], ...]
    """Each header's native address and image offset."""
    origin: tuple[tuple[int, int], ...]
    """Native addresses of instructions, markers and block ends, with their image offsets."""
    items: tuple[Item, ...]

    @classmethod
    def read(cls, data: bytes) -> Container:
        """Parse and check `data`: header, bounds, and that the id is the image's."""
        head = Head.parse(data)
        if head.extent > len(data):
            raise ContainerError(f"it needs {head.extent} bytes and {len(data)} are there")
        off, size = head.image
        image = bytes(data[off : off + size])

        def table(span: Span, fmt: str) -> list[tuple[int, ...]]:
            step = struct.Struct("<" + fmt)
            return [step.unpack_from(data, span[0] + k * step.size) for k in range(span[1])]

        relocs = tuple(w for (w,) in table(head.relocs, "I"))
        patches = tuple((n, o) for n, o in table(head.patches, "2I"))
        origin = tuple((n, o) for n, o in table(head.origin, "2I"))
        try:
            items = tuple(Item(n, o, s, Kind(k)) for n, o, s, k in table(head.items, "4I"))
        except ValueError as e:
            raise ContainerError(f"an item has an unknown kind ({e})") from None
        box = Container(head, image, relocs, patches, origin, items)
        box._check()
        return box

    def _check(self) -> None:
        size = len(self.image)
        if any(r + 4 > size for r in self.relocs):
            raise ContainerError("a relocation lies outside the image")
        if any(o > size for _, o in (*self.patches, *self.origin)):
            raise ContainerError("an offset lies outside the image")
        if any(i.offset + i.size > size for i in self.items):
            raise ContainerError("an item lies outside the image")
        if _id(self._moved(0)) != self.head.id:
            raise ContainerError("its id is not the image's")

    def _moved(self, load: int) -> bytes:
        """The image with its pointer words written for `load`."""
        img = asm.Image(self.image, self.head.load, list(self.relocs), {}, {}, {})
        return img.at(load)

    @property
    def row(self) -> int:
        return self.head.row

    @property
    def load(self) -> int:
        return self.head.load

    @cached_property
    def headers(self) -> dict[int, int]:
        """Each header's native address and its address in the image."""
        return {n: self.load + o for n, o in self.patches}

    @cached_property
    def _forward(self) -> dict[int, int]:
        return {**dict(self.patches), **dict(self.origin)}

    @cached_property
    def _backward(self) -> dict[int, int]:
        out: dict[int, int] = {}
        for native, off in sorted((*self.patches, *self.origin)):
            out.setdefault(off, native)
        return out

    @cached_property
    def _spans(self) -> tuple[list[Item], list[int], list[Item], list[int]]:
        """The tables and data by native address and by image offset, each with its keys."""
        kept = [i for i in self.items if i.kind in (Kind.TABLE, Kind.DATA)]
        by_native = sorted(kept, key=lambda i: i.native)
        by_offset = sorted(kept, key=lambda i: i.offset)
        return by_native, [i.native for i in by_native], by_offset, [i.offset for i in by_offset]

    def rebase(self, addr: int) -> int | None:
        """The image address of the native `addr`: an instruction, marker or block end, a header,
        or a byte inside a table or data; None for anything else."""
        off = self._forward.get(addr)
        if off is None:
            held, keys, _, _ = self._spans
            k = bisect_right(keys, addr) - 1
            if k >= 0 and addr - held[k].native < held[k].size:
                off = held[k].offset + addr - held[k].native
        return None if off is None else self.load + off

    def native(self, addr: int) -> int | None:
        """The native address of the image address `addr`, the inverse of `rebase`; the lowest
        when several map to one place."""
        off = addr - self.load
        if not 0 <= off <= len(self.image):
            return None
        if off in self._backward:
            return self._backward[off]
        _, _, held, keys = self._spans
        k = bisect_right(keys, off) - 1
        if k >= 0 and off - held[k].offset < held[k].size:
            return held[k].native + off - held[k].offset
        return None

    def entry(self, header: int) -> int | None:
        """The routine in slot 0 of the base list of the image header at `header`."""
        lst = self._pointer(header)
        return None if lst is None else self._pointer(lst)

    def _pointer(self, addr: int) -> int | None:
        """The image address in the word at `addr`; None for an empty or external word."""
        off = addr - self.load
        if off not in self._moving:
            return None
        return int.from_bytes(self.image[off : off + 4], "little")

    @cached_property
    def _moving(self) -> frozenset[int]:
        return frozenset(self.relocs)

    def place(self, load: int) -> bytes:
        """These bytes written for an image at `load`."""
        return _pack(
            self.row,
            self.head.id,
            load,
            self._moved(load),
            self.relocs,
            self.patches,
            self.origin,
            self.items,
        )


def _id(image: bytes) -> int:
    """The first 4 bytes of the SHA-1 of an image written for base 0."""
    return int.from_bytes(hashlib.sha1(image).digest()[:4], "little")


def _pack(
    row: int,
    id_: int,
    load: int,
    image: bytes,
    relocs: Sequence[int],
    patches: Sequence[tuple[int, int]],
    origin: Sequence[tuple[int, int]],
    items: Sequence[Item],
) -> bytes:
    out = bytearray(HEAD)
    spans = [HEAD, len(image)]
    out += image
    sections = (
        (len(relocs), struct.pack(f"<{len(relocs)}I", *relocs)),
        (len(patches), b"".join(struct.pack("<2I", *e) for e in patches)),
        (len(origin), b"".join(struct.pack("<2I", *e) for e in origin)),
        (
            len(items),
            b"".join(struct.pack("<4I", i.native, i.offset, i.size, i.kind) for i in items),
        ),
    )
    for count, raw in sections:
        out += bytes(-len(out) % _ALIGN)
        spans += [len(out), count]
        out += raw
    out += bytes(-len(out) % _ALIGN)
    _HEAD.pack_into(out, 0, MAGIC, VERSION, row, id_, load, *spans)
    return bytes(out)


def _items(p: ir.Program, img: asm.Image) -> list[Item]:
    """The native items of `p`, by native address."""
    out = []
    for it in p.items:
        if it.va is None:
            continue
        if isinstance(it, ir.Blob):  # unnamed and never moved: it stays where it was lifted
            off, size, kind = it.va - p.base, len(it.data), Kind.BLOB
            if img.data[off : off + size] != it.data:
                raise ValueError(f"the blob at 0x{it.va:08X} is not where it was lifted")
        else:
            off, size = img.items[it.name]
            kind = {
                ir.Routine: Kind.ROUTINE,
                ir.Table: Kind.TABLE,
                ir.Data: Kind.DATA,
                ir.Header: Kind.HEADER,
            }[type(it)]
        out.append(Item(it.va, off, size, kind))
    return sorted(out, key=lambda i: i.native)


def container(p: ir.Program, img: asm.Image, load: int = 0, origin: bool = True) -> bytes:
    """The container of an assembled program, its image written for `load`. A stick container
    (`load` 0, `origin` False) leaves out the maps a live push rebinds monsters with."""
    return _pack(
        p.row,
        _id(img.at(0)),
        load,
        img.at(load),
        img.relocs,
        sorted(img.headers.items()),
        sorted(img.origin.items()) if origin else (),
        _items(p, img) if origin else (),
    )
