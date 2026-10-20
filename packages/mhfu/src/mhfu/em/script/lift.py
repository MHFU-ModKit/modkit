# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Lift a species row's script bytes into a Program.

    prog = lift(Scripts(game, 75), 75)        # one row; lift_all(sc) every row of the overlay

A row's region runs from its lowest item to the end of its last header. Each item owns its bytes
up to the next item, a start of any row the overlay serves bounding it (siblings interleave their
variant-private items), and bytes no item owns, the float parameters after a header and another
row's items, are kept as Blobs. A routine's bytes past its last instruction are its tail. Native
list words stay as they are: a pointer inside the region becomes the item's name, one outside it
(the overlay's bss) an External. Routines become trees by a strict bracket parse: a marker
belongs to the innermost open block.
"""

from __future__ import annotations

import hashlib
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass

from . import disasm, ir
from .disasm import BadScript, Scripts
from .opcodes import COUNT, NUMBERED, Shape, derived_count, marker_sub, slot

_HEADLESS = frozenset({Shape.SWITCH, Shape.RANDOM, Shape.BUCKET, Shape.ZONE})
"""Shapes whose first marker follows the header with nothing between."""


class LiftError(ValueError):
    """Bytes the program model does not cover."""


@dataclass
class _Frame:
    block: ir.Block
    outer: list[ir.Node]
    """The list the block sits in."""
    body: list[ir.Node]
    """Where nodes go now: the head, or the last arm's body."""
    count: int | None
    """The opener's `n`, which the arms must add up to."""


def _tree(row: int, insns: Sequence[disasm.Insn]) -> list[ir.Node]:
    """The nodes of a routine's instructions by a strict bracket parse."""
    out: list[ir.Node] = []
    stack: list[_Frame] = []
    cur = out
    for ins in insns:
        op, role = ins.op, ins.role
        nid = f"{row}:{ins.pc:08X}"
        if role is None:
            cur.append(ir.Insn(op, dict(ins.args), nid, ins.pc))
        elif role == "open":
            args: dict[str, ir.Value] = {o.name: ins.args[o.name] for o in op.opener}
            block = ir.Block(op, args, [], [], nid, ins.pc)
            cur.append(block)
            stack.append(_Frame(block, cur, block.head, ins.args.get(COUNT)))
            cur = block.head
        else:
            if not stack or stack[-1].block.op.marker != op.code:
                raise LiftError(f"{ins.mnemonic} at 0x{ins.pc:08X} belongs to no open block")
            frame = stack[-1]
            block = frame.block
            shape, sub = block.op.shape, ins.sub or 0
            if role == "mid":
                if not block.arms and block.head and shape in _HEADLESS:
                    raise LiftError(f"nodes between {block.op.name} and its first marker")
                kind = 1 if shape in NUMBERED else sub
                if marker_sub(shape, kind, len(block.arms)) != sub:
                    raise LiftError(f"{ins.mnemonic} at 0x{ins.pc:08X} is out of order")
                arm = ir.Arm(kind, dict(ins.args), [], nid, ins.pc)
                block.arms.append(arm)
                frame.body = cur = arm.body
            else:
                n = derived_count(shape, [a.sub for a in block.arms])
                if n != frame.count:
                    raise LiftError(
                        f"{block.op.name} at 0x{block.pc:08X} counts {frame.count}, not {n}"
                    )
                block.close = ins.pc
                stack.pop()
                cur = frame.outer
    if stack:
        raise LiftError(f"{stack[-1].block.op.name} at 0x{stack[-1].block.pc:08X} is never closed")
    return out


def routine(
    read: disasm.Reader, entry: int, row: int = 0, dec: disasm.Routine | None = None
) -> tuple[list[ir.Node], int]:
    """The tree of the routine at `entry` and where its last instruction ends; `row` prefixes
    the node ids, `dec` is the routine decoded already."""
    dec = disasm.decode(read, entry) if dec is None else dec
    if not dec.clean or not dec.insns:
        why = dec.problems[0].text if dec.problems else "no instructions"
        raise LiftError(f"the routine at 0x{entry:08X} does not decode: {why}")
    stop = max(i.pc + i.length for i in dec.insns)
    insns: list[disasm.Insn] = []
    pc = entry
    try:
        while pc < stop:
            insns.append(disasm.fetch(read, pc))
            pc += insns[-1].length
        if pc != stop:
            raise LiftError(f"the routine at 0x{entry:08X} does not end where its flow does")
        return _tree(row, insns), stop
    except BadScript as e:
        raise LiftError(f"the routine at 0x{entry:08X}: {e}") from e


class _Lifter:
    def __init__(self, sc: Scripts) -> None:
        self.sc = sc
        self.ovl = sc.ovl
        self.read = sc.read
        self.data = range(self.ovl.text.stop, self.ovl.initialised.stop)
        self.fence = sorted(set().union(*(self._starts(r) for r in sc.rows)))

    def _starts(self, row: int) -> set[int]:
        """Where the items a row's headers point at begin: the headers, their lists and every
        list word that points into the overlay's data."""
        out: set[int] = set()
        for va in self.sc.variants(row):
            h = self.sc.header_in(va)
            out.add(va)
            out.update(p for p in h.fields if p in self.data)
            out.update(w for ws in h.pointers.values() for w in ws if w in self.data)
        return out

    def _kinds(self, headers: dict[int, disasm.Header]) -> dict[int, str]:
        """Routine, data or idle (to be decoded), by the address a list word names. A walk list
        mixes routines and stage arrays: a slot some walk names is known, any other is idle. An
        address the lists disagree on is a routine."""
        routines: set[int] = set()
        data: set[int] = set()
        idle: set[int] = set()
        for va, h in headers.items():
            lst = self.sc.listing(va)
            named = set(lst.routines)
            data.update(w for w in lst.stages if w in self.data)
            for f, ws in h.pointers.items():
                kind = slot(f).kind
                for w in (w for w in ws if w in self.data):
                    if kind == "routines":
                        routines.add(w)
                    elif kind == "data":
                        data.add(w)
                    elif w in named:
                        routines.add(w)
                    elif w not in lst.stages:
                        idle.add(w)
        return {
            **dict.fromkeys(data, "data"),
            **dict.fromkeys(idle - routines - data, "idle"),
            **dict.fromkeys(routines, "routine"),
        }

    def program(self, row: int) -> ir.Program:
        sc, ovl = self.sc, self.ovl
        variants = sc.variants(row)
        headers = {va: sc.header_in(va) for va in sorted(variants)}
        kinds = self._kinds(headers)
        starts: dict[int, str] = {va: "header" for va in headers}
        for h in headers.values():
            starts.update((p, "table") for p in h.fields if p in self.data)
        for w, k in kinds.items():
            starts.setdefault(w, k)
        lo = min(starts)
        hi = max(h.va + 4 * len(h.fields) for h in headers.values())
        if max(starts) >= hi:
            raise LiftError(f"row {row} has items after its last header")
        fence = sorted({*self.fence, *starts})

        def reach(s: int) -> int:
            i = bisect_right(fence, s)
            return min(fence[i], hi) if i < len(fence) else hi

        end = {
            s: (s + 4 * len(headers[s].fields) if k == "header" else reach(s))
            for s, k in starts.items()
        }
        words = {
            s: [ovl.u32(s + 4 * i) for i in range((end[s] - s) // 4)]
            for s, k in starts.items()
            if k == "table"
        }
        names = self._names(headers, starts, words, lo)

        def entry(w: int) -> ir.Entry:
            if w == 0:
                return None
            return names[w] if w in names else ir.External(w)

        items: list[ir.Item] = []
        pos = lo
        for s in sorted(starts):
            if s < pos:
                raise LiftError(f"row {row}: the item at 0x{s:08X} starts inside the one before")
            if s > pos:
                items.append(ir.Blob(ovl.read(pos, s - pos), pos))
            e, kind = end[s], starts[s]
            if kind == "header":
                h = headers[s]
                items.append(ir.Header(names[s], [entry(p) for p in h.fields], variants[s], s))
            elif kind == "table":
                if (e - s) % 4:
                    raise LiftError(f"row {row}: the list at 0x{s:08X} is not whole words")
                items.append(ir.Table(names[s], [entry(w) for w in words[s]], s))
            elif (routine := self._routine_item(row, names[s], s, e, kind)) is not None:
                items.append(routine)
            else:
                self._data(items, names[s], s, ovl.read(s, e - s))
            pos = e
        region = ovl.read(lo, hi - lo)
        return ir.Program(row, lo, items, hi, hashlib.sha1(region).hexdigest())

    @staticmethod
    def _data(items: list[ir.Item], name: str, va: int, chunk: bytes) -> None:
        """Data at `va`, joining the data run before it when that ends here."""
        last = items[-1] if items else None
        if isinstance(last, ir.Data) and last.va is not None and last.va + len(last.data) == va:
            last.labels[name] = len(last.data)
            last.data += chunk
        else:
            items.append(ir.Data(name, chunk, {name: 0}, va))

    def _routine_item(self, row: int, name: str, s: int, e: int, kind: str) -> ir.Routine | None:
        """The routine at `s`, owning bytes up to `e`. None for data, and for an unnamed walk
        slot whose bytes are no routine or run past their item; any other that does not lift
        is an error."""
        if kind == "data":
            return None
        try:
            body, stop = routine(self.read, s, row, self.sc.decode(s))
            if stop > e:
                raise LiftError(f"the routine at 0x{s:08X} runs into the next item")
        except LiftError as err:
            if kind == "routine":
                raise LiftError(f"row {row}: {err}") from err
            return None
        return ir.Routine(name, body, self.ovl.read(stop, e - stop), s)

    def _names(
        self,
        headers: dict[int, disasm.Header],
        starts: dict[int, str],
        words: dict[int, list[int]],
        lo: int,
    ) -> dict[int, str]:
        """A name for every item and data label, from the first list slot that names it: the
        first header's plainly, a later header's with its position in front."""
        names: dict[int, str] = {}
        for k, (va, h) in enumerate(headers.items()):
            pre = "" if k == 0 else f"h{k}_"
            names[va] = f"h{k}"
            for f, p in enumerate(h.fields):
                if p not in words:
                    continue
                sl = slot(f)
                names.setdefault(p, f"{pre}{sl.name}_l")
                for i, w in enumerate(words[p]):
                    if w in starts and w not in names:
                        names[w] = f"{pre}{sl.name}_{i}"
        for s in starts:
            names.setdefault(s, f"x{s - lo:X}")
        return names


def lift(sc: Scripts, row: int | None = None) -> ir.Program:
    """The program of one species row, by default the overlay's own."""
    row = sc.species if row is None else row
    if row not in sc.rows:
        raise ValueError(f"em{sc.species:02d} does not serve species row {row}")
    return _Lifter(sc).program(row)


def lift_all(sc: Scripts) -> dict[int, ir.Program]:
    """A program for every species row the overlay serves."""
    lifter = _Lifter(sc)
    return {row: lifter.program(row) for row in sc.rows}
