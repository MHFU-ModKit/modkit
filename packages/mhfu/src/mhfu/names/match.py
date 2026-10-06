# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Which function is which between two builds of one module (the same source, linked at other
addresses): each function's instructions with what relocation changes masked out, aligned in
link order."""

from __future__ import annotations

import bisect
import hashlib
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import cached_property

from rabbitizer import InstrId, Instruction, OperandType

from ..memory import Memory
from ..mips import Code, Pair

MIN_RATIO = 0.5
"""Two functions in the same place of a changed stretch, or called from the same site, are
taken for one above this similarity."""
_IMM, _TARGET = 0xFFFF, 0x03FFFFFF


@dataclass(frozen=True)
class Function:
    start: int
    stop: int
    words: tuple[int, ...]
    """The instructions, relocated fields masked."""

    @cached_property
    def key(self) -> bytes:
        return hashlib.sha1(struct.pack(f"<{len(self.words)}I", *self.words)).digest()


@dataclass(frozen=True)
class Match:
    jp: int
    eu: int
    how: str
    """`exact` (the same code in the same place), `order` (the same place in a stretch that
    changed), `call` (called from the same site of matched code)."""
    ratio: float = 1.0


class Side:
    """One build of a module: its code split into functions."""

    def __init__(self, mem: Memory, text: range) -> None:
        self.code = Code(mem, text)
        starts = _starts(self.code)
        self.functions = tuple(
            Function(a, b, _words(self.code, range(a, b)))
            for a, b in zip(starts, (*starts[1:], text.stop), strict=True)
        )
        self.starts = {f.start: f for f in self.functions}
        self._order = [f.start for f in self.functions]

    @property
    def text(self) -> range:
        return self.code.text

    def at(self, va: int) -> Function | None:
        """The function holding `va`."""
        if va not in self.text:
            return None
        return self.functions[bisect.bisect_right(self._order, va) - 1]

    def calls(self, fn: Function) -> Iterator[tuple[int, int]]:
        """(offset, target) of each jal, and of each j that leaves the function."""
        for ins in self.code.span(range(fn.start, fn.stop)):
            if ins.uniqueId == InstrId.cpu_jal or ins.uniqueId == InstrId.cpu_j:
                target = ins.getInstrIndexAsVram()
                if ins.uniqueId == InstrId.cpu_jal or not fn.start <= target < fn.stop:
                    yield ins.vram - fn.start, target

    def pairs(self, fn: Function) -> dict[tuple[int, int], int]:
        """(lui offset, lo offset) -> the address each lui/lo pair in the function forms."""
        lo = bisect.bisect_left(self._pairs_lo, fn.start)
        hi = bisect.bisect_left(self._pairs_lo, fn.stop)
        return {(p.hi - fn.start, p.lo - fn.start): p.value for p in self._pairs[lo:hi]}

    @cached_property
    def _pairs(self) -> tuple[Pair, ...]:
        return self.code.pairs

    @cached_property
    def _pairs_lo(self) -> list[int]:
        return [p.lo for p in self._pairs]


def align(jp: Side, eu: Side) -> dict[int, Match]:
    """JP function start -> its match: identical code in the same order, then the same place
    in a stretch that changed between two such runs."""
    a, b = jp.functions, eu.functions
    out: dict[int, Match] = {}
    ops = SequenceMatcher(None, [f.key for f in a], [f.key for f in b], autojunk=False)
    for tag, i0, i1, j0, j1 in ops.get_opcodes():
        if tag == "equal":
            for f, g in zip(a[i0:i1], b[j0:j1], strict=True):
                out[f.start] = Match(f.start, g.start, "exact")
        elif tag == "replace" and i1 - i0 == j1 - j0:
            for f, g in zip(a[i0:i1], b[j0:j1], strict=True):
                r = similarity(f, g)
                if r > MIN_RATIO:
                    out[f.start] = Match(f.start, g.start, "order", round(r, 3))
    return out


def similarity(f: Function, g: Function) -> float:
    return SequenceMatcher(None, f.words, g.words, autojunk=False).ratio()


def _ends_flow(ins: Instruction) -> bool:
    return ins.isUnconditionalBranch() or (ins.isJump() and not ins.doesLink())


def _starts(code: Code) -> list[int]:
    """`Code.entries`, and code after a jump or return that nothing branches to: the leaf
    functions only a pointer reaches."""
    starts = set(code.entries)
    labels = code.labels
    ins = list(code)
    for k, i in enumerate(ins):
        if not _ends_flow(i):
            continue
        n = k + 2
        while n < len(ins) and ins[n].isNop():
            n += 1
        if n < len(ins) and ins[n].vram not in labels:
            starts.add(ins[n].vram)
    return sorted(starts)


def _words(code: Code, fn: range) -> tuple[int, ...]:
    """The function's words with the fields relocation changes masked: every `lui` immediate,
    the immediate of an instruction based on a `lui` register (or on a sum with one), jal
    targets, and j targets outside the function (inside, the offset from the start)."""
    hi: set[object] = set()
    out = []
    for ins in code.span(fn):
        w, u = ins.getRaw(), ins.uniqueId
        if u == InstrId.cpu_jal:
            w &= ~_TARGET
        elif u == InstrId.cpu_j:
            t = ins.getInstrIndexAsVram()
            w = w & ~_TARGET | ((t - fn.start) >> 2 if t in fn else _TARGET)
        elif u == InstrId.cpu_lui or (
            ins.hasOperandAlias(OperandType.cpu_immediate)
            and ins.hasOperandAlias(OperandType.cpu_rs)
            and ins.rs in hi
        ):
            w &= ~_IMM
        dest = ins.getDestinationGpr()
        if u == InstrId.cpu_lui:
            hi.add(ins.rt)
        elif dest is not None:
            if u == InstrId.cpu_addu and (ins.rs in hi or ins.rt in hi):
                hi.add(dest)
            else:
                hi.discard(dest)
        out.append(w & 0xFFFFFFFF)
    return tuple(out)
