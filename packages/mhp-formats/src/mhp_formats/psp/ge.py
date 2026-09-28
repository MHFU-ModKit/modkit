# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
# SPDX-FileCopyrightText: 2013 Seth VanHeulen
"""GE display lists: the command words, and the draws a list makes.

Addressing and the address advance after a PRIM follow PPSSPP (`GPUCommon.cpp`, `GPUState.h`).
"""

import struct
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from enum import IntEnum
from functools import lru_cache
from typing import NamedTuple, Self

from .._base import FormatError
from .vtype import VertexType


class Op(IntEnum):
    """The command opcodes (the top byte of a word) the games' lists use, and their kin."""

    NOP = 0x00
    VADDR = 0x01
    IADDR = 0x02
    PRIM = 0x04
    BEZIER = 0x05
    SPLINE = 0x06
    BOUNDINGBOX = 0x07
    JUMP = 0x08
    BJUMP = 0x09
    CALL = 0x0A
    RET = 0x0B
    END = 0x0C
    SIGNAL = 0x0E
    FINISH = 0x0F
    BASE = 0x10
    VTYPE = 0x12
    OFFSETADDR = 0x13
    ORIGIN = 0x14
    FFACE = 0x9B


class Prim(IntEnum):
    POINTS = 0
    LINES = 1
    LINE_STRIP = 2
    TRIANGLES = 3
    TRIANGLE_STRIP = 4
    TRIANGLE_FAN = 5
    RECTANGLES = 6


class Command(NamedTuple):
    """One word: the opcode and its 24-bit argument."""

    op: int
    arg: int = 0

    @classmethod
    def from_word(cls, word: int) -> Self:
        return cls(word >> 24, word & 0xFFFFFF)

    @property
    def word(self) -> int:
        if not (0 <= self.op < 1 << 8 and 0 <= self.arg < 1 << 24):
            raise ValueError(f"not a command: op {self.op:#x}, arg {self.arg:#x}")
        return self.op << 24 | self.arg

    @classmethod
    def prim(cls, prim: int, count: int) -> Self:
        return cls(Op.PRIM, prim << 16 | count)


_END = (Op.RET, Op.END)
_UNFOLLOWED = (Op.JUMP, Op.BJUMP, Op.CALL, Op.BEZIER, Op.SPLINE)


@dataclass(slots=True)
class Draw:
    """What one PRIM draws. Vertex `i` of `indices` sits at `vertex_addr + i * vtype.stride`."""

    command: int
    """Index of the PRIM in its list."""
    prim: int
    count: int
    vtype: VertexType
    vertex_addr: int
    index_addr: int | None
    """`None` when the vertex type has no index format."""
    indices: tuple[int, ...]
    face_order: int
    """FFACE bit 0 in force: 1 reverses the winding of every triangle."""

    @property
    def vertex_count(self) -> int:
        """Vertices this draw reads from `vertex_addr`."""
        return max(self.indices) + 1 if self.indices else 0

    def triangles(self) -> list[tuple[int, int, int]]:
        return triangles(self.prim, self.indices, self.face_order)


@dataclass
class DisplayList:
    """The commands from a list's start up to and including its RET or END."""

    commands: list[Command]

    @classmethod
    def from_bytes(cls, data: bytes, offset: int = 0) -> Self:
        if not 0 <= offset <= len(data):
            raise FormatError(f"no display list at {offset:#x} in {len(data):#x} bytes")
        end = offset + (len(data) - offset) // 4 * 4
        commands = []
        for (word,) in struct.iter_unpack("<I", memoryview(data)[offset:end]):
            commands.append(Command.from_word(word))
            if word >> 24 in _END:
                return cls(commands)
        raise FormatError(f"the display list at {offset:#x} has no RET or END")

    def to_bytes(self) -> bytes:
        return struct.pack(f"<{len(self.commands)}I", *(c.word for c in self.commands))

    def draws(self, mem: bytes, base: int) -> Iterator[Draw]:
        """Walk the list as the GE would, sitting at `base` in `mem` (which ORIGIN reads).

        Indices are read from `mem`; vertices are not. Jumps, calls and patches are refused.
        """
        offset_addr = base_reg = face_order = 0
        vaddr: int | None = None
        iaddr: int | None = None
        vtype: VertexType | None = None
        for i, (op, arg) in enumerate(self.commands):
            if op == Op.PRIM:
                if vtype is None or vaddr is None:
                    raise FormatError(f"PRIM at command {i} before VTYPE and VADDR")
                prim, count = arg >> 16 & 7, arg & 0xFFFF
                if vtype.index:
                    if iaddr is None:
                        raise FormatError(f"indexed PRIM at command {i} before IADDR")
                    indices = _read_indices(mem, iaddr, count, vtype.index_code)
                    yield Draw(i, prim, count, vtype, vaddr, iaddr, indices, face_order)
                    iaddr += count * vtype.index_size
                else:
                    yield Draw(i, prim, count, vtype, vaddr, None, tuple(range(count)), face_order)
                    vaddr += count * vtype.stride
            elif op == Op.VADDR:
                vaddr = _address(offset_addr, base_reg, arg)
            elif op == Op.IADDR:
                iaddr = _address(offset_addr, base_reg, arg)
            elif op == Op.VTYPE:
                vtype = _vtype(arg)
            elif op == Op.FFACE:
                face_order = arg & 1
            elif op == Op.BASE:
                base_reg = arg
            elif op == Op.OFFSETADDR:
                offset_addr = arg << 8
            elif op == Op.ORIGIN:
                offset_addr = base + 4 * i
            elif op in _UNFOLLOWED:
                raise FormatError(f"command {i} is {Op(op).name}, which the walker does not follow")


@lru_cache(maxsize=256)
def _vtype(arg: int) -> VertexType:
    return VertexType.from_word(arg)


def _address(offset_addr: int, base_reg: int, arg: int) -> int:
    return (offset_addr + ((base_reg & 0x0F0000) << 8 | arg)) & 0x0FFFFFFF


def _read_indices(mem: bytes, addr: int, count: int, code: str) -> tuple[int, ...]:
    try:
        return struct.unpack_from(f"<{count}{code}", mem, addr)
    except struct.error as e:
        raise FormatError(f"{count} indices at {addr:#x} run past {len(mem):#x}") from e


def triangles(prim: int, indices: Sequence[int], face_order: int = 0) -> list[tuple[int, int, int]]:
    """The triangles a primitive draws, wound for FFACE 0 (reversed for `face_order` 1).

    A strip alternates winding from one triangle to the next; a list and a fan do not.
    Points, lines and rectangles draw none. Degenerate triangles are kept.
    """
    n = len(indices)
    swap = face_order & 1
    if prim == Prim.TRIANGLES:
        return [
            (indices[k + 1], indices[k], indices[k + 2])
            if swap
            else (indices[k], indices[k + 1], indices[k + 2])
            for k in range(0, n - 2, 3)
        ]
    if prim == Prim.TRIANGLE_STRIP:
        return [
            (indices[k + 1], indices[k], indices[k + 2])
            if (k + swap) & 1
            else (indices[k], indices[k + 1], indices[k + 2])
            for k in range(n - 2)
        ]
    if prim == Prim.TRIANGLE_FAN:
        return [
            (indices[k + 1], indices[0], indices[k + 2])
            if swap
            else (indices[0], indices[k + 1], indices[k + 2])
            for k in range(n - 2)
        ]
    if 0 <= prim <= Prim.RECTANGLES:
        return []
    raise FormatError(f"no primitive type {prim}")
