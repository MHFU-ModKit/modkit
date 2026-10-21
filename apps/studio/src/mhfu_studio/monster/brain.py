# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A big monster's native AI script as the Brain tab reads it: the source one header runs, as
rows in a tree, with an outline of the header's lists, a search and a history. No toolkit here.

    p = decode(game, 75)                 # one species row as a Program (a second or more)
    v = view(p, default_header(p))       # what that header runs, as lines
    v.find("enter 3 0")                  # rows

A program an edit made (a `Built`'s) views the same: `edited` marks its rows, and a `Renderer`
kept across the views of a header draws only the routines the edit replaced again.

    cache = Renderer()
    v = view(built.program, header, graph.edited_ids(built.applied), cache)
    v.node_at(row), v.row_for(node)      # the Graph and the Code pane select each other by node

    w = words(game, 75, pairs, names, bf)   # what the numbers are called, for every view of it
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache, cached_property

from mhfu import files, stage
from mhfu.em import script
from mhfu.em.script import catalogue as cat
from mhfu.em.script import ir, source
from mhfu.em.script.brainfile import BrainFile
from mhfu.em.script.graph import ENTRY, join_id
from mhfu.em.script.opcodes import BY_NAME, Op
from mhfu.em.script.source import Line
from mhfu.files import Extracted
from mhfu.memory import Space
from mhfu.structs import Species


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


@cache
def maps(game: Extracted) -> dict[int, tuple[int, ...]]:
    """The maps each area is in, from the game's map table."""
    return stage.maps_of(stage.read_map_table(game))


@cache
def _task(game: Extracted) -> Space:
    return Space([game.overlay(files.GAME_TASK)])


def limits(game: Extracted, row: int) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """The distance limits of the row's two range sets (`SPECIES.RANGE_SETS`)."""
    return Species.at(_task(game), row).limits


def words(
    game: Extracted | None,
    row: int,
    pairs: cat.Pairs | None = None,
    names: Mapping[str, str] | None = None,
    bf: BrainFile | None = None,
) -> cat.Words:
    """What the numbers of `row`'s script are called: the file's names of moves, routines,
    variables and ranges, the game's areas and maps and the row's distance limits (none from a
    game that lacks them)."""
    near: tuple[tuple[float, ...], ...] = ()
    where: dict[int, tuple[int, ...]] = {}
    if game is not None:
        try:
            near, where = limits(game, row), maps(game)
        except (OSError, LookupError, ValueError):
            pass
    return cat.Words(
        dict(pairs or {}),
        dict(names or {}),
        {} if bf is None else dict(bf.vars),
        {} if bf is None else dict(bf.ranges),
        near,
        where,
    )


def var_of(op: Op, args: Mapping[str, ir.Value]) -> int | None:
    """The script variable 1 to 9 a node of `op` with `args` sets or tests; None for none."""
    if op.name == "if_var3_zero":
        return 3
    v = next((args.get(i.name) for i in cat.entry(op).operands if i.fmt is cat.Fmt.VAR), None)
    return v if isinstance(v, int) and 1 <= v <= cat.VARS else None


def pair_of(node: object) -> tuple[int, int] | None:
    """The move pair a Move node plays; None for any other node, or one whose pair is not two
    numbers."""
    if not isinstance(node, ir.Insn) or node.op.name != "enter":
        return None
    main, sub = node.args.get("main"), node.args.get("sub")
    return (main, sub) if isinstance(main, int) and isinstance(sub, int) else None


@dataclass(frozen=True)
class Uses:
    """How often a script names each variable, and how many distance ranges it branches on."""

    vars: Counter[int]
    ranges: int


def uses(p: ir.Program) -> Uses:
    out: Counter[int] = Counter()
    ranges = 0
    for n in p.nodes():
        if isinstance(n, ir.Insn | ir.Block):
            if (v := var_of(n.op, n.args)) is not None:
                out[v] += 1
            ranges += n.op.name == "switch_range"
    return Uses(out, ranges)


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
    at: dict[int, int] = field(default_factory=dict)
    """The row of the line at each native address: an instruction, an opener or a marker."""
    closes: dict[int, int] = field(default_factory=dict)
    """The opener's row of the block whose closing marker lies at each native address."""
    ends: dict[int, int] = field(default_factory=dict)
    """The row of the `enter` that ends at each native address, where its run resumes."""
    edited: frozenset[int] = frozenset()
    """The rows of edited code: a node with no native address or among the edited ids, and the
    routine line over one."""
    routine_row: dict[str, int] = field(default_factory=dict)
    """The row of each routine's line, by name."""
    nodes: dict[str, ir.Located] = field(default_factory=dict)
    """The node or arm each line is, by id."""
    blocks: dict[str, ir.Block] = field(default_factory=dict)
    """The block of each arm, by the arm's id."""

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

    def top_of(self, row: int) -> int:
        """The depth 0 row `row` is under: a routine's, a list's, a header's; `row` itself there."""
        up = self.ancestors(row)
        return up[0] if up else row

    def span(self, row: int) -> range:
        """`row` and every row under it."""
        end = row + 1
        while end < len(self.lines) and self.lines[end].depth > self.lines[row].depth:
            end += 1
        return range(row, end)

    def target_row(self, row: int) -> int | None:
        """The row of the routine a call, goto or walk line leads to."""
        node = self.lines[row].target
        return None if node is None else self.row_of.get(node)

    @cached_property
    def _routine_at(self) -> dict[int, str]:
        return {r: name for name, r in self.routine_row.items()}

    def node_at(self, row: int) -> str | None:
        """The id of the node a line is; None for a routine, a list or a header line."""
        return self.lines[row].node

    def row_for(self, node: str, routine: str | None = None) -> int | None:
        """The row of a graph node: its line, a join the line of its block, a routine's entry
        (`routine` named) the routine's line; None for a node this view has not."""
        if node == ENTRY:
            return None if routine is None else self.routine_row.get(routine)
        return self.row_of.get(node.removesuffix(join_id("")))

    def routine_at(self, row: int) -> str | None:
        """The routine a row is in or is the line of; None under a list or the header."""
        return self._routine_at.get(self.top_of(row))

    def gloss(self, row: int, words: cat.Words) -> str:
        """What a line says beyond its numbers (`catalogue.gloss`); for a call, goto or walk the
        user's name of the routine it leads to."""
        node = self.lines[row].node
        if node is None:
            return ""
        if (to := self.target_row(row)) is not None:
            return words.routines.get(self.routine_at(to) or "", "")
        n = self.nodes.get(node)
        return "" if n is None else cat.gloss(n, words, self.blocks.get(node))


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


_STUB = BY_NAME["nop"]


class Renderer:
    """The lines of a header's routines, kept from one view to the next. An edit shares every
    routine it leaves alone with the program before (copy on write), so a routine object seen
    before keeps its lines. What its links lead to is another matter: it reads the header's lists
    and the first node of every routine, so those changing draws every routine again. Keep one
    per header: another header's links are other lines."""

    def __init__(self) -> None:
        self._lines: dict[str, tuple[ir.Routine, _Reads, tuple[Line, ...]]] = {}

    def render(self, p: ir.Program, h: ir.Header, keep: Sequence[bool]) -> list[tuple[Line, ...]]:
        """`source.render` cut by item as `[region, *p.items]`, each item's lines or none where
        `keep` drops it."""
        links = _Reads(
            h.name,
            tuple(i for i in p.items if isinstance(i, ir.Table | ir.Header)),
            tuple((r.name, r.body[0].id) for r in p.routines() if r.body),
        )
        items: list[ir.Item] = []
        home: list[ir.Item | None] = []
        for item, kept in zip(p.items, keep[1:], strict=True):
            if isinstance(item, ir.Routine):
                held = self._lines.get(item.name)
                if kept and (held is None or held[0] is not item or not held[1].same(links)):
                    items.append(item)
                    home.append(item)
                else:  # its first node is all the others' links read
                    items.append(_stub(item))
                    home.append(None)
            elif kept:
                items.append(item)
                home.append(item)
        mini = ir.Program(p.row, p.base, items, p.end, p.sha1)
        lines = source.render(mini, h.name)
        spans = _groups(lines)
        if len(spans) != len(items) + 1:
            raise ValueError(f"row {p.row}: {len(spans)} groups of lines for {len(items)} items")
        drawn = {id(it): lines[a:b] for it, (a, b) in zip(home, spans[1:], strict=True) if it}
        out: list[tuple[Line, ...]] = [tuple(lines[spans[0][0] : spans[0][1]])]
        now: dict[str, tuple[ir.Routine, _Reads, tuple[Line, ...]]] = {}
        for item, kept in zip(p.items, keep[1:], strict=True):
            if not kept:
                out.append(())
            elif isinstance(item, ir.Routine):
                fresh = drawn.get(id(item))
                now[item.name] = (item, links, tuple(fresh)) if fresh else self._lines[item.name]
                out.append(now[item.name][2])
            else:
                out.append(tuple(drawn[id(item)]))
        self._lines = now
        return out


@dataclass(frozen=True)
class _Reads:
    """What the lines' links depend on besides their routine."""

    header: str
    lists: tuple[ir.Item, ...]
    first: tuple[tuple[str, str], ...]
    """Each routine's first node, by name."""

    def same(self, other: _Reads) -> bool:
        return (
            self.header == other.header
            and len(self.lists) == len(other.lists)
            and all(a is b for a, b in zip(self.lists, other.lists, strict=True))
            and self.first == other.first
        )


def _stub(r: ir.Routine) -> ir.Routine:
    """A routine that is only where its first node is, which is all a link to it reads."""
    return ir.Routine(r.name, [ir.Insn(_STUB, {}, r.body[0].id)] if r.body else [])


def view(
    p: ir.Program,
    header: str,
    edited: Collection[str] = (),
    renderer: Renderer | None = None,
) -> BrainView:
    """What `header` runs: the program's source with the links resolved through it, kept to its
    routines, lists and data. `edited` is the ids an edit set changes (`graph.edited_ids`); a node
    with no native address is edited too. `renderer` is the one kept for this header, where a
    view follows another of an edited program. `KeyError` for a header the program has not."""
    h = next((x for x in p.headers() if x.name == header), None)
    if h is None:
        raise KeyError(header)
    keep = [True, *_kept(p, h)]
    groups = (renderer or Renderer()).render(p, h, keep)
    lines: list[Line] = []
    routine_row: dict[str, int] = {}
    for item, group in zip([None, *p.items], groups, strict=True):
        if isinstance(item, ir.Routine) and group:
            routine_row[item.name] = len(lines)
        lines.extend(group)
    parent, children = _tree(lines)
    row_of = {ln.node: i for i, ln in enumerate(lines) if ln.node is not None}
    pairs: dict[int, tuple[int, int]] = {}
    ends: dict[int, int] = {}
    closes: dict[int, int] = {}
    flagged: set[int] = set()
    nodes: dict[str, ir.Located] = {}
    blocks: dict[str, ir.Block] = {}
    for n in p.nodes():
        if n.id not in row_of:
            continue
        nodes[n.id] = n
        if isinstance(n, ir.Block):
            blocks |= {a.id: n for a in n.arms}
        row = row_of[n.id]
        if n.pc is None or n.id in edited:
            flagged.add(row)
        if isinstance(n, ir.Block) and n.close is not None:
            closes[n.close] = row
        if (pr := pair_of(n)) is not None:
            pairs[row] = pr
            if isinstance(n, ir.Insn) and n.pc is not None:
                ends[n.pc + n.length] = row
    flagged |= {_top(parent, row) for row in flagged}
    flagged |= {routine_row[r.name] for r in p.routines() if r.va is None and r.name in routine_row}
    return BrainView(
        p.row,
        header,
        tuple(lines),
        tuple(parent),
        tuple(tuple(c) for c in children),
        tuple(i for i, up in enumerate(parent) if up < 0),
        row_of,
        tuple(_outline(h, p.tables(), routine_row)),
        pairs,
        at={ln.pc: i for i, ln in enumerate(lines) if ln.node is not None and ln.pc is not None},
        closes=closes,
        ends=ends,
        edited=frozenset(flagged),
        routine_row=routine_row,
        nodes=nodes,
        blocks=blocks,
    )


def _top(parent: Sequence[int], row: int) -> int:
    while parent[row] >= 0:
        row = parent[row]
    return row


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
