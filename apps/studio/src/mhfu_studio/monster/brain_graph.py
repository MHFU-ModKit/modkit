# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""What the Brain tab's graph pane and outline show of the native AI script, laid out. No toolkit:
the pane draws these specs and the outline lists these rows.

    proj = Projection(built.applied, edited=..., pairs=...)   # `mhfu.em.script.graph`
    scene(proj.routine("base_0"), ROOT)                       # one scope: a routine or an arm
    plan(rg, key).place(sizes)                                # the second pass, with drawn sizes
    outline(proj.overview(), "h0", names=bf_names)            # the outline, by meaning

A scope is a column of statements top to bottom, the routine's own or an arm's. A block whose arms
fit (`collapse` nodes or fewer) is drawn in line: its arms are framed columns to the right of
the column, each under the block, and its join sits below them in the column again. A bigger
block is one compact node that opens as the scopes of its arms. The layout is never stored: the
same program gives the same scene. `place` takes the sizes the pane drew the nodes at (the
Behaviour tab's two passes); `scene` takes the estimate. Link edges are badges, not wires.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import cached_property

from mhfu.em.script import catalogue as cat
from mhfu.em.script import source
from mhfu.em.script.check import Finding
from mhfu.em.script.graph import (
    ENTRY,
    ROOT,
    Caller,
    Crumb,
    Edge,
    EdgeKind,
    Entry,
    Join,
    ListSlot,
    ListView,
    Overview,
    RoutineGraph,
    Stmt,
    head_key,
)

Point = tuple[float, float]
Size = tuple[float, float]

COLLAPSE = 12
"""Nodes a block may hold and still be drawn in line."""
IN, OUT = "in", "out"
"""A node's input, and its one output when it has no ports; a block's ports are `p<i>`."""
LEVELS = ("error", "warning", "info")
"""A finding's levels, worst first."""
UNLISTED = "Linked only"
"""The outline's section for routines no list names, which are reached by links alone."""
DEEP = "deep"

# the pane's own sizes (panels/node_graph.py); the estimate stands in until it has drawn them
NODE_W, TITLE_H, LINE_H = 168.0, 38.0, 14.0
SUMMARY_COLS, SUMMARY_LINES = 24, 2
JOIN_H = 30.0
FRAME_PAD, FRAME_TOP, FRAME_BOTTOM = 18.0, 38.0, 14.0
NODE_GAP, COLUMN_GAP, ARM_DROP = 26.0, 46.0, 24.0
"""The room between a column's nodes, between two columns, and from a block down to its arms'
frames (the arms' wires run in the middle of it)."""
EDGE = 14.0
"""How far a loop's wire stands off its node."""


def named(routine: str, labels: Mapping[str, str]) -> str:
    """A routine as the user knows it: `Charge combo (call_3)`, or its name where it has none."""
    return _called(routine, labels.get(routine, ""))


def _called(routine: str, label: str) -> str:
    return f"{label} ({routine})" if label else routine


# ---- specs


@dataclass(frozen=True)
class PortSpec:
    """One output of a block: where one of its cases leads."""

    key: str
    """Its name on the node, `p<i>`; a catalogue name may repeat (two branches of 8/32)."""
    name: str
    """The catalogue's: then, else, case 2, otherwise, 8/32."""
    share: int | None
    """A pick's steps of the draw, of `cat.DRAW`."""
    weight: int | None
    scope: str | None
    """The scope its body is; None for a case the block has no arm for."""
    arm: str
    """The arm's id; empty for the head and an implicit case."""
    size: int
    falls: bool
    """Control goes on to the join from here."""
    edited: bool


@dataclass(frozen=True)
class Open:
    """A way into a compact block: the scope of one of its ports."""

    scope: str
    name: str
    size: int


@dataclass(frozen=True)
class LinkBadge:
    """Where a call, goto or walk leads, as a badge on its node."""

    kind: str
    role: str
    list: str
    index: int | None
    target: str | None
    """The routine; None for an empty slot or one outside the program."""
    text: str
    headers: tuple[str, ...]


@dataclass(frozen=True)
class Box:
    """One node as the pane draws it. Its wires start at `ports` (a block) or `out`, and end at
    `in`."""

    id: str
    kind: str
    """The role the pane tints it by: a catalogue kind in lower case, or entry, join."""
    title: str
    summary: str
    at: Point
    size: Size
    ports: tuple[PortSpec, ...] = ()
    inputs: tuple[str, ...] = (IN,)
    exit: bool = False
    """Control leaves it through `out`."""
    badge: str = ""
    links: tuple[LinkBadge, ...] = ()
    compact: bool = False
    """A block drawn as one node, `opens` its scopes."""
    opens: tuple[Open, ...] = ()
    nodes: int = 1
    """The statements it stands for."""
    edited: bool = False
    new: bool = False
    inner: bool = False
    """Something under it is edited (a compact block shows it)."""
    dead: bool = False
    falls_off: bool = False
    terminal: bool = False
    confidence: cat.Confidence = cat.Confidence.HIGH
    """A compact block: the lowest of what it holds."""
    findings: int = 0
    """A compact block: counting what it holds."""
    level: str | None = None
    """The worst of its findings."""
    tip: str = ""

    @property
    def outputs(self) -> tuple[str, ...]:
        return (*(p.key for p in self.ports), *((OUT,) if self.exit else ()))

    @property
    def rect(self) -> tuple[float, float, float, float]:
        return (*self.at, *self.size)


@dataclass(frozen=True)
class Frame:
    """An arm's frame: its title, the nodes in it (nested arms' too) and where it is."""

    id: str
    """The arm's scope."""
    title: str
    nodes: tuple[str, ...]
    role: str
    at: Point
    size: Size
    depth: int
    edited: bool

    @property
    def rect(self) -> tuple[float, float, float, float]:
        return (*self.at, *self.size)


@dataclass(frozen=True)
class Wire:
    """A flow edge as a polyline from the anchor of `src_port` to the anchor of `dst_port`."""

    kind: EdgeKind
    src: str
    src_port: str
    dst: str
    dst_port: str
    points: tuple[Point, ...]


@dataclass(frozen=True)
class Scene:
    """One scope, drawn."""

    routine: str
    scope: str
    crumbs: tuple[Crumb, ...]
    nodes: tuple[Box, ...]
    """In flow order."""
    frames: tuple[Frame, ...]
    """Outermost first."""
    wires: tuple[Wire, ...]
    size: Size
    plan: Plan = field(compare=False, repr=False)

    def box(self, nid: str) -> Box:
        return self._by_id[nid]

    @cached_property
    def _by_id(self) -> dict[str, Box]:
        return {b.id: b for b in self.nodes}

    def placed(self, sizes: Mapping[str, Size]) -> Scene:
        """The same scene laid out again at the sizes the pane drew its nodes at."""
        return self.plan.place(sizes)


# ---- what a scope shows


def _stmt(rg: RoutineGraph, nid: str) -> Stmt:
    n = rg.nodes[nid]
    if not isinstance(n, Stmt):
        raise KeyError(nid)
    return n


def _shown(rg: RoutineGraph, key: str, collapse: int) -> tuple[tuple[str, ...], frozenset[str]]:
    """The nodes a scope draws, in flow order, and the blocks it draws compact (which have no
    join of their own: the node is the whole block)."""
    ids: list[str] = [ENTRY] if key == ROOT else []
    compact: set[str] = set()

    def go(k: str) -> None:
        for sid in rg.scopes[k].body:
            s = _stmt(rg, sid)
            ids.append(sid)
            if not s.block:
                continue
            if s.size > collapse:
                compact.add(sid)
                continue
            for port in s.ports:
                if port.scope is not None:
                    go(port.scope)
            if s.join is not None:
                ids.append(s.join)

    go(key)
    return tuple(ids), frozenset(compact)


def _under(rg: RoutineGraph, s: Stmt) -> Iterator[Stmt]:
    """The statements inside a block, at any depth."""
    for port in s.ports:
        if port.scope is None:
            continue
        for sid in rg.scopes[port.scope].body:
            inner = _stmt(rg, sid)
            yield inner
            yield from _under(rg, inner)


def _owners(rg: RoutineGraph) -> dict[str, str]:
    """The block each arm id belongs to."""
    return {p.arm: n.id for n in rg.nodes.values() if isinstance(n, Stmt) for p in n.ports if p.arm}


def _tally(rg: RoutineGraph, findings: Sequence[Finding]) -> dict[str, list[Finding]]:
    """The findings of this routine by the node they are about: an arm's by its block's, a
    routine's own by the entry."""
    owner = _owners(rg)
    out: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        if f.node is None:
            if f.item == rg.name:
                out[ENTRY].append(f)
            continue
        nid = owner.get(f.node, f.node)
        if nid in rg.nodes and (f.item is None or f.item == rg.name):
            out[nid].append(f)
    return out


def _worst(found: Sequence[Finding]) -> str | None:
    return min((f.level for f in found), key=_rank, default=None)


def _rank(level: str) -> int:
    return LEVELS.index(level) if level in LEVELS else len(LEVELS)


def estimate(summary: str) -> Size:
    """The size of a node with this summary as the pane draws it: a title and up to two lines."""
    lines = min(SUMMARY_LINES, math.ceil(len(summary) / SUMMARY_COLS)) if summary else 0
    return NODE_W, TITLE_H + LINE_H * lines


def _brief(title: str, summary: str) -> str:
    """A node's summary without its title, which the node shows above it."""
    if summary == title:
        return ""
    return summary.removeprefix(f"{title}: ")


def _badge(links: Sequence[LinkBadge]) -> str:
    """A node's links on its title bar: `→ call_3, call_4 +2`."""
    to = [k.target or "?" for k in links]
    return "→ " + ", ".join(to[:2]) + (f" +{len(to) - 2}" if len(to) > 2 else "") if to else ""


def _links(rg: RoutineGraph, nid: str, labels: Mapping[str, str]) -> tuple[LinkBadge, ...]:
    out = []
    for e in rg.out(nid):
        if e.kind is not EdgeKind.LINK or e.link is None:
            continue
        k = e.link
        way = f"{k.kind} {k.role}".strip()
        at = f"{k.list}[{k.index}]" if k.index is not None else k.list
        text = f"{way} to {named(k.target, labels)}" if k.target else f"{way} to {at}: nothing"
        out.append(LinkBadge(k.kind, k.role, k.list, k.index, k.target, text, k.headers))
    return tuple(out)


def _ports(s: Stmt) -> tuple[PortSpec, ...]:
    return tuple(
        PortSpec(
            f"p{i}",
            p.name,
            p.share,
            p.weight,
            p.scope,
            p.arm,
            p.size,
            p.falls,
            p.edited,
        )
        for i, p in enumerate(s.ports)
    )


def _tip(meaning: str, conf: cat.Confidence, found: Sequence[Finding]) -> str:
    lines = [meaning]
    if conf is not cat.Confidence.HIGH:
        lines.append(f"{conf.label} confidence in what this means")
    lines += [f"{f.level}: {f.text}" for f in found]
    return "\n".join(lines)


class _Boxes:
    """A scope's nodes, as `plan` makes them; geometry is `place`'s."""

    def __init__(
        self,
        rg: RoutineGraph,
        key: str,
        collapse: int,
        findings: Sequence[Finding],
        labels: Mapping[str, str],
    ) -> None:
        self.rg, self.key, self.collapse, self.labels = rg, key, collapse, labels
        self.found = _tally(rg, findings)
        self.ids, self.compact = _shown(rg, key, collapse)

    def boxes(self) -> list[Box]:
        return [self.box(nid) for nid in self.ids]

    def box(self, nid: str) -> Box:
        n = self.rg.nodes[nid]
        if isinstance(n, Entry):
            return self.entry(n)
        if isinstance(n, Join):
            return self.join(n)
        return self.stmt(_stmt(self.rg, nid))

    def entry(self, e: Entry) -> Box:
        found = self.found.get(ENTRY, [])
        summary = "empty" if e.falls_off else f"{self.rg.size} nodes"
        return Box(
            ENTRY,
            "entry",
            named(self.rg.name, self.labels),
            summary,
            (0.0, 0.0),
            estimate(summary),
            exit=not e.falls_off,
            inputs=(),
            nodes=0,
            edited=self.rg.edited,
            dead=e.dead,
            falls_off=e.falls_off,
            findings=len(found),
            level=_worst(found),
            tip=_tip("where the routine starts", cat.Confidence.HIGH, found),
        )

    def join(self, j: Join) -> Box:
        owner = _stmt(self.rg, j.owner)
        summary = f"after {owner.title}"
        return Box(
            j.id,
            "join",
            "Join",
            summary,
            (0.0, 0.0),
            (NODE_W, JOIN_H),
            exit=True,
            nodes=0,
            dead=j.dead,
            falls_off=j.falls_off,
            tip="where the cases of the block meet",
        )

    def stmt(self, s: Stmt) -> Box:
        compact = s.id in self.compact
        inside = list(_under(self.rg, s)) if compact else []
        found = [*self.found.get(s.id, [])]
        for x in inside:
            found += self.found.get(x.id, [])
        conf = min([s.confidence, *(x.confidence for x in inside)])
        links = _links(self.rg, s.id, self.labels)
        summary = self.summary(s) if compact else _brief(s.title, s.summary)
        ports = () if compact else _ports(s)
        exits = not s.terminal and (compact or s.join is None)
        return Box(
            s.id,
            s.kind.name.lower(),
            s.title,
            summary,
            (0.0, 0.0),
            estimate(summary),
            ports=ports,
            exit=exits,
            badge=_badge(links),
            links=links,
            compact=compact,
            opens=tuple(Open(p.scope, p.name, p.size) for p in s.ports if p.scope and compact),
            nodes=s.size,
            edited=s.edited,
            new=s.new,
            inner=s.inner_edits or any(x.edited for x in inside),
            dead=s.dead,
            falls_off=s.falls_off,
            terminal=s.terminal,
            confidence=conf,
            findings=len(found),
            level=_worst(found),
            tip=_tip(cat.entry(s.op).meaning, conf, found),
        )

    @staticmethod
    def summary(s: Stmt) -> str:
        """`random · 4 branches · 37 nodes`: what is inside, as one line."""
        parts = [x for x in [_brief(s.title, s.summary)] if x]
        if len(s.ports) > 1:
            parts.append(f"{len(s.ports)} branches")
        return " · ".join([*parts, f"{s.size} nodes"])


@dataclass(frozen=True)
class Plan:
    """A scope's nodes before they are placed, as the Behaviour tab's `rows.Plan` is."""

    rg: RoutineGraph
    scope: str
    collapse: int
    crumbs: tuple[Crumb, ...]
    boxes: tuple[Box, ...]
    """At the origin, at the estimated size."""
    compact: frozenset[str]

    def place(self, sizes: Mapping[str, Size] | None = None) -> Scene:
        """Positions from the drawn `sizes` (the estimate for a node not in them)."""
        return _place(self, sizes or {})


def plan(
    rg: RoutineGraph,
    scope: str = ROOT,
    collapse: int = COLLAPSE,
    findings: Sequence[Finding] = (),
    labels: Mapping[str, str] | None = None,
) -> Plan:
    """The scope `scope` of a routine's graph: its nodes, the blocks over `collapse` nodes
    compact. `findings` count on the node they name, `labels` are the user's names for routines.
    KeyError for a scope the routine has not."""
    names = labels or {}
    p = _Boxes(rg, scope, collapse, findings, names)
    crumbs = rg.crumbs(scope)
    crumbs = (replace(crumbs[0], label=named(rg.name, names)), *crumbs[1:])
    return Plan(rg, scope, collapse, crumbs, tuple(p.boxes()), p.compact)


def scene(
    rg: RoutineGraph,
    scope: str = ROOT,
    collapse: int = COLLAPSE,
    findings: Sequence[Finding] = (),
    labels: Mapping[str, str] | None = None,
) -> Scene:
    """`plan` placed at the estimated sizes."""
    return plan(rg, scope, collapse, findings, labels).place()


# ---- layout


class _Layout:
    def __init__(self, p: Plan, sizes: Mapping[str, Size]) -> None:
        self.p, self.rg = p, p.rg
        self.boxes = {b.id: b for b in p.boxes}
        self.sizes = {b.id: sizes.get(b.id, b.size) for b in p.boxes}
        self.at: dict[str, Point] = {}
        self.frames: list[Frame] = []

    def column(self, key: str, x: float, y: float) -> tuple[Size, list[str]]:
        """The statements of a scope as a column from (x, y): its extent, the ids placed."""
        body = self.rg.scopes[key].body
        placed: list[str] = []
        stack = [*([ENTRY] if key == ROOT else []), *body]
        joins = [j for sid in body if (j := self.join_of(sid)) is not None]
        spine = max((self.sizes[i][0] for i in (*stack, *joins)), default=0.0)
        right, at = x + spine, y
        for sid in stack:
            w, h = self.sizes[sid]
            self.at[sid] = (x, at)
            placed.append(sid)
            end = at + h
            if sid != ENTRY and (b := _stmt(self.rg, sid)).block and sid not in self.p.compact:
                end, right = self.arms(b, x, spine, end, right, placed)
            at = end + NODE_GAP
        return (right - x, max(at - NODE_GAP - y, 0.0)), placed

    def join_of(self, sid: str) -> str | None:
        n = self.rg.nodes[sid]
        return n.join if isinstance(n, Stmt) and sid not in self.p.compact else None

    def arms(
        self, b: Stmt, x: float, spine: float, end: float, right: float, placed: list[str]
    ) -> tuple[float, float]:
        """The arms of an in-line block in frames to the right of the column, then its join below
        them: the bottom of the block and the right edge of the column."""
        left, bottom = x + spine + COLUMN_GAP, end
        for port in b.ports:
            key = port.scope
            if key is None or not self.rg.scopes[key].body:
                continue
            top = end + ARM_DROP
            (w, h), inner = self.column(key, left + FRAME_PAD, top + FRAME_TOP)
            size = (w + 2 * FRAME_PAD, FRAME_TOP + h + FRAME_BOTTOM)
            depth = self.rg.scopes[key].depth
            role = self.boxes[b.id].kind
            self.frames.append(
                Frame(key, port.name, tuple(inner), role, (left, top), size, depth, port.edited)
            )
            placed += inner
            bottom = max(bottom, top + size[1])
            right = max(right, left + size[0])
            left += size[0] + COLUMN_GAP
        if b.join is not None:
            self.at[b.join] = (x, bottom + NODE_GAP)
            placed.append(b.join)
            bottom += NODE_GAP + self.sizes[b.join][1]
        return bottom, right


def _place(p: Plan, sizes: Mapping[str, Size]) -> Scene:
    lay = _Layout(p, sizes)
    (w, h), _ = lay.column(p.scope, 0.0, 0.0)
    boxes = tuple(replace(b, at=lay.at[b.id], size=lay.sizes[b.id]) for b in p.boxes)
    frames = tuple(sorted(lay.frames, key=lambda f: f.depth))
    placed = {b.id: b for b in boxes}
    wires = tuple(_wires(p, placed, {f.id: f for f in frames}))
    return Scene(p.rg.name, p.scope, p.crumbs, boxes, frames, wires, (w, h), p)


# ---- wires


def _bottom(b: Box, key: str) -> Point:
    """Where an output leaves a node: along its bottom edge, in the order of its outputs."""
    outs = b.outputs
    x, y = b.at
    w, h = b.size
    return x + w * (outs.index(key) + 1) / (len(outs) + 1), y + h


def _top(b: Box) -> Point:
    return b.at[0] + b.size[0] / 2, b.at[1]


def _down(a: Point, b: Point, mid: float) -> tuple[Point, ...]:
    """Down from `a`, across at height `mid`, down to `b`; straight where they line up."""
    if abs(a[0] - b[0]) < 0.5:
        return (a, b)
    return (a, (a[0], mid), (b[0], mid), b)


def _wires(p: Plan, boxes: Mapping[str, Box], frames: Mapping[str, Frame]) -> Iterator[Wire]:
    rg = p.rg
    for e in rg.edges:
        if e.kind is EdgeKind.LINK:
            continue
        src, dst = _src(rg, e, p.compact), e.dst
        if src is None or dst is None or src not in boxes or dst not in boxes:
            continue
        a, b = boxes[src], boxes[dst]
        key = f"p{e.port}" if e.port is not None else OUT
        if key not in a.outputs:
            continue
        yield Wire(e.kind, src, key, dst, IN, _route(rg, e, a, key, b, frames))


def _src(rg: RoutineGraph, e: Edge, compact: frozenset[str]) -> str | None:
    """The node a wire starts at: a compact block stands for its join, and keeps none of what
    runs inside it."""
    n = rg.nodes[e.src]
    if isinstance(n, Join) and n.owner in compact:
        return n.owner
    if e.kind is EdgeKind.PORT and e.src in compact:
        return None
    return e.src


def _route(
    rg: RoutineGraph, e: Edge, a: Box, key: str, b: Box, frames: Mapping[str, Frame]
) -> tuple[Point, ...]:
    p0, p1 = _bottom(a, key), _top(b)
    match e.kind:
        case EdgeKind.PORT if b.kind == "join":
            return _down(p0, p1, p1[1] - NODE_GAP / 2)
        case EdgeKind.PORT:
            return _down(p0, p1, p0[1] + ARM_DROP / 2)
        case EdgeKind.JOIN:
            return _down(p0, p1, p1[1] - NODE_GAP / 2)
        case EdgeKind.LOOP:
            return _loop(rg, e, a, b, p0, p1, frames)
        case _:
            return _down(p0, p1, (p0[1] + p1[1]) / 2)


def _loop(
    rg: RoutineGraph,
    e: Edge,
    a: Box,
    b: Box,
    p0: Point,
    p1: Point,
    frames: Mapping[str, Frame],
) -> tuple[Point, ...]:
    """Back up to a repeat: out of the end of its body, round the right of the body's frame, in
    over the repeat; a body with nothing in it loops on the node."""
    above = p1[1] - EDGE
    f = frames.get(head_key(b.id))
    if f is None:
        right = b.at[0] + b.size[0] + EDGE
        return (
            p0,
            (p0[0], p0[1] + EDGE),
            (right, p0[1] + EDGE),
            (right, above),
            (p1[0], above),
            p1,
        )
    side = f.at[0] + f.size[0] + COLUMN_GAP / 2
    low = p0[1] + EDGE
    return (p0, (p0[0], low), (side, low), (side, above), (p1[0], above), p1)


# ---- selection


def scope_of(rg: RoutineGraph, nid: str) -> str | None:
    """The scope that holds a node, for show node: a statement's, a join's, the entry's; an arm
    or a head is a scope of its own. None for an id the routine has not."""
    if nid in rg.scopes:
        return nid
    n = rg.nodes.get(nid)
    return None if n is None else ROOT if isinstance(n, Entry) else n.scope


def scope_for(
    rg: RoutineGraph, nid: str, current: str = ROOT, collapse: int = COLLAPSE
) -> str | None:
    """The scope to show to show a node: `current` where it draws the node already (in line in a
    block that fits), else the node's own scope."""
    if current in rg.scopes and nid in _shown(rg, current, collapse)[0]:
        return current
    return scope_of(rg, nid)


def sibling(rg: RoutineGraph, nid: str, step: int = 1) -> str | None:
    """The statement `step` places down the column of a statement, a negative step up it; None
    past either end. The entry's next is the routine's first statement."""
    if nid == ENTRY:
        body = rg.scopes[ROOT].body
        return body[0] if step > 0 and body else None
    s = _stmt(rg, nid)
    body = rg.scopes[s.scope].body
    at = body.index(nid) + step
    return body[at] if 0 <= at < len(body) else None


# ---- the outline


@dataclass(frozen=True)
class OutlineRow:
    """A routine in the outline."""

    routine: str
    label: str
    """The user's name for it, empty for none."""
    nodes: int
    edited: bool
    new: bool
    findings: int
    level: str | None
    """The worst of its findings."""
    entry: str
    """What the game starts it as, `Top` or `On notice`; empty for none."""
    confidence: cat.Confidence | None
    """How sure the notes are of that."""
    shared: bool
    """Several headers run it: an edit here changes all their map types."""
    variants: tuple[int, ...]
    where: tuple[str, ...]
    """The list slots that name it: `base 0`."""
    callers: tuple[Caller, ...]
    """The routines that lead to it under this header, by sites; each is a link."""

    @property
    def reached_from(self) -> int:
        return len(self.callers)

    @property
    def text(self) -> str:
        return _called(self.routine, self.label)


@dataclass(frozen=True)
class OutlineList:
    """A header list as the outline shows it: the lists that name one table are one."""

    table: str
    fields: tuple[int, ...]
    names: tuple[str, ...]
    label: str
    """Short: `base`, `deep0-2`."""
    title: str
    confidence: cat.Confidence
    meaning: str
    slots: tuple[ListSlot, ...]


@dataclass(frozen=True)
class OutlineGroup:
    name: str
    """One of `catalogue.GROUPS`, or `UNLISTED`."""
    lists: tuple[OutlineList, ...]
    rows: tuple[OutlineRow, ...]
    """Each routine once, in slot order."""


@dataclass(frozen=True)
class Outline:
    header: str
    variants: tuple[int, ...]
    groups: tuple[OutlineGroup, ...]

    def rows(self, routine: str) -> list[OutlineRow]:
        """Where the outline lists a routine: once per group that holds it."""
        return [r for g in self.groups for r in g.rows if r.routine == routine]


def _merged(views: Sequence[ListView]) -> list[OutlineList]:
    """The lists of a group, the ones that name one table as one."""
    by: dict[str, list[ListView]] = {}
    for v in views:
        by.setdefault(v.table, []).append(v)
    out = []
    for table, vs in by.items():
        infos = [v.info for v in vs]
        names = tuple(i.name for i in infos)
        deep = all(n.startswith(DEEP) and n[len(DEEP) :].isdigit() for n in names)
        if len(vs) == 1:
            label, title = names[0], infos[0].title
        elif deep:
            span = source.ranges(int(n[len(DEEP) :]) for n in names)
            label, title = f"{DEEP}{span}", f"Deep calls {span}"
        else:
            label, title = "/".join(names), " / ".join(i.title for i in infos)
        weakest = min(i.confidence for i in infos)
        out.append(
            OutlineList(
                table,
                tuple(v.field for v in vs),
                names,
                label,
                title,
                weakest,
                infos[0].meaning,
                vs[0].slots,
            )
        )
    return out


def _where(lists: Sequence[OutlineList], routine: str) -> tuple[str, ...]:
    return tuple(f"{lst.label} {s.index}" for lst in lists for s in lst.slots if s.name == routine)


def outline(
    ov: Overview,
    header: str,
    names: Mapping[str, str] | None = None,
    findings: Sequence[Finding] = (),
) -> Outline:
    """The routines one header runs, in the groups the catalogue gives its lists, and the ones
    only links reach last. `names` are the user's labels for routines; `findings` count on the
    routine they are in. KeyError for a header the overview has not."""
    labels = names or {}
    if not any(h.name == header for h in ov.headers):
        raise KeyError(header)
    hv = ov.header(header)
    found: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        if f.item is not None:
            found[f.item].append(f)
    sites: dict[str, Counter[str]] = defaultdict(Counter)
    for c in hv.calls:
        if c.src != c.dst:
            sites[c.dst][c.src] += c.count
    points: dict[str, list[cat.EntryPoint]] = defaultdict(list)
    for e in hv.entry_points:
        if e.routine is not None:
            points[e.routine].append(e.point)

    def row(name: str, lists: Sequence[OutlineList]) -> OutlineRow:
        info = ov.routines[name]
        got = found.get(name, [])
        at = points.get(name, [])
        return OutlineRow(
            name,
            labels.get(name, ""),
            info.nodes,
            info.edited,
            info.new,
            len(got),
            _worst(got),
            ", ".join(pt.title for pt in at),
            min((pt.confidence for pt in at), default=None),
            info.shared,
            info.variants,
            _where(lists, name),
            tuple(Caller(src, n) for src, n in sites[name].items()),
        )

    groups = []
    for g in hv.groups:
        lists = _merged(g.lists)
        groups.append(OutlineGroup(g.name, tuple(lists), tuple(row(n, lists) for n in g.routines)))
    if hv.unlisted:
        groups.append(OutlineGroup(UNLISTED, (), tuple(row(n, ()) for n in hv.unlisted)))
    return Outline(header, hv.variants, tuple(groups))
