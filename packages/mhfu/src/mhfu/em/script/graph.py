# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The AI script as a graph the studio draws: each routine projected without loss into nodes,
ports and edges, and the program into an overview. No layout, no toolkit.

    proj = Projection(prog)                    # or Projection(applied, edited=edited_ids(es))
    g = proj.routine("call_3")                 # one routine alone; `header=` links as it sees them
    g.nodes[nid], g.out(nid), g.scopes[arm_id], g.crumbs(arm_id)
    ov = proj.overview()                       # entry points, groups, calls, who reaches whom

A routine is an `Entry`, a `Stmt` per instruction or block, and a `Join` after each block that
some way through leaves by its end. Edges: `next` to the following statement (a Move's is its
resume point), `port` from a block into the first node of each of its ports (an empty port goes
straight to the join), `join` from the last node of a port that falls through, `loop` from a
repeat's body back to the repeat, `link` from a call, goto, walk or route to the routine the
header's lists name. What ends a path is `check`'s rule: an End, a Jump or a block every way
through which ends has no `next`, and what follows is `dead`, with no edge in. An if with no else
and a switch with no default have an implicit port, which falls to the join.

Scopes nest: the routine is scope `ROOT`, a block's then or body `head_key(id)`, an arm its own
id. A scope lists its statements, so the tree is rebuilt from the nodes, ports and scopes alone;
`size` is the statements under a node, a scope or a port. A node is `edited` when it has no
native address or its id is in the edited ids; a block with such a node under it is `inner_edits`.
A graph holds copies, not the program's nodes, and its nodes are not hashable: key by id.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Collection
from dataclasses import dataclass, replace
from enum import Enum
from functools import cached_property
from typing import NamedTuple

from . import catalogue as cat
from . import ir

# the rules check and budget already hold, so the graph cannot drift from them
from .budget import _ROUTE_LISTS
from .check import _terminal
from .editset import Applied, EditSet
from .opcodes import LISTS, Flow, Op, Ref, Shape, operands, slot

__all__ = [
    "ENTRY",
    "LINKS",
    "ROOT",
    "Caller",
    "CallEdge",
    "Crumb",
    "Edge",
    "EdgeKind",
    "Entry",
    "EntryRoutine",
    "Group",
    "HeaderView",
    "Join",
    "Link",
    "ListSlot",
    "ListView",
    "Overview",
    "Port",
    "Projection",
    "RoutineGraph",
    "RoutineInfo",
    "Scope",
    "Stmt",
    "edited_ids",
    "head_key",
    "join_id",
]

ROOT = ""
"""The scope of a routine's own body."""
ENTRY = "entry"
"""The id of a routine's entry node."""
LINKS = ("goto", "call", "call_ret", "call_deep", "walk", "route", "call_spot")
"""The kinds of cross-routine link."""
_ROUTE = ("route_arm", "route_run")


def head_key(block: str) -> str:
    """The scope of a block's head: an if's then, a repeat's body."""
    return f"{block}#head"


def join_id(block: str) -> str:
    return f"{block}#join"


class EdgeKind(Enum):
    NEXT = "next"
    PORT = "port"
    JOIN = "join"
    LOOP = "loop"
    LINK = "link"


@dataclass(frozen=True)
class Link:
    """Where a link leads under some headers."""

    kind: str
    """One of `LINKS`."""
    role: str
    """The operand it is: a walk's `step` and `arrive`, a route's `body`; empty for the rest."""
    list: str
    """The header list it indexes: `base`, `call`, `ret`, `deep3`, `walk`, `loop`, `node`."""
    index: int | None
    """The slot; None where it runs every routine of the list (`route_run`, `call_spot`)."""
    target: str | None
    """The routine; None where the slot is empty, outside the program or not a routine."""
    headers: tuple[str, ...]
    """The headers under which it leads there."""


@dataclass(frozen=True)
class Edge:
    kind: EdgeKind
    src: str
    dst: str | None
    """A node of the graph; None for a link."""
    port: int | None = None
    """For a port edge, and a loop edge from an empty body: the index in the source's ports."""
    link: Link | None = None


@dataclass(frozen=True)
class Port:
    """What a block runs in one case: its head, an arm, or an implicit one."""

    name: str
    """The catalogue's: then, else, case 2, otherwise, 8/32."""
    scope: str | None
    """The scope of its body; None for an implicit port, which has none."""
    head: bool
    arm: str
    """The arm's id; empty for the head and an implicit port."""
    sub: int | None
    args: dict[str, ir.Value]
    """The arm's operands: a case's value, a branch's weight."""
    weight: int | None
    share: int | None
    """A pick's steps of the draw, of 32."""
    falls: bool
    """Whether control goes on to the join from here."""
    size: int
    pc: int | None
    origin: str | None
    edited: bool

    @classmethod
    def implicit(cls, name: str) -> Port:
        """The port of a block that has no arm for the case: it falls to the join."""
        return cls(
            name=name,
            scope=None,
            head=False,
            arm="",
            sub=None,
            args={},
            weight=None,
            share=None,
            falls=True,
            size=0,
            pc=None,
            origin=None,
            edited=False,
        )


@dataclass(frozen=True)
class Entry:
    """Where a routine starts."""

    routine: str
    id: str = ENTRY
    scope: str = ROOT
    depth: int = 0
    dead: bool = False
    falls_off: bool = False
    """The routine is empty."""


@dataclass(frozen=True)
class Join:
    """Where the ports of a block meet and control goes on."""

    id: str
    owner: str
    """The block it closes."""
    scope: str
    depth: int
    dead: bool
    falls_off: bool


@dataclass(frozen=True)
class Stmt:
    """An instruction or a block."""

    id: str
    kind: cat.Kind
    op: Op
    title: str
    summary: str
    confidence: cat.Confidence
    args: dict[str, ir.Value]
    pc: int | None
    origin: str | None
    close: int | None
    scope: str
    depth: int
    size: int
    ports: tuple[Port, ...]
    join: str | None
    end: cat.EndKind | None
    """What an `end` does."""
    chance: cat.Chance | None
    yields: bool
    """A Move or a walk saves its resume point and stops the run: `next` continues there."""
    terminal: bool
    dead: bool
    falls_off: bool
    """The routine can run off its end here."""
    edited: bool
    new: bool
    """Edited and not a native node's change: inserted or cloned code."""
    inner_edits: bool

    @property
    def block(self) -> bool:
        return self.op.coded


GNode = Entry | Stmt | Join


@dataclass(frozen=True)
class Scope:
    key: str
    parent: str | None
    owner: str | None
    """The block whose port it is; None for the routine."""
    port: int | None
    """The index in the owner's ports."""
    depth: int
    body: tuple[str, ...]
    """Its statements in order."""
    nodes: tuple[str, ...]
    """Its graph nodes in flow order: the entry, each statement and the join after a block."""
    size: int
    edited: bool


@dataclass(frozen=True)
class Crumb:
    label: str
    scope: str
    node: str | None = None
    """The block to select in `scope`; None for the routine and a port."""


@dataclass(frozen=True)
class RoutineGraph:
    name: str
    nodes: dict[str, GNode]
    """In flow order, parents before what is in them."""
    scopes: dict[str, Scope]
    edges: tuple[Edge, ...]
    headers: tuple[str, ...]
    """The headers its links are resolved through."""
    size: int
    edited: bool

    @cached_property
    def _out(self) -> dict[str, tuple[Edge, ...]]:
        by: dict[str, list[Edge]] = defaultdict(list)
        for e in self.edges:
            by[e.src].append(e)
        return {k: tuple(v) for k, v in by.items()}

    def out(self, nid: str) -> tuple[Edge, ...]:
        """The edges that leave a node."""
        return self._out.get(nid, ())

    def port(self, e: Edge) -> Port | None:
        """The port a port or loop edge starts at."""
        src = self.nodes[e.src]
        return src.ports[e.port] if isinstance(src, Stmt) and e.port is not None else None

    def crumbs(self, key: str) -> tuple[Crumb, ...]:
        """The way into a scope: the routine, then each block and the port taken."""
        chain: list[Crumb] = []
        while key != ROOT:
            s = self.scopes[key]
            if s.owner is None or s.port is None or s.parent is None:
                raise KeyError(key)
            owner = self.nodes[s.owner]
            if not isinstance(owner, Stmt):
                raise KeyError(key)
            chain += [Crumb(owner.ports[s.port].name, key), Crumb(owner.title, s.parent, owner.id)]
            key = s.parent
        return (Crumb(self.name, ROOT), *reversed(chain))


# ---- links


@dataclass(frozen=True)
class _Site:
    kind: str
    role: str
    field: int
    index: int | None


def _link_kind(op: Op) -> str:
    if op.name in _ROUTE:
        return "route"
    return "goto" if op.flow is Flow.GOTO else op.name


def _tagged(n: ir.Node) -> list[str]:
    """The names of the list operands that `ir.refs` gives a ref for, in its order."""
    op = n.op
    layout = op.args if op.coded else op.plain_layout(n.args)
    return [o.name for o in operands(layout) if o.ref and isinstance(n.args.get(o.name), int)]


def _sites(n: ir.Node) -> tuple[_Site, ...]:
    """The routines an instruction leads to, by list and slot; data (a stage array) is no link."""
    op = n.op
    if op.name in _ROUTE_LISTS:
        return (_Site(_link_kind(op), "", LISTS[_ROUTE_LISTS[op.name]], None),)
    refs = ir.refs(n)
    if not refs:
        return ()
    names = _tagged(n)
    out = []
    for i, r in enumerate(refs):
        if r.kind == "stages" or slot(r.field).kind == "data":
            continue
        role = names[i - len(op.fixed)] if i >= len(op.fixed) else ""
        out.append(_Site(_link_kind(op), "" if role == "routine" else role, r.field, r.index))
    return tuple(out)


# ---- a routine


class _At(NamedTuple):
    """A list of nodes: the scope it makes and where that sits."""

    key: str
    parent: str | None
    owner: str | None
    port: int | None
    depth: int
    dead: bool
    marked: bool
    """The arm it is the body of is edited."""


class _Body(NamedTuple):
    first: str | None
    tail: str | None
    """The node control runs off the end of the list from; None where the list ends before."""
    falls: bool
    size: int
    dirty: bool


class _Build:
    def __init__(self, proj: Projection, r: ir.Routine, seen: tuple[ir.Header, ...]) -> None:
        self.proj, self.r, self.seen = proj, r, seen
        self.made: dict[str, GNode] = {}
        self.order: list[str] = []
        self.scopes: dict[str, Scope] = {}
        self.edges: list[Edge] = []

    def edge(self, kind: EdgeKind, src: str, dst: str | None, port: int | None = None) -> None:
        self.edges.append(Edge(kind, src, dst, port))

    def run(self) -> RoutineGraph:
        name = self.r.name
        self.order.append(ENTRY)
        self.made[ENTRY] = Entry(name)
        body = self.body(self.r.body, _At(ROOT, None, None, None, 0, False, False))
        if body.first is None:
            self.made[ENTRY] = Entry(name, falls_off=True)
        else:
            self.edge(EdgeKind.NEXT, ENTRY, body.first)
        if body.tail is not None:
            self.made[body.tail] = replace(self.made[body.tail], falls_off=True)
        root = self.scopes[ROOT]
        self.scopes[ROOT] = replace(root, nodes=(ENTRY, *root.nodes))
        return RoutineGraph(
            name,
            {k: self.made[k] for k in self.order},
            self.scopes,
            tuple(self.edges),
            tuple(h.name for h in self.seen),
            body.size,
            body.dirty or name in self.proj.touched,
        )

    def body(self, nodes: list[ir.Node], at: _At) -> _Body:
        """A list of nodes as a scope, its statements chained by `next`."""
        stmts: list[str] = []
        flow: list[str] = []
        first: str | None = None
        flowing: str | None = None
        ended, size, dirty = False, 0, at.marked
        for n in nodes:
            exit_, got, inner = self.stmt(n, at.key, at.depth, at.dead or ended)
            if first is None:
                first = n.id
            if flowing is not None:
                self.edge(EdgeKind.NEXT, flowing, n.id)
            flowing = exit_
            ended = ended or exit_ is None
            stmts.append(n.id)
            flow.append(n.id)
            if exit_ is not None and exit_ != n.id:
                flow.append(exit_)
            size += got
            dirty = dirty or inner
        self.scopes[at.key] = Scope(
            at.key, at.parent, at.owner, at.port, at.depth, tuple(stmts), tuple(flow), size, dirty
        )
        return _Body(first, None if ended else flowing, not ended, size, dirty)

    def stmt(self, n: ir.Node, key: str, depth: int, dead: bool) -> tuple[str | None, int, bool]:
        """Build a statement and all under it: its exit, None where it is terminal, its size and
        whether it or anything in it is edited."""
        if not n.id:
            raise ValueError(f"{self.r.name}: a node has no id")
        self.order.append(n.id)
        terminal = _terminal(n)
        ports: tuple[Port, ...] = ()
        join: str | None = None
        size, inner = 1, False
        if isinstance(n, ir.Block):
            ports, join, more, inner = self.block(n, key, depth, dead, terminal)
            size += more
        edited = self.proj._flagged(n)
        self.made[n.id] = Stmt(
            id=n.id,
            kind=cat.kind_of(n),
            op=n.op,
            title=cat.title(n),
            summary=cat.summary(n, self.proj.pairs),
            confidence=cat.confidence(n),
            args=dict(n.args),
            pc=n.pc,
            origin=n.origin,
            close=n.close if isinstance(n, ir.Block) else None,
            scope=key,
            depth=depth,
            size=size,
            ports=ports,
            join=join,
            end=_end_of(n),
            chance=cat.chance(n) if isinstance(n, ir.Block) else None,
            yields=n.op.resume,
            terminal=terminal,
            dead=dead,
            falls_off=False,
            edited=edited,
            new=n.pc is None and n.origin != n.id,
            inner_edits=inner,
        )
        self.links(n)
        if join is not None:
            self.order.append(join)
            self.made[join] = Join(join, n.id, key, depth, dead, False)
        return (None if terminal else join or n.id), size, edited or inner

    def block(
        self, b: ir.Block, key: str, depth: int, dead: bool, terminal: bool
    ) -> tuple[tuple[Port, ...], str | None, int, bool]:
        """The ports of a block, each with its body a scope, and the join they meet at."""
        shape = b.op.shape
        loop = shape is Shape.REPEAT
        spec = cat.ports(b)
        wanted: list[tuple[str, list[ir.Node] | None, int | None]] = []
        if spec.head is not None:
            wanted.append((spec.head, b.head, None))
        wanted += [
            (name, a.body, k) for k, (a, name) in enumerate(zip(b.arms, spec.arms, strict=True))
        ]
        if (shape is Shape.IF and not b.arms) or (
            shape is Shape.SWITCH and not any(a.sub == 2 for a in b.arms)
        ):
            wanted.append((_implicit(b), None, None))
        join = None if terminal or loop or not wanted else join_id(b.id)
        weights = [a.args.get("weight") for a in b.arms] if shape is Shape.RANDOM else []
        steps = [w for w in weights if isinstance(w, int)]
        shares = cat.shares(steps) if weights and len(steps) == len(weights) else ()
        ports: list[Port] = []
        size, dirty = 0, False
        for i, (name, body, k) in enumerate(wanted):
            arm = b.arms[k] if k is not None else None
            if body is None:
                if join is not None:
                    self.edge(EdgeKind.PORT, b.id, join, i)
                ports.append(Port.implicit(name))
                continue
            skey = head_key(b.id) if arm is None else arm.id
            marked = arm is not None and self.proj._flagged(arm)
            sub = self.body(body, _At(skey, key, b.id, i, depth + 1, dead, marked))
            if sub.first is not None:
                self.edge(EdgeKind.PORT, b.id, sub.first, i)
            elif loop:
                self.edge(EdgeKind.LOOP, b.id, b.id, i)
            elif join is not None:
                self.edge(EdgeKind.PORT, b.id, join, i)
            if sub.tail is not None:
                if loop:
                    self.edge(EdgeKind.LOOP, sub.tail, b.id)
                elif join is not None:
                    self.edge(EdgeKind.JOIN, sub.tail, join)
            weight = weights[k] if k is not None and weights else None
            ports.append(
                Port(
                    name=name,
                    scope=skey,
                    head=arm is None,
                    arm="" if arm is None else arm.id,
                    sub=None if arm is None else arm.sub,
                    args={} if arm is None else dict(arm.args),
                    weight=weight if isinstance(weight, int) else None,
                    share=shares[k] if k is not None and shares else None,
                    falls=sub.falls,
                    size=sub.size,
                    pc=None if arm is None else arm.pc,
                    origin=None if arm is None else arm.origin,
                    edited=marked,
                )
            )
            size += sub.size
            dirty = dirty or sub.dirty
        return tuple(ports), join, size, dirty

    def links(self, n: ir.Node) -> None:
        for s in _sites(n):
            by: dict[str | None, list[str]] = {}
            for h in self.seen:
                for target in self.proj._targets(h, s):
                    by.setdefault(target, []).append(h.name)
            for target, names in (by or {None: []}).items():
                link = Link(s.kind, s.role, slot(s.field).name, s.index, target, tuple(names))
                self.edges.append(Edge(EdgeKind.LINK, n.id, None, None, link))


def _end_of(n: ir.Node) -> cat.EndKind | None:
    if isinstance(n, ir.Insn) and n.op.flow is Flow.END:
        kind = n.args.get("kind")
        return cat.end_kind(kind) if isinstance(kind, int) else None
    return None


def _implicit(b: ir.Block) -> str:
    """The name of the port a block has no arm for: an if's else, a switch's default."""
    arm = ir.Arm(1 if b.op.shape is Shape.IF else 2, {}, [])
    return cat.ports(replace(b, arms=[*b.arms, arm])).arms[-1]


# ---- overview


@dataclass(frozen=True)
class Caller:
    routine: str
    sites: int


@dataclass(frozen=True)
class RoutineInfo:
    name: str
    nodes: int
    headers: tuple[str, ...]
    """The headers that run it."""
    variants: tuple[int, ...]
    """The map types of those headers: an edit here changes all of them."""
    callers: tuple[Caller, ...]
    """The other routines that lead to it, by link sites."""
    entries: tuple[str, ...]
    """The entry points the game starts it as: Top, On notice."""
    edited: bool
    new: bool

    @property
    def reached_from(self) -> int:
        return len(self.callers)

    @property
    def shared(self) -> bool:
        """Run by several headers."""
        return len(self.headers) > 1


@dataclass(frozen=True)
class CallEdge:
    src: str
    dst: str
    kind: str
    count: int


@dataclass(frozen=True)
class EntryRoutine:
    point: cat.EntryPoint
    routine: str | None
    """None where the header has no list there or the slot is empty."""


@dataclass(frozen=True)
class ListSlot:
    index: int
    name: str | None
    kind: str
    """routine, data, extern or empty."""


@dataclass(frozen=True)
class ListView:
    field: int
    table: str
    info: cat.ListInfo
    slots: tuple[ListSlot, ...]


@dataclass(frozen=True)
class Group:
    name: str
    """One of `catalogue.GROUPS`."""
    lists: tuple[ListView, ...]
    routines: tuple[str, ...]
    """Each once, in slot order."""


@dataclass(frozen=True)
class HeaderView:
    name: str
    variants: tuple[int, ...]
    entry_points: tuple[EntryRoutine, ...]
    groups: tuple[Group, ...]
    routines: tuple[str, ...]
    """Everything the header runs."""
    unlisted: tuple[str, ...]
    """Run through links only: in none of its lists."""
    calls: tuple[CallEdge, ...]


@dataclass(frozen=True)
class Overview:
    row: int
    headers: tuple[HeaderView, ...]
    routines: dict[str, RoutineInfo]

    def header(self, name: str) -> HeaderView:
        return next(h for h in self.headers if h.name == name)


def edited_ids(by: EditSet | Applied) -> frozenset[str]:
    """The ids an edit changes: the native nodes an edit set edits, or in an applied program the
    new code of the routines it touched."""
    if isinstance(by, EditSet):
        return frozenset(k for k, v in by.nodes.items() if not v.is_empty())
    touched = set(by.touched)
    return frozenset(
        x.id
        for r in by.program.routines()
        if r.name in touched
        for x in ir.walk(r.body)
        if x.pc is None and x.id
    )


class Projection:
    """A program's graphs, one routine at a time. Build another after an edit: it holds what the
    program had."""

    def __init__(
        self,
        source: ir.Program | Applied,
        edited: Collection[str] = (),
        pairs: cat.Pairs | None = None,
    ) -> None:
        self.p = source.program if isinstance(source, Applied) else source
        self.touched = frozenset(source.touched) if isinstance(source, Applied) else frozenset()
        self.edited = frozenset(edited)
        self.pairs = pairs
        self.routines = {r.name: r for r in self.p.routines()}
        self.headers = {h.name: h for h in self.p.headers()}
        self.tables = self.p.tables()
        self._graphs: dict[tuple[str, str | None], RoutineGraph] = {}
        self._names: dict[tuple[str, int, int | None], tuple[str | None, ...]] = {}
        self._overview: Overview | None = None

    @cached_property
    def reach(self) -> dict[str, tuple[str, ...]]:
        return self.p.reach()

    def routine(self, name: str, header: str | None = None) -> RoutineGraph:
        """The graph of a routine; its links are resolved through `header`, by default through
        every header that runs it (all of them where none does). KeyError for an unknown name."""
        key = (name, header)
        if key not in self._graphs:
            r = self.routines[name]
            seen: tuple[ir.Header, ...] = ()
            if header is not None:
                seen = (self.headers[header],)
            else:
                run = self.reach.get(name, ())
                seen = tuple(self.headers[n] for n in run) or tuple(self.headers.values())
            self._graphs[key] = _Build(self, r, seen).run()
        return self._graphs[key]

    def _targets(self, h: ir.Header, s: _Site) -> tuple[str | None, ...]:
        """The routines a link site leads to under one header; None for a slot with nothing."""
        key = (h.name, s.field, s.index)
        if key not in self._names:
            if s.index is None:
                t = h.fields[s.field] if s.field < len(h.fields) else None
                slots = self.tables[t].slots if isinstance(t, str) and t in self.tables else []
                names = [x for x in slots if isinstance(x, str) and x in self.routines]
                self._names[key] = tuple(dict.fromkeys(names)) or (None,)
            else:
                found = self.p.entry(h, Ref("goto", s.field, s.index), self.tables)
                self._names[key] = (found if found in self.routines else None,)
        return self._names[key]

    def _flagged(self, x: ir.Located) -> bool:
        return x.pc is None or x.id in self.edited

    def overview(self) -> Overview:
        if self._overview is None:
            self._overview = self._build_overview()
        return self._overview

    def _build_overview(self) -> Overview:
        p, reach = self.p, self.reach
        sites = {
            r.name: [
                (n.id, k, s)
                for n in ir.walk(r.body)
                if isinstance(n, ir.Insn | ir.Block)
                for k, s in enumerate(_sites(n))
            ]
            for r in p.routines()
        }
        callers: dict[str, Counter[str]] = defaultdict(Counter)
        counted: set[tuple[str, str, int, str]] = set()
        views = []
        for h in p.headers():
            names = tuple(r.name for r in p.routines() if h.name in reach[r.name])
            calls: Counter[tuple[str, str, str]] = Counter()
            for src in names:
                for nid, k, s in sites[src]:
                    for dst in self._targets(h, s):
                        if dst is None:
                            continue
                        calls[(src, dst, s.kind)] += 1
                        if dst != src and (src, nid, k, dst) not in counted:
                            counted.add((src, nid, k, dst))
                            callers[dst][src] += 1
            groups = self._groups(h)
            listed = {n for g in groups for n in g.routines}
            views.append(
                HeaderView(
                    h.name,
                    h.variants,
                    self._entry_points(h),
                    groups,
                    names,
                    tuple(n for n in names if n not in listed),
                    tuple(CallEdge(*k, c) for k, c in calls.items()),
                )
            )
        entries: dict[str, dict[str, None]] = defaultdict(dict)
        for v in views:
            for e in v.entry_points:
                if e.routine is not None:
                    entries[e.routine][e.point.title] = None
        infos = {}
        for r in p.routines():
            nodes, edited = 0, r.name in self.touched
            for x in ir.walk(r.body):
                nodes += isinstance(x, ir.Insn | ir.Block)
                edited = edited or self._flagged(x)
            hs = reach[r.name]
            infos[r.name] = RoutineInfo(
                r.name,
                nodes,
                hs,
                tuple(sorted({v for n in hs for v in self.headers[n].variants})),
                tuple(Caller(src, c) for src, c in callers[r.name].items()),
                tuple(entries[r.name]),
                edited,
                r.va is None,
            )
        return Overview(p.row, tuple(views), infos)

    def _entry_points(self, h: ir.Header) -> tuple[EntryRoutine, ...]:
        out = []
        for pt in cat.ENTRY_POINTS:
            name = self.p.entry(h, Ref("goto", LISTS[pt.list_name], pt.index), self.tables)
            out.append(EntryRoutine(pt, name if name in self.routines else None))
        return tuple(out)

    def _groups(self, h: ir.Header) -> tuple[Group, ...]:
        by: dict[str, list[ListView]] = {}
        for f, t in enumerate(h.fields):
            if not isinstance(t, str) or t not in self.tables:
                continue
            info = cat.list_info(f)
            slots = tuple(self._slot(i, e) for i, e in enumerate(self.tables[t].slots))
            by.setdefault(info.group, []).append(ListView(f, t, info, slots))
        return tuple(
            Group(
                g,
                tuple(by[g]),
                tuple(
                    dict.fromkeys(
                        s.name
                        for v in by[g]
                        for s in v.slots
                        if s.name is not None and s.kind == "routine"
                    )
                ),
            )
            for g in cat.GROUPS
            if g in by
        )

    def _slot(self, index: int, e: ir.Entry) -> ListSlot:
        match e:
            case None:
                return ListSlot(index, None, "empty")
            case ir.External():
                return ListSlot(index, None, "extern")
            case _:
                return ListSlot(index, e, "routine" if e in self.routines else "data")
