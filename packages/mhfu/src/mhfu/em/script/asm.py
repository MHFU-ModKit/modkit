# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Encode a Program back into bytes.

    image = assemble(prog)              # at the native base: the bytes the game has
    moved = image.at(0x00100000)        # the same bytes placed elsewhere, pointers rebased

Every item with a native address is laid out where it was; items without one follow. An item
that outgrew its place is an error, and an unresolved `Sym` is too: both belong to the edit pass.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from . import ir
from .opcodes import CLOSE, COUNT, COUNTED, derived_count, marker_sub, operands

_ALIGN = 8


class AssembleError(ValueError):
    """A program that cannot be encoded as it stands."""


@dataclass
class Image:
    """A program's bytes, written for `base`."""

    data: bytes
    base: int
    relocs: list[int]
    """The offsets of the pointer words that move with the image, each a u32 address in it."""
    headers: dict[int, int]
    """The offset of each header, by its native address."""
    origin: dict[int, int]
    """The offset of every native address an instruction, marker or block end sits at, so a live
    address (a resume or return slot) can be rebased."""
    nodes: dict[str, tuple[int, int]]
    """Offset and length of the bytes of each node id: an instruction, a block's opener, a
    marker."""
    items: dict[str, tuple[int, int]] = field(default_factory=dict)
    """Offset and length of each named item."""

    def at(self, base: int) -> bytes:
        """The bytes placed at `base`: header and list words rebased, External words kept."""
        out = bytearray(self.data)
        delta = base - self.base
        for off in self.relocs:
            word = int.from_bytes(out[off : off + 4], "little") + delta
            out[off : off + 4] = (word & 0xFFFF_FFFF).to_bytes(4, "little")
        return bytes(out)


@dataclass
class _Piece:
    """One item's bytes, its pointer words and the native addresses inside it."""

    data: bytearray = field(default_factory=bytearray)
    fixups: list[tuple[int, str]] = field(default_factory=list)
    """Offset in the piece of a word that holds the address of the named item."""
    starts: list[tuple[int, int]] = field(default_factory=list)
    """A native address where an instruction starts and its offset in the piece."""
    ends: list[tuple[int, int]] = field(default_factory=list)
    """The same for where one ends."""
    nodes: dict[str, tuple[int, int]] = field(default_factory=dict)
    labels: dict[str, int] = field(default_factory=dict)


def _bytes(
    what: str,
    layout: str,
    args: Mapping[str, ir.Value],
    derived: Mapping[str, int] | None = None,
) -> bytes:
    """The operands of `layout` from `args`, in order; `derived` supplies the ones the tree
    implies, which `args` must leave out."""
    derived = derived or {}
    ops = operands(layout)
    names = [o.name for o in ops if o.name not in derived]
    if set(args) != set(names):
        raise AssembleError(f"{what} takes {names}, not {sorted(args)}")
    out = bytearray()
    for o in ops:
        v = derived[o.name] if o.name in derived else args[o.name]
        if isinstance(v, ir.Sym):
            raise AssembleError(
                f"{what}: {o.name} is the label {v.label!r}, and labels are not resolved yet"
            )
        try:
            out += v.to_bytes(o.size, "big", signed=o.signed)
        except OverflowError:
            raise AssembleError(f"{what}: {o.name} {v} does not fit {o.size} byte(s)") from None
    return bytes(out)


class _Routine:
    """The bytes of a routine's nodes."""

    def __init__(self) -> None:
        self.piece = _Piece()

    def emit(self, raw: bytes, pc: int | None, nid: str = "") -> None:
        at = len(self.piece.data)
        self.piece.data += raw
        if pc is not None:
            self.piece.starts.append((pc, at))
            self.piece.ends.append((pc + len(raw), at + len(raw)))
        if nid:
            self.piece.nodes[nid] = (at, len(raw))

    def nodes(self, body: list[ir.Node]) -> None:
        for n in body:
            if isinstance(n, ir.Insn):
                self.insn(n)
            else:
                self.block(n)

    def insn(self, n: ir.Insn) -> None:
        op = n.op
        if op.coded:
            raise AssembleError(f"{op.name} opens a block, which a Block holds")
        raw = bytes([op.code]) + _bytes(op.name, op.plain_layout(n.args), n.args)
        self.emit(raw, n.pc, n.id)

    def block(self, b: ir.Block) -> None:
        op, shape = b.op, b.op.shape
        if not op.coded:
            raise AssembleError(f"{op.name} is a plain instruction, not a block")
        count = derived_count(shape, [a.sub for a in b.arms])
        derived = {COUNT: count} if shape in COUNTED and count is not None else {}
        self.emit(bytes([op.code, 0]) + _bytes(op.name, op.args, b.args, derived), b.pc, b.id)
        self.nodes(b.head)
        for k, arm in enumerate(b.arms):
            sub = marker_sub(shape, arm.sub, k)
            if op.role(sub) != "mid":
                raise AssembleError(f"{op.name} has no marker {sub} to start an arm with")
            what = f"{op.name}.{op.mnemonic(sub).rpartition('.')[2]}"
            self.emit(
                bytes([op.marker, sub]) + _bytes(what, op.layout(sub), arm.args), arm.pc, arm.id
            )
            self.nodes(arm.body)
        self.emit(bytes([op.marker, CLOSE[shape]]), b.close)


def _words(entries: list[ir.Entry]) -> _Piece:
    """The words of a list or a header's fields."""
    piece = _Piece()
    for i, e in enumerate(entries):
        match e:
            case None:
                word = 0
            case ir.External(addr):
                word = addr
            case str(name):
                piece.fixups.append((4 * i, name))
                word = 0
        piece.data += word.to_bytes(4, "little")
    return piece


def _piece(item: ir.Item) -> _Piece:
    match item:
        case ir.Routine():
            enc = _Routine()
            enc.nodes(item.body)
            enc.piece.data += item.tail
            return enc.piece
        case ir.Table():
            return _words(item.slots)
        case ir.Header():
            return _words(item.fields)
        case ir.Data():
            return _Piece(bytearray(item.data), labels=dict(item.labels))
        case ir.Blob():
            return _Piece(bytearray(item.data))


def _name(item: ir.Item) -> str | None:
    return None if isinstance(item, ir.Blob) else item.name


def _layout(p: ir.Program, pieces: list[_Piece]) -> tuple[list[int], int]:
    """The offset of each item and the size of the whole: native items where they were, the
    others after them."""
    native = sorted((it.va - p.base, k) for k, it in enumerate(p.items) if it.va is not None)
    limit = p.end - p.base if p.end else None
    for n, (off, k) in enumerate(native):
        nxt = native[n + 1][0] if n + 1 < len(native) else limit
        size = len(pieces[k].data)
        if off < 0:
            raise AssembleError(f"{_name(p.items[k])} lies before the program's base")
        if nxt is not None and off + size > nxt:
            raise AssembleError(f"{_name(p.items[k])} is {size} bytes and outgrew its place")
    where = {k: off for off, k in native}
    end = max((where[k] + len(pieces[k].data) for k in where), default=0)
    for k, it in enumerate(p.items):
        if it.va is None:
            end = -(-end // _ALIGN) * _ALIGN
            where[k] = end
            end += len(pieces[k].data)
    return [where[k] for k in range(len(p.items))], end


def assemble(p: ir.Program, base: int | None = None) -> Image:
    """The bytes of the program, written for `base` (by default its native one)."""
    base = p.base if base is None else base
    pieces = [_piece(i) for i in p.items]
    offsets, size = _layout(p, pieces)
    data = bytearray(size)
    img = Image(b"", base, [], {}, {}, {})
    names: dict[str, int] = {}

    def claim(name: str, at: int) -> None:
        if name in names:
            raise AssembleError(f"{name} names two things")
        names[name] = at

    for item, piece, off in zip(p.items, pieces, offsets, strict=True):
        data[off : off + len(piece.data)] = piece.data
        name = _name(item)
        if name is not None:
            img.items[name] = (off, len(piece.data))
            claim(name, off)
        for label, at in piece.labels.items():
            if (label, at) != (name, 0):  # a data run's first label may be its own name
                claim(label, off + at)
        img.nodes |= {nid: (off + at, n) for nid, (at, n) in piece.nodes.items()}
    starts: dict[int, int] = {}
    ends: dict[int, int] = {}
    for item, piece, off in zip(p.items, pieces, offsets, strict=True):
        for at, name in piece.fixups:
            if name not in names:
                raise AssembleError(f"nothing is named {name}")
            data[off + at : off + at + 4] = (base + names[name]).to_bytes(4, "little")
            img.relocs.append(off + at)
        if isinstance(item, ir.Header) and item.va is not None:
            img.headers[item.va] = off
        for pc, at in piece.starts:
            starts[pc] = off + at
        for pc, at in piece.ends:
            ends[pc] = off + at
    img.origin = ends | starts
    img.relocs.sort()
    img.data = bytes(data)
    return img
