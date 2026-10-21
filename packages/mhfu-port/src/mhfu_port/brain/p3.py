# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""MHP3rd's AI script as a tree: the species table, each species' lists of routines, and the
routines decoded, encoded back to the game's bytes and written as text.

    from mhfu.files import Extracted
    from mhfu_port.brain import p3

    extracted = Extracted.find(env="MHP3RD_DATA")
    brain = p3.lift(extracted, 23)           # Brute Tigrex: its lists and routines
    routine = brain.routines["call_12"]      # name, va, body (a tree), problems
    p3.encode(routine)                       # its bytes, as the game has them
    p3.render(brain)                         # the text lines
    p3.check(extracted).ok                   # every id and slot lifts and encodes back

A list ends where the next item starts (an item is whatever a descriptor, a list table or a
list points at), usually after a zero word. A routine runs from its entry to the end instruction
at block depth 0; nothing jumps inside one, so reading it linearly is exact.
"""

from __future__ import annotations

import struct
from bisect import bisect_right
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field

from mhfu.files import ENGINE_SKEW, Extracted
from mhfu.overlay import Overlay

from .p3ops import CASE, CLOSE, ENDS, LISTS, OPEN, OPS, P3Op

GAME_TASK = 99
"""MHP3rd's game_task.ovl, by extracted file id: the species table, and the lengths."""
SPECIES_TABLE = 0x09D78C04  # noaddr: MHP3rd game_task
IDS = range(1, 75)
"""The monster ids with a row in the species table; the records follow its last row."""
RESIDENT = 0xFFFF
"""The engine file id of a descriptor that sits in game_task."""
DESCRIPTOR_WORDS = 24
"""Words of a descriptor read for pointers into the script data."""
TABLE_AT = 0x14
"""The descriptor field that points at its table of list pointers."""
KNOWN = {3: "call_59", 51: "call_59"}
"""Routines the game leaves without an end instruction: em003's, which runs into the next item.
Each slot of those ids has one."""

UNKNOWN = "unknown opcode"
CUT = "runs into the next item"
NESTING = "marker outside its block"


@dataclass(frozen=True)
class Slot:
    addr: int
    """The species descriptor's address."""
    file: int
    """The extracted file of the overlay that holds it."""


@dataclass(frozen=True)
class Species:
    """The species table: each monster id's descriptors, one per overlay slot (m0, m1, ...)."""

    slots: Mapping[int, tuple[Slot, ...]]

    @classmethod
    def read(cls, extracted: Extracted) -> Species:
        game_task = extracted.overlay(GAME_TASK)
        out: dict[int, tuple[Slot, ...]] = {}
        for ident in IDS:
            records, info = struct.unpack("<II", game_task.read(SPECIES_TABLE + 8 * ident, 8))
            pairs = [
                struct.unpack("<II", game_task.read(records + 8 * k, 8))
                for k in range(info & 0xFF if records else 0)
            ]
            out[ident] = tuple(
                Slot(addr, GAME_TASK if file == RESIDENT else file - ENGINE_SKEW)
                for addr, file in pairs
            )
        return cls(out)

    def descriptors(self, file: int) -> list[int]:
        """Every descriptor in that overlay, of all ids: they share lists and items."""
        return sorted({s.addr for slots in self.slots.values() for s in slots if s.file == file})


@dataclass(frozen=True)
class Problem:
    kind: str
    """UNKNOWN, CUT or NESTING."""
    pc: int
    detail: str = ""

    def __str__(self) -> str:
        return f"0x{self.pc:08X}: {self.kind}" + (f": {self.detail}" if self.detail else "")


@dataclass
class Insn:
    op: P3Op
    args: dict[str, int]
    pc: int | None = None
    """The address it was decoded at; None for a new one."""


@dataclass
class Leave:
    """A marker of an enclosing block met inside a block of another opcode: the repeat's
    break inside an if, which leaves the loop."""

    op: P3Op
    sub: int
    value: int | None = None
    pc: int | None = None


@dataclass
class Arm:
    """A marker inside a block and the nodes under it: an else, a case, a default, a branch."""

    sub: int
    value: int | None
    """The case marker's byte (a case value, a branch's weight); None for any other marker."""
    body: list[Node]
    pc: int | None = None


@dataclass
class Block:
    op: P3Op
    args: dict[str, int]
    """The opener's operands."""
    head: list[Node]
    """What runs before the first marker: an if's then-branch, a loop or bracket body."""
    arms: list[Arm]
    pc: int | None = None
    close: int | None = None
    """The address of the closing marker."""


Node = Insn | Block | Leave
Located = Insn | Block | Leave | Arm


@dataclass
class Routine:
    name: str
    va: int
    body: list[Node]
    problems: list[Problem] = field(default_factory=list)

    @property
    def ends(self) -> bool:
        """Whether it stops at an end instruction, as opposed to running into the next item."""
        last = self.body[-1] if self.body else None
        return isinstance(last, Insn) and last.op.code in ENDS

    @property
    def instructions(self) -> int:
        return count(self.body)


@dataclass
class List:
    index: int
    """The list's number: LISTS[index] names it."""
    va: int
    entries: list[str | None]
    """A routine's name per slot, None for an empty one. The zero words that end the list in the
    data (a terminator, or padding to the next item) are not slots."""


@dataclass
class Brain:
    id: int
    slot: int
    overlay: str
    descriptor: int
    lists: list[List | None]
    """By list number; None where the descriptor has no such list."""
    routines: dict[str, Routine]
    """In list order; named `<list>_<index>` after the first slot that holds each."""

    @property
    def instructions(self) -> int:
        return sum(r.instructions for r in self.routines.values())

    def entry(self, list_: int, index: int) -> str | None:
        """The routine in a list slot; None if the list or the slot is not there or is empty."""
        found = self.lists[list_] if list_ < len(self.lists) else None
        return found.entries[index] if found and index < len(found.entries) else None


def walk(nodes: Iterable[Node]) -> Iterator[Located]:
    """Every node and arm under `nodes`, in source order."""
    for n in nodes:
        yield n
        if isinstance(n, Block):
            yield from walk(n.head)
            for arm in n.arms:
                yield arm
                yield from walk(arm.body)


def count(nodes: Iterable[Node]) -> int:
    """The instructions, markers and closers under `nodes`: what the game steps over."""
    return sum(2 if isinstance(n, Block) else 1 for n in walk(nodes))


def decode(code: bytes, va: int = 0, name: str = "") -> Routine:
    """The routine at the start of `code`, which ends where the item ends: decoding stops at the
    end instruction at depth 0, or where the bytes run out (a problem)."""
    body: list[Node] = []
    problems: list[Problem] = []
    frames: list[tuple[Block, list[Node]]] = []  # open blocks, each with where its next node goes
    pos = 0
    while True:
        pc = va + pos
        into = frames[-1][1] if frames else body
        if pos >= len(code):
            problems.append(Problem(CUT, pc))
            break
        code_byte = code[pos]
        if code_byte in ENDS:
            into.append(Insn(OPS[code_byte], {}, pc))
            pos += 1
            if not frames:
                break
            continue
        op = OPS.get(code_byte)
        if op is None:
            problems.append(Problem(UNKNOWN, pc, f"0x{code_byte:02X}"))
            break
        sub = code[pos + 1] if op.block and pos + 1 < len(code) else 0
        size = op.length(sub)
        if pos + size > len(code):
            problems.append(Problem(CUT, pc))
            break
        operands = code[pos + 1 + op.block : pos + size]
        if not op.block:
            into.append(Insn(op, dict(zip(op.operands, operands, strict=True)), pc))
        elif sub == OPEN:
            block = Block(op, dict(zip(op.operands, operands, strict=True)), [], [], pc)
            into.append(block)
            frames.append((block, block.head))
        else:
            value = operands[0] if sub == CASE else None
            owner = next((i for i in reversed(range(len(frames))) if frames[i][0].op is op), None)
            if owner is None or (owner != len(frames) - 1 and sub == CLOSE):
                problems.append(Problem(NESTING, pc, op.mnemonic(sub)))
                break
            if owner != len(frames) - 1:
                into.append(Leave(op, sub, value, pc))
            elif sub == CLOSE:
                frames.pop()[0].close = pc
            else:
                arm = Arm(sub, value, [], pc)
                frames[-1][0].arms.append(arm)
                frames[-1] = (frames[-1][0], arm.body)
        pos += size
    return Routine(name, va, body, problems)


def _operands(op: P3Op, args: Mapping[str, int]) -> bytes:
    if args.keys() != set(op.operands):
        raise ValueError(
            f"{op.name} takes {' '.join(op.operands) or 'no operands'}, got {dict(args)}"
        )
    return bytes(args[name] for name in op.operands)


def _marker(op: P3Op, sub: int, value: int | None) -> bytes:
    if (value is None) == (sub == CASE):
        raise ValueError(f"{op.mnemonic(sub)} {'needs' if sub == CASE else 'takes no'} value")
    return bytes([op.code, sub]) + (b"" if value is None else bytes([value]))


def _own(node: Node) -> bytes:
    """The bytes of the instruction, opener or marker itself: not what is inside a block."""
    match node:
        case Insn():
            return bytes([node.op.code]) + _operands(node.op, node.args)
        case Block():
            return bytes([node.op.code, OPEN]) + _operands(node.op, node.args)
        case Leave():
            return _marker(node.op, node.sub, node.value)


def encode_nodes(nodes: Iterable[Node]) -> bytes:
    out = bytearray()
    for n in nodes:
        out += _own(n)
        if isinstance(n, Block):
            out += encode_nodes(n.head)
            for arm in n.arms:
                out += _marker(n.op, arm.sub, arm.value) + encode_nodes(arm.body)
            out += _marker(n.op, CLOSE, None)
    return bytes(out)


def encode(routine: Routine) -> bytes:
    """The routine's bytes: what `decode` was given, up to its end."""
    return encode_nodes(routine.body)


class Region:
    """One overlay's script data: the lists of its descriptors and where every item starts."""

    def __init__(self, overlay: Overlay, descriptors: Iterable[int]) -> None:
        self.overlay = overlay
        self.data = range(overlay.text.stop, overlay.initialised.stop)
        self.descriptors = sorted(descriptors)
        fence: set[int] = set()  # item starts known without reading a list
        starts: set[int] = set()
        for d in self.descriptors:
            fence.add(d)
            fence.update(w for w in self._words(d, DESCRIPTOR_WORDS) if w in self.data)
            table = [w for w in self.table(d) if w in self.data]
            fence.update(table)
            starts.update(table)
        self.lists: dict[int, list[int]] = {}
        while True:
            ordered = sorted(fence)
            self.lists = {
                s: self._words(s, (self._next(ordered, s) - s) // 4) for s in sorted(starts)
            }
            grown = fence | {w for words in self.lists.values() for w in words if w in self.data}
            if grown == fence:
                break
            fence = grown
        self.fence = sorted(fence)

    def _words(self, addr: int, n: int) -> list[int]:
        return list(struct.unpack(f"<{n}I", self.overlay.read(addr, 4 * n)))

    def _next(self, ordered: Sequence[int], va: int) -> int:
        i = bisect_right(ordered, va)
        return ordered[i] if i < len(ordered) else self.data.stop

    def table(self, descriptor: int) -> list[int]:
        """The descriptor's 13 list addresses; empty when it has no script."""
        at = self.overlay.u32(descriptor + TABLE_AT)
        return self._words(at, len(LISTS)) if at in self.data else []

    def entries(self) -> list[int]:
        """Every routine's address, of all descriptors."""
        return sorted({w for words in self.lists.values() for w in words if w in self.data})

    def limit(self, va: int) -> int:
        """Where the item at `va` must end: the next item's start."""
        return self._next(self.fence, va)

    def routine(self, va: int, name: str = "") -> Routine:
        return decode(self.overlay.read(va, self.limit(va) - va), va, name)

    def brain(self, ident: int, slot: int, descriptor: int) -> Brain:
        table = self.table(descriptor)
        if not table:
            raise ValueError(f"id {ident} slot {slot} has no script")
        names: dict[int, str] = {}
        routines: dict[str, Routine] = {}
        lists: list[List | None] = []
        for number, start in enumerate(table):
            if start not in self.lists:
                lists.append(None)
                continue
            words = self.lists[start]
            while words and not words[-1]:
                words = words[:-1]
            entries: list[str | None] = []
            for index, va in enumerate(words):
                if not va:
                    entries.append(None)
                    continue
                if va not in self.data:
                    raise ValueError(
                        f"{LISTS[number]} slot {index} points to 0x{va:08X}, no script"
                    )
                if va not in names:
                    names[va] = f"{LISTS[number]}_{index}"
                    routines[names[va]] = self.routine(va, names[va])
                entries.append(names[va])
            lists.append(List(number, start, entries))
        return Brain(
            ident, slot, self.overlay.name.removesuffix(".ovl"), descriptor, lists, routines
        )


def lift(extracted: Extracted, ident: int, slot: int = 0, species: Species | None = None) -> Brain:
    """One species' brain from slot `slot` (m0, m1, ...); ValueError for an id or slot without
    a script."""
    species = species or Species.read(extracted)
    slots = species.slots.get(ident, ())
    if slot >= len(slots):
        raise ValueError(f"id {ident} has {len(slots)} slots, not {slot + 1}")
    where = slots[slot]
    region = Region(extracted.overlay(where.file), species.descriptors(where.file))
    return region.brain(ident, slot, where.addr)


def render(brain: Brain, routine: str | None = None, raw: bool = False) -> list[str]:
    """The brain as text lines, or one routine of it; `raw` puts each instruction's address and
    bytes first. A block's markers sit level with its opener and a dedent closes it."""
    if routine is not None and routine not in brain.routines:
        raise ValueError(f"no routine {routine}: try {next(iter(brain.routines), 'none')}")
    out: list[str] = []
    if routine is None:
        out.append(f"; id {brain.id} slot {brain.slot} {brain.overlay} 0x{brain.descriptor:08X}")
        for found in brain.lists:
            if found is not None:
                n = sum(e is not None for e in found.entries)
                out.append(f"; list {found.index} {LISTS[found.index]} 0x{found.va:08X} ({n})")
    for r in brain.routines.values() if routine is None else [brain.routines[routine]]:
        out += ["", f"; {r.name} 0x{r.va:08X}"] if out else [f"; {r.name} 0x{r.va:08X}"]
        _lines(brain, r.body, 0, raw, out)
        out += [f"; {p}" for p in r.problems]
    return out


def _lines(brain: Brain, nodes: list[Node], depth: int, raw: bool, out: list[str]) -> None:
    def put(level: int, pc: int | None, data: bytes, text: str) -> None:
        prefix = f"{'' if pc is None else f'{pc:08X}'}  {data.hex(' '):<12}  " if raw else ""
        out.append(prefix + "  " * level + text)

    for n in nodes:
        put(depth, n.pc, _own(n), _text(brain, n))
        if isinstance(n, Block):
            _lines(brain, n.head, depth + 1, raw, out)
            for arm in n.arms:
                put(
                    depth,
                    arm.pc,
                    _marker(n.op, arm.sub, arm.value),
                    _marker_text(n.op, arm.sub, arm.value),
                )
                _lines(brain, arm.body, depth + 1, raw, out)
            put(depth, n.close, _marker(n.op, CLOSE, None), _marker_text(n.op, CLOSE, None))


def _text(brain: Brain, node: Node) -> str:
    """A line's words: the mnemonic and its operands, and the routine a call or goto enters."""
    if isinstance(node, Leave):
        return _marker_text(node.op, node.sub, node.value)
    text = node.op.name + "".join(f" {k}={v}" for k, v in node.args.items())
    if (entered := node.op.entry(node.args)) is not None:
        text += f"  ; {brain.entry(*entered) or '?'}"
    return text


def _marker_text(op: P3Op, sub: int, value: int | None) -> str:
    return op.mnemonic(sub) + ("" if value is None else f" {op.case}={value}")


@dataclass(frozen=True)
class Totals:
    routines: int = 0
    instructions: int = 0
    unknown: int = 0
    """Routines that stop at an opcode the table does not have."""
    uncovered: int = 0
    """Bytes between a routine's end and the next item that are not zero padding."""
    mismatched: int = 0
    """Routines whose encoding is not the bytes they were decoded from."""

    def __add__(self, other: Totals) -> Totals:
        return Totals(*(a + b for a, b in zip(self.fields, other.fields, strict=True)))

    @property
    def fields(self) -> tuple[int, ...]:
        return (self.routines, self.instructions, self.unknown, self.uncovered, self.mismatched)


@dataclass
class Report:
    slots: dict[int, Totals] = field(default_factory=dict)
    """By slot (m0, m1, ...): each overlay's routines counted once, whatever ids share them."""
    skipped: list[int] = field(default_factory=list)
    """The ids that have no script."""
    problems: list[str] = field(default_factory=list)
    """Everything wrong except the known quirk, as `id slot routine: what`."""
    quirks: list[str] = field(default_factory=list)
    """The known problems met, as `id slot routine`."""
    missing: list[str] = field(default_factory=list)
    """The known problems that were not met."""

    @property
    def total(self) -> Totals:
        return sum(self.slots.values(), Totals())

    @property
    def ok(self) -> bool:
        t = self.total
        return not (self.problems or self.missing or t.unknown or t.uncovered or t.mismatched)


def _padding(region: Region, routine: Routine, data: bytes) -> int:
    """Bytes after the routine's end that the next item does not account for: anything but
    fewer than a word of zeros."""
    end = routine.va + len(data)
    gap = region.limit(routine.va) - end
    return gap if gap >= 4 or any(region.overlay.read(end, gap)) else 0


def check(extracted: Extracted, ids: Iterable[int] | None = None) -> Report:
    """Lift every id and slot (or `ids`) and compare each routine with its bytes: unknown opcodes,
    bytes no routine accounts for, encodings that differ, and problems beyond KNOWN."""
    species = Species.read(extracted)
    wanted = sorted(IDS if ids is None else ids)
    report = Report()
    regions: dict[int, Region] = {}
    slot_of: dict[int, int] = {}
    met: set[tuple[int, int]] = set()
    for ident in wanted:
        for slot, where in enumerate(species.slots.get(ident, ())):
            if where.file not in regions:
                regions[where.file] = Region(
                    extracted.overlay(where.file), species.descriptors(where.file)
                )
            slot_of.setdefault(where.file, slot)
            region = regions[where.file]
            if not region.table(where.addr):
                if ident not in report.skipped:
                    report.skipped.append(ident)
                continue
            try:
                brain = region.brain(ident, slot, where.addr)
            except ValueError as e:
                report.problems.append(f"id {ident} slot {slot}: {e}")
                continue
            for r in brain.routines.values():
                for problem in r.problems:
                    here = f"id {ident} slot {slot} {r.name}"
                    if KNOWN.get(ident) == r.name and problem.kind == CUT:
                        met.add((ident, slot))
                        report.quirks.append(here)
                    else:
                        report.problems.append(f"{here}: {problem}")
    report.missing = [
        f"id {ident} slot {slot} {name}"
        for ident, name in KNOWN.items()
        if ident in wanted
        for slot in range(len(species.slots[ident]))
        if (ident, slot) not in met
    ]
    for file, region in regions.items():
        total = Totals()
        for va in region.entries():
            r = region.routine(va)
            data = encode(r)
            total += Totals(
                1,
                r.instructions,
                sum(p.kind == UNKNOWN for p in r.problems),
                _padding(region, r, data) if r.ends else 0,
                region.overlay.read(va, len(data)) != data,
            )
        report.slots[slot_of[file]] = report.slots.get(slot_of[file], Totals()) + total
    return report
