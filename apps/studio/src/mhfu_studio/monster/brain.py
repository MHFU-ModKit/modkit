# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A big monster's native AI script as the Brain tab reads it: the source one header runs, as
rows in a tree, with an outline of the header's lists, a search and a history. No toolkit here.

    p = decode(game, 75)                 # one species row as a Program (a second or more)
    v = view(p, default_header(p))       # what that header runs, as lines
    v.find("enter 3 0")                  # rows
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache

from mhfu import files
from mhfu.em import script
from mhfu.em.script import ir, source
from mhfu.em.script.source import Line
from mhfu.files import Extracted


class NoScript(LookupError):
    """A species row no em overlay serves."""


LIMIT = 100
"""Rows the history keeps."""
_PC = re.compile(r"0x[0-9a-f]+", re.IGNORECASE)


@cache
def rows(game: Extracted) -> dict[int, int]:
    """Every script row an em overlay serves, with the species of the overlay that serves it.
    Seconds to scan, so once per game."""
    return {row: sp for sp in files.EM_SPECIES for row in script.Scripts(game, sp).rows}


def decode(game: Extracted, row: int) -> ir.Program:
    """The program of species row `row`, from the overlay that serves it: its own species' when
    it has one (a second or less), else the one `rows` finds. `NoScript` for a row none serves."""
    sc = script.Scripts(game, row) if row in files.EM_SPECIES else None
    if sc is None or row not in sc.rows:
        sp = rows(game).get(row)
        if sp is None:
            raise NoScript(f"no overlay serves script row {row}")
        sc = script.Scripts(game, sp)
    return script.lift.lift(sc, row)


@dataclass(frozen=True)
class Variant:
    """A header and the map types that run it."""

    header: str
    va: int | None
    variants: tuple[int, ...]
    words: str


def map_words(types: tuple[int, ...]) -> str:
    """`Every map type`, `Map types 0-4, 6`."""
    if len(set(types)) == script.VARIANTS:
        return "Every map type"
    if not types:
        return "No map type"
    return f"Map type{'s' if len(types) > 1 else ''} {source.ranges(types).replace(',', ', ')}"


def variants(p: ir.Program) -> list[Variant]:
    """One per header, in the program's order."""
    return [Variant(h.name, h.va, h.variants, map_words(h.variants)) for h in p.headers()]


def default_header(p: ir.Program) -> str:
    """The header most map types use, the first of equals."""
    return max(p.headers(), key=lambda h: len(h.variants)).name


def pick_header(p: ir.Program, name: str | None) -> str:
    """`name` when the program has that header, else the default."""
    if name is not None and any(h.name == name for h in p.headers()):
        return name
    return default_header(p)


@dataclass(frozen=True)
class Item:
    """A slot of a header list."""

    index: int
    name: str | None
    """The routine or data it names; None for an empty slot or a word outside the program."""
    row: int | None
    """The row of its `routine NAME` line; None when it names no routine of the view."""


@dataclass(frozen=True)
class Outline:
    """A header field's list."""

    index: int
    """The header field's."""
    name: str
    items: tuple[Item, ...]


@dataclass(frozen=True)
class BrainView:
    """The lines one header runs, as a tree: a row's parent is the nearest row above it with a
    smaller depth, so walking `top` and `children` depth first gives `lines` back."""

    row: int
    """The species row of the program."""
    header: str
    lines: tuple[Line, ...]
    parent: tuple[int, ...]
    """By row; -1 at the top."""
    children: tuple[tuple[int, ...], ...]
    top: tuple[int, ...]
    row_of: dict[str, int]
    """A node's row, by its id."""
    outline: tuple[Outline, ...]
    enters: dict[int, tuple[int, int]]
    """The `(main, sub)` of each `enter` line, by row."""
    labels: dict[int, str] = field(default_factory=dict)
    """What an `enter` line plays, by row; empty until the workspace labels the view."""

    def find(self, text: str) -> list[int]:
        """The rows whose text holds `text`, any case; a `0x...` query also gives the rows whose
        address it is."""
        q = text.strip()
        if not q:
            return []
        pc = int(q, 16) if _PC.fullmatch(q) else None
        low = q.lower()
        return [
            i for i, ln in enumerate(self.lines) if low in ln.text.lower() or (pc and ln.pc == pc)
        ]

    def ancestors(self, row: int) -> list[int]:
        """The rows above `row`, outermost first."""
        out = []
        at = self.parent[row]
        while at >= 0:
            out.append(at)
            at = self.parent[at]
        return out[::-1]

    def target_row(self, row: int) -> int | None:
        """The row of the routine a call, goto or walk line leads to."""
        node = self.lines[row].target
        return None if node is None else self.row_of.get(node)


def _groups(lines: list[Line]) -> list[tuple[int, int]]:
    """The span of lines of each item: a depth 0 line and the deeper lines after it."""
    heads = [i for i, ln in enumerate(lines) if ln.depth == 0]
    return list(zip(heads, [*heads[1:], len(lines)], strict=True))


def _kept(p: ir.Program, header: ir.Header) -> list[bool]:
    """Per item: what `header` runs. Its routines, the lists its fields name, the data those
    lists name, and itself; no other header, blob or unnamed item."""
    reach = p.reach()
    tables = p.tables()
    lists = {f for f in header.fields if isinstance(f, str)}
    named = {s for t in lists if t in tables for s in tables[t].slots if isinstance(s, str)}
    keep = []
    for item in p.items:
        match item:
            case ir.Routine():
                keep.append(header.name in reach[item.name])
            case ir.Table():
                keep.append(item.name in lists)
            case ir.Data():
                keep.append(item.name in named or not named.isdisjoint(item.labels))
            case ir.Header():
                keep.append(item is header)
            case _:
                keep.append(False)
    return keep


def _tree(lines: list[Line]) -> tuple[list[int], list[list[int]]]:
    parent: list[int] = []
    children: list[list[int]] = [[] for _ in lines]
    above: list[int] = []
    for i, ln in enumerate(lines):
        while above and lines[above[-1]].depth >= ln.depth:
            above.pop()
        parent.append(above[-1] if above else -1)
        if above:
            children[above[-1]].append(i)
        above.append(i)
    return parent, children


def _outline(
    h: ir.Header, tables: dict[str, ir.Table], routine_row: dict[str, int]
) -> list[Outline]:
    out = []
    for f, t in enumerate(h.fields):
        if not isinstance(t, str) or t not in tables:
            continue
        slot = script.opcodes.slot(f)
        name = f"interrupt 0x{slot.interrupt:02X}" if slot.interrupt else slot.name
        items = tuple(
            Item(
                i,
                s if isinstance(s, str) else None,
                routine_row.get(s) if isinstance(s, str) else None,
            )
            for i, s in enumerate(tables[t].slots)
        )
        out.append(Outline(f, name, items))
    return out


def view(p: ir.Program, header: str) -> BrainView:
    """What `header` runs: the program's source with the links resolved through it, kept to its
    routines, lists and data. `KeyError` for a header the program has not."""
    h = next((x for x in p.headers() if x.name == header), None)
    if h is None:
        raise KeyError(header)
    full = source.render(p, header)
    groups = _groups(full)
    if len(groups) != len(p.items) + 1:
        raise ValueError(f"row {p.row}: {len(groups)} groups of lines for {len(p.items)} items")
    keep = [True, *_kept(p, h)]
    lines: list[Line] = []
    routine_row: dict[str, int] = {}
    for item, (a, b), kept in zip([None, *p.items], groups, keep, strict=True):
        if not kept:
            continue
        if isinstance(item, ir.Routine):
            routine_row[item.name] = len(lines)
        lines.extend(full[a:b])
    parent, children = _tree(lines)
    enter = {
        n.id: (int(n.args["main"]), int(n.args["sub"]))
        for n in p.nodes()
        if isinstance(n, ir.Insn)
        and n.op.name == "enter"
        and isinstance(n.args["main"], int)
        and isinstance(n.args["sub"], int)
    }
    row_of = {ln.node: i for i, ln in enumerate(lines) if ln.node is not None}
    return BrainView(
        p.row,
        header,
        tuple(lines),
        tuple(parent),
        tuple(tuple(c) for c in children),
        tuple(i for i, up in enumerate(parent) if up < 0),
        row_of,
        tuple(_outline(h, p.tables(), routine_row)),
        {row_of[n]: pair for n, pair in enter.items() if n in row_of},
    )


class History:
    """Rows visited, to go back and forward over; the oldest fall off past `LIMIT`."""

    def __init__(self, limit: int = LIMIT) -> None:
        self.limit = limit
        self.rows: list[int] = []
        self.at = -1

    @property
    def current(self) -> int | None:
        return self.rows[self.at] if self.rows else None

    @property
    def can_back(self) -> bool:
        return self.at > 0

    @property
    def can_forward(self) -> bool:
        return self.at < len(self.rows) - 1

    def go(self, row: int) -> None:
        """Arrive at `row`: what was ahead is dropped; the row already current is no new stop."""
        if row == self.current:
            return
        del self.rows[self.at + 1 :]
        self.rows.append(row)
        del self.rows[: -self.limit]
        self.at = len(self.rows) - 1

    def back(self) -> int | None:
        """The row before; None at the start."""
        if not self.can_back:
            return None
        self.at -= 1
        return self.rows[self.at]

    def forward(self) -> int | None:
        if not self.can_forward:
            return None
        self.at += 1
        return self.rows[self.at]

    def clear(self) -> None:
        self.rows.clear()
        self.at = -1
