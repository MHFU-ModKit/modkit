# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""One species row's AI script as a program: routines as trees, the lists that name them, data,
and the headers that point at the lists.

Addresses are not part of it. A native node keeps the address it was decoded at (`pc`, `va`) so
a program can be laid out as the game has it and live addresses mapped back; a new node has none.
Everything the bytes derive is left out: a switch's, random's or bucket's `n`, the sub-codes of
numbered markers, a block's closing marker.

    Program(row, base, items)
      Routine(name, body, tail)    body: Insn | Block, a Block's arms hold more
      Table(name, slots)           a list: names, External words, None for an empty slot
      Data(name, data, labels)     bytes with named offsets
      Blob(data)                   bytes kept as they are
      Header(name, fields, variants)
    Index(prog)                    its nodes by address and by id
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field

from .opcodes import Op, Ref, derived_count, operands, slot

_NEW = re.compile(r"n(\d+)")
MAX_LIST = 256
"""Entries a header list holds: its index operand is a byte."""


@dataclass(frozen=True)
class Sym:
    """A label for a list entry in new code; assembling resolves it to the entry's index."""

    label: str


@dataclass(frozen=True)
class External:
    """A word that points outside the program, into the overlay's bss or another row's data:
    assembled as it is, never relocated."""

    addr: int


Value = int | Sym
Entry = str | External | None
"""A pointer word: the name of an item in the program, an External, or None for 0."""


@dataclass
class Insn:
    op: Op
    args: dict[str, Value]
    id: str = ""
    pc: int | None = None
    """The address it was decoded at; None for a new instruction."""
    origin: str | None = None
    """The id of the node this was copied from."""

    @property
    def length(self) -> int:
        return self.op.length_of(self.args)


@dataclass
class Arm:
    """A marker inside a block and the nodes under it: an if's else, a switch's case or default,
    a random's branch, a bucket's or zone's case."""

    sub: int
    """The marker's kind: 1 for an else and a case, 2 for a default; 1 for the numbered kinds,
    whose sub-code is the arm's position."""
    args: dict[str, Value]
    body: list[Node]
    id: str = ""
    pc: int | None = None
    origin: str | None = None
    """The id of the node this was copied from."""


@dataclass
class Block:
    op: Op
    args: dict[str, Value]
    """The opener's operands without the derived `n`."""
    head: list[Node]
    """What runs before the first marker: an if's then-branch, a repeat's body."""
    arms: list[Arm]
    id: str = ""
    pc: int | None = None
    close: int | None = None
    """The address of the closing marker."""
    origin: str | None = None
    """The id of the node this was copied from."""

    @property
    def count(self) -> int | None:
        return derived_count(self.op.shape, [a.sub for a in self.arms])


Node = Insn | Block
Located = Insn | Block | Arm


@dataclass
class Routine:
    name: str
    body: list[Node]
    tail: bytes = b""
    """The bytes after the last instruction, up to the next item."""
    va: int | None = None


@dataclass
class Table:
    name: str
    slots: list[Entry]
    va: int | None = None


@dataclass
class Data:
    name: str
    data: bytes
    labels: dict[str, int] = field(default_factory=dict)
    """Label to offset: the targets of the list slots that name this data."""
    va: int | None = None


@dataclass
class Blob:
    data: bytes
    va: int | None = None


@dataclass
class Header:
    name: str
    fields: list[Entry]
    """One per pointer field, by header field: the table's name, an External, or None."""
    variants: tuple[int, ...] = ()
    """The map types that use this header."""
    va: int | None = None


Item = Routine | Table | Data | Blob | Header


def refs(n: Node) -> tuple[Ref, ...]:
    """The list entries a node names; an operand that is a Sym is left out."""
    return n.op.refs(n.args) if isinstance(n, Insn) else n.op.refs(n.args, 0)


def labels(n: Node) -> list[tuple[str, Sym, Ref]]:
    """The labels on an instruction's `@list` operands: the operand, the label and the Ref its
    list entry will have, at index 0."""
    if not isinstance(n, Insn) or not any(isinstance(v, Sym) for v in n.args.values()):
        return []
    args = {k: 0 if isinstance(v, Sym) else v for k, v in n.args.items()}
    tagged = [o.name for o in operands(n.op.plain_layout(args)) if o.ref]
    out = []
    for name, ref in zip(tagged, n.op.refs(args)[len(n.op.fixed) :], strict=True):
        if isinstance(sym := n.args[name], Sym):
            out.append((name, sym, ref))
    return out


def walk(nodes: Iterable[Node]) -> Iterator[Located]:
    """Every node and arm under `nodes`, in source order."""
    for n in nodes:
        yield n
        if isinstance(n, Block):
            yield from walk(n.head)
            for arm in n.arms:
                yield arm
                yield from walk(arm.body)


class Index:
    """A program's nodes by address and by id, built once: an edit changes the tree, so build
    another after it."""

    def __init__(self, p: Program) -> None:
        self._at: dict[int, list[Located]] = {}
        self._ending: dict[int, Node] = {}
        self._node: dict[str, Located] = {}
        self._routine: dict[str, Routine] = {}
        self._home: dict[str, list[Node]] = {}
        for r in p.routines():
            self._add(r, r.body)

    def _add(self, r: Routine, nodes: list[Node]) -> None:
        for n in nodes:
            self._see(r, n, nodes)
            if isinstance(n, Block):
                self._add(r, n.head)
                for arm in n.arms:
                    self._see(r, arm, None)
                    self._add(r, arm.body)

    def _see(self, r: Routine, n: Located, home: list[Node] | None) -> None:
        if n.pc is not None:
            self._at.setdefault(n.pc, []).append(n)
        if isinstance(n, Block) and n.close is not None:
            self._at.setdefault(n.close, []).append(n)
        if isinstance(n, Insn) and n.op.resume and n.pc is not None:
            self._ending.setdefault(n.pc + n.length, n)
        if n.id:
            self._node[n.id] = n
            self._routine[n.id] = r
            if home is not None:
                self._home[n.id] = home

    def __contains__(self, nid: str) -> bool:
        return nid in self._node

    def at(self, pc: int) -> tuple[Located, ...]:
        """What starts at the native address `pc`: an instruction, a block's opener or a
        marker; a block's closing marker answers with its block."""
        return tuple(self._at.get(pc, ()))

    def ending_at(self, pc: int) -> Node | None:
        """The instruction that saves `pc` as the run's resume address: an enter or a walk whose
        native end is `pc`."""
        return self._ending.get(pc)

    def node(self, nid: str) -> Located:
        """The node or arm with this id; KeyError when there is none."""
        return self._node[nid]

    def routine(self, nid: str) -> Routine:
        """The routine the node or arm with this id sits in."""
        return self._routine[nid]

    def home(self, nid: str) -> list[Node]:
        """The list a node sits in: a routine's body, a block's head or an arm's body. An arm
        has none."""
        return self._home[nid]


@dataclass
class Program:
    row: int
    base: int
    """The native address of the region's first byte, which an item's `va` is measured from."""
    items: list[Item]
    """In layout order."""
    end: int = 0
    """The end of the native region, 0 for a program that was not lifted."""
    sha1: str = ""
    """The native region's SHA-1, hex."""

    def routines(self) -> list[Routine]:
        return [i for i in self.items if isinstance(i, Routine)]

    def headers(self) -> list[Header]:
        return [i for i in self.items if isinstance(i, Header)]

    def item(self, name: str) -> Routine | Table | Data | Header:
        for i in self.items:
            if not isinstance(i, Blob) and (i.name == name):
                return i
        raise KeyError(name)

    def nodes(self) -> Iterator[Located]:
        for r in self.routines():
            yield from walk(r.body)

    def at(self, pc: int) -> tuple[Located, ...]:
        """`Index.at` on a fresh index; build an `Index` to look up many."""
        return Index(self).at(pc)

    def ending_at(self, pc: int) -> Node | None:
        """`Index.ending_at` on a fresh index."""
        return Index(self).ending_at(pc)

    def tables(self) -> dict[str, Table]:
        return {t.name: t for t in self.items if isinstance(t, Table)}

    def entry(
        self, header: Header, ref: Ref, tables: Mapping[str, Table] | None = None
    ) -> str | None:
        """The name in the list slot `ref` picks in `header`; None when the field has no list,
        the slot is past the list or empty, or its word is an External. Pass `tables()` when
        looking up many."""
        if ref.field >= len(header.fields):
            return None
        table = header.fields[ref.field]
        if not isinstance(table, str):
            return None
        found = (self.tables() if tables is None else tables).get(table)
        if found is None or ref.index >= len(found.slots):
            return None
        name = found.slots[ref.index]
        return name if isinstance(name, str) else None

    def reach(self) -> dict[str, tuple[str, ...]]:
        """The names of the headers that run each routine: the routines in their lists, and
        what those goto, call and walk to; a label names its routine directly."""
        named = {
            r.name: [x for n in walk(r.body) if isinstance(n, Insn | Block) for x in refs(n)]
            for r in self.routines()
        }
        linked = {
            r.name: [
                sym.label
                for n in walk(r.body)
                if isinstance(n, Insn)
                for _, sym, ref in labels(n)
                if ref.kind != "route" and sym.label in named
            ]
            for r in self.routines()
        }
        tables = self.tables()
        out: dict[str, list[str]] = {name: [] for name in named}
        for h in self.headers():
            todo = [
                s
                for f, t in enumerate(h.fields)
                if isinstance(t, str) and slot(f).kind != "data"
                for s in tables[t].slots
                if isinstance(s, str) and s in named
            ]
            seen: set[str] = set()
            while todo:
                name = todo.pop()
                if name in seen:
                    continue
                seen.add(name)
                out[name].append(h.name)
                todo += linked[name]
                for ref in named[name]:
                    nxt = self.entry(h, ref, tables)
                    if ref.kind != "route" and nxt is not None and nxt in named:
                        todo.append(nxt)
        return {k: tuple(v) for k, v in out.items()}

    def fresh(self, extra: Iterable[Located] = ()) -> str:
        """An id no node has: `n<k>`, one past the highest in use, in the program or `extra`."""
        used = (int(m[1]) for n in (*self.nodes(), *extra) if (m := _NEW.fullmatch(n.id)))
        return f"n{max(used, default=-1) + 1}"
