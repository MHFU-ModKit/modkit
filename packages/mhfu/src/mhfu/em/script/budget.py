# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The most instructions a script runs between two yields, counted as the game counts them.

    b = budget.Budget(prog)
    b.worst                 # the longest Run: count, entry, path (node ids), how it ends
    b.runs["base_0"]        # per entry: base and interrupt routines, resume points
    b.new["base_0"]         # the longest run through new code, where there is one

How AI_SCRIPT counts (read from its code, then run on synthetic scripts and random entity state):
its loop counter, at AI_SCRIPT + 0x60 and in the loop tail (+ 0x1AE4), is bumped before every
dispatch, and the loop leaves at 1000 without dispatching and without storing a resume address.
A run holds 999 dispatches at most; the next wake starts from the old resume address again. A
dispatch is one instruction or one marker the pc lands on:

- a block opener is 1. The marker a failed condition or a chosen arm steps over is not counted:
  a false if with no else is just its opener, a chosen random branch or case starts in its body;
- a body that falls through lands on the next marker, 1 more, and an arm marker (else, case,
  default, branch) jumps past the block's end marker: a block costs opener + body + 1;
- a repeat is its opener, then each pass its body and its end marker;
- `end` is 1; the byte past a routine's end is 1 (no opcode: the run ends, no resume address);
- only `enter` yields (`wait` does not). `end base`, `goto_base` and `goto 0` restart base[0] in
  the same run, `end return` and `end return2` go to the call's return address, and an
  interrupt's own `end` restarts base[0] too.

Calls keep three return slots (call, call_deep, call_ret) and a depth: `call` saves its return at
depth 0 and else only sets depth 1, `call_deep` saves at depth 1 and sets depth 2, `end return`
sets depth 0 and `end return2` depth 1. These and a repeat's passes left persist across runs, so
a resume point is explored with the stack it was reached with. The route ops (`walk`,
`route_run`, `call_spot`) are taken as calling every candidate routine once, or none when no
route is armed.

Worst cases follow every branch: a path is possible when the conditions allow it, so the count is
an upper bound. Base[0] may restart once in a run (a second restart ends the path); an
interrupt's end is a restart of its own, so its run adds the longest run of base[0].
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import NamedTuple

from . import ir
from .opcodes import END_KINDS, LISTS, SLOTS, Flow, Shape, slot

__all__ = ["LIMIT", "WARN", "Budget", "Draw", "Restart", "Run", "contexts", "needs"]

LIMIT = 999
"""Dispatches in one run: the 1000th is not made."""
WARN = 900
_NO = -1
_RETURN_SLOT = {"return": 0, "return2": 1, "return_ret": 2}
_ROUTE_RETURNS = frozenset({"route", "route2", "loop_next", "walk_step", "walk_loop"})
_INTERRUPT_ENDS = frozenset({"forget", "notice"} | {n for n in END_KINDS.values() if "int_" in n})
_CALL_SLOT = {"call": 0, "call_deep": 1, "call_ret": 2, "walk": 3, "route_run": 3, "call_spot": 3}
_CALL_LABEL = {"call": "call", "call_ret": "ret", "call_deep": "deep", "walk": "walk"}
_ROUTE_LISTS = {"route_run": "loop", "call_spot": "node"}
_SITE = {"enter": "enter", "goto": "goto", "goto_base": "restart", "end": "end"}
_CHANNELS = range(4)


@dataclass(frozen=True)
class Run:
    """The longest run from an entry."""

    entry: str
    """The routine it starts in, or `after <id>` for the resume point behind an enter."""
    header: str
    count: int
    """Dispatches, the yielding enter and the markers included."""
    own: int
    """The same without the run of base[0] that an interrupt's `end` goes on into."""
    path: tuple[str, ...]
    """The ids of the nodes it dispatches in order, a repeated node once per dispatch."""
    ends: str
    """enter, end, reset (falls off a routine, or calls an empty slot), loop (a second restart),
    int (an interrupt's end) or unbounded (a path that can go round without yielding)."""
    new: bool
    """Whether the path touches new or edited code."""
    unbounded: bool = False


@dataclass(frozen=True)
class Restart:
    """A path from base[0] that restarts base[0] with no yield, at a new or edited node."""

    node: str
    header: str
    path: tuple[str, ...]
    forced: bool
    """No condition or random on the path: it restarts every time."""


@dataclass(frozen=True)
class Draw:
    """A new or edited random that can run after another draw in the same run."""

    node: str
    header: str


@dataclass(slots=True)
class _Site:
    kind: str
    """op, open, marker, rep_open, rep_end, enter, goto, restart, call, end or fall."""
    id: str
    new: bool
    dirty: bool = False
    """An opener whose block or one of its arms is new: its distribution is new or edited."""
    next: int = _NO
    to: tuple[int, ...] = ()
    node: ir.Insn | None = None
    arg: int = 0
    """A repeat's count; 1 on an opener that draws."""


class _St(NamedTuple):
    site: int
    rets: tuple[int, int, int, int]
    depth: int
    rem: int
    """The passes a repeat has left."""
    again: bool
    """A restart has been taken in this run."""


class _Step(NamedTuple):
    leaf: str
    """Empty when the run goes on to `nxt`; else how it ends here."""
    nxt: tuple[_St, ...]
    resume: _St | None = None
    restart: bool = False


class _Val:
    """The longest paths from a state in four channels: 0 any path to a leaf, 1 any path to an
    interrupt's end, 2 and 3 the same through new code."""

    __slots__ = ("p", "unb", "v")

    def __init__(self) -> None:
        self.v: list[int | None] = [None] * 4
        self.p: list[tuple[_St, int] | None] = [None] * 4
        self.unb: _St | None = None
        """A state on a cycle with no yield, or the next one on the way to it."""


class _Cont(NamedTuple):
    ops: int
    path: tuple[str, ...]


class _Graph:
    """A program's routines as sites: a site is one dispatch, and its `next` or `to` the sites
    it can go to. Where a block falls through to a marker, the marker is a site."""

    def __init__(self, p: ir.Program) -> None:
        self.p = p
        self.sites: list[_Site] = []
        self.entry: dict[str, int] = {}
        for r in p.routines():
            fall = self._add(_Site("fall", "", r.va is None))
            self.entry[r.name] = self._list(r.body, fall)

    def _add(self, s: _Site) -> int:
        self.sites.append(s)
        return len(self.sites) - 1

    def _list(self, nodes: Sequence[ir.Node], after: int) -> int:
        cur = after
        for n in reversed(nodes):
            cur = self._insn(n, cur) if isinstance(n, ir.Insn) else self._block(n, cur)
        return cur

    def _insn(self, n: ir.Insn, nxt: int) -> int:
        name = n.op.name
        kind = _SITE.get(name, "call" if name in _CALL_SLOT else "op")
        return self._add(_Site(kind, n.id, n.pc is None, next=nxt, node=n))

    def _block(self, b: ir.Block, after: int) -> int:
        new, shape = b.pc is None, b.op.shape
        dirty = new or any(a.pc is None for a in b.arms)
        if shape is Shape.REPEAT:
            count = b.args.get("count")
            end = self._add(_Site("rep_end", b.id, new, next=after))
            body = self._list(b.head, end)
            self.sites[end].to = (body,)
            n = count if isinstance(count, int) and count >= 1 else 1
            return self._add(_Site("rep_open", b.id, new, dirty, next=body, arg=n))
        mark = self._add(_Site("marker", b.id, new, next=after))
        to = [self._list(b.head, mark)] if shape is Shape.IF else []
        arms = [a for a in b.arms if a.args.get("weight") != 0] if shape is Shape.RANDOM else b.arms
        to += [self._list(a.body, mark) for a in arms]
        skips = shape is Shape.SWITCH or (shape is Shape.IF and not b.arms)
        if skips and not any(a.sub == 2 for a in b.arms):
            to.append(after)
        return self._add(
            _Site("open", b.id, new, dirty, to=tuple(to), arg=int(shape is Shape.RANDOM))
        )


class _Walk:
    """The states one header's lists lead to: their steps, and the longest run from each."""

    def __init__(self, g: _Graph, h: ir.Header) -> None:
        self.g, self.h = g, h
        self.tables = g.p.tables()
        base = self.names(0)
        self.base0 = g.entry.get(base[0] or "") if base else None
        self.memo: dict[_St, _Val] = {}
        self.found: list[tuple[_St, str]] = []
        """The resume points met, with the id of the enter that makes each."""
        self._seen: set[_St] = set()

    def names(self, field: int) -> list[str | None]:
        t = self.h.fields[field] if field < len(self.h.fields) else None
        if not isinstance(t, str) or t not in self.tables:
            return []
        return [e if isinstance(e, str) else None for e in self.tables[t].slots]

    def root(self, name: str) -> _St:
        return _St(self.g.entry[name], (_NO,) * 4, 0, 0, False)

    def entries(self) -> Iterator[tuple[str, _St]]:
        """The routines a run starts in: the base list and the first of each interrupt list."""
        for f in (0, *(i for i, s in enumerate(SLOTS) if s.interrupt)):
            for k, name in enumerate(self.names(f)):
                if name in self.g.entry and (f == 0 or k == 0):
                    yield name, self.root(name)

    def _targets(self, n: ir.Insn) -> list[int]:
        if n.op.name in _ROUTE_LISTS:
            names = self.names(LISTS[_ROUTE_LISTS[n.op.name]])
        else:
            want = "walk" if n.op.name == "walk" else "call"
            names = [self.g.p.entry(self.h, r, self.tables) for r in ir.refs(n) if r.kind == want]
        return [self.g.entry[x] for x in dict.fromkeys(names) if x in self.g.entry]

    def step(self, st: _St) -> _Step:
        s = self.g.sites[st.site]
        match s.kind:
            case "op" | "marker":
                return _Step("", (st._replace(site=s.next),))
            case "open":
                return _Step("", tuple(st._replace(site=t) for t in s.to))
            case "rep_open":
                return _Step("", (st._replace(site=s.next, rem=s.arg - 1),))
            case "rep_end":
                if st.rem > 0:
                    return _Step("", (st._replace(site=s.to[0], rem=st.rem - 1),))
                return _Step("", (st._replace(site=s.next),))
            case "enter":
                return _Step("enter", (), _St(s.next, st.rets, st.depth, st.rem, False))
            case "fall":
                return _Step("reset", ())
            case "restart":
                return self._restart(st)
        assert s.node is not None
        match s.kind:
            case "goto":
                return self._goto(st, s.node)
            case "call":
                return self._call(st, s.node, s.next)
        return self._end(st, s.node)

    def _restart(self, st: _St) -> _Step:
        if self.base0 is None:
            return _Step("reset", ())
        if st.again:
            return _Step("loop", (), restart=True)
        return _Step("", (st._replace(site=self.base0, again=True),), restart=True)

    def _goto(self, st: _St, n: ir.Insn) -> _Step:
        refs = ir.refs(n)
        if not refs:
            return _Step("reset", ())
        if refs[0].index == 0:
            return self._restart(st)
        name = self.g.p.entry(self.h, refs[0], self.tables)
        if name not in self.g.entry:
            return _Step("reset", ())
        return _Step("", (st._replace(site=self.g.entry[name]),))

    def _call(self, st: _St, n: ir.Insn, back: int) -> _Step:
        at = _CALL_SLOT[n.op.name]
        rets, depth = list(st.rets), st.depth
        if at == 0:
            if depth == 0:
                rets[0] = back
            depth = 1
        elif at == 1:
            if depth == 1:
                rets[1] = back
            depth = 2
        else:
            rets[at] = back
        into = st._replace(rets=(rets[0], rets[1], rets[2], rets[3]), depth=depth)
        out = tuple(into._replace(site=t) for t in self._targets(n))
        if at == 3:  # a route that is not armed is passed over
            out += (st._replace(site=back),)
        return _Step("", out) if out else _Step("reset", ())

    def _end(self, st: _St, n: ir.Insn) -> _Step:
        k = n.args.get("kind")
        kind = END_KINDS.get(k) if isinstance(k, int) else None
        if kind == "base":
            return self._restart(st)
        if kind in _INTERRUPT_ENDS:
            return _Step("int", ())
        at = _RETURN_SLOT.get(kind or "", 3 if kind in _ROUTE_RETURNS else _NO)
        if at == _NO or st.rets[at] == _NO:
            return _Step("end", ())
        depth = {0: 0, 1: 1}.get(at, st.depth)
        rets = tuple(_NO if i == at else r for i, r in enumerate(st.rets))
        return _Step("", (st._replace(site=st.rets[at], rets=rets, depth=depth),))  # type: ignore[arg-type]

    def solve(self, root: _St) -> _Val:
        """The longest paths from `root`, memoised; a walk with no recursion."""
        memo, sites = self.memo, self.g.sites
        if root in memo:
            return memo[root]
        onstack: set[_St] = set()
        frames: list[tuple[_St, _Step, list[int]]] = []

        def push(st: _St) -> None:
            stp = self.step(st)
            if stp.resume is not None and stp.resume not in self._seen:
                self._seen.add(stp.resume)
                self.found.append((stp.resume, sites[st.site].id))
            frames.append((st, stp, [0]))
            onstack.add(st)

        push(root)
        while frames:
            st, stp, at = frames[-1]
            if at[0] < len(stp.nxt):
                nx = stp.nxt[at[0]]
                at[0] += 1
                if nx not in memo and nx not in onstack:
                    push(nx)
                continue
            frames.pop()
            onstack.discard(st)
            memo[st] = self._combine(st, stp, sites[st.site])
        return memo[root]

    def _combine(self, st: _St, stp: _Step, s: _Site) -> _Val:
        out = _Val()
        if not stp.nxt:
            lead = 1 if stp.leaf == "int" else 0
            out.v[lead] = 1
            out.v[lead + 2] = 1 if s.new else None
            return out
        for nx in stp.nxt:
            nv = self.memo.get(nx)
            if nv is None:  # still being walked: a cycle
                out.unb = out.unb or nx
                continue
            if nv.unb is not None:
                out.unb = out.unb or nx
            for c in _CHANNELS:
                src = c & 1 if s.new else c
                got = nv.v[src]
                if got is not None and (out.v[c] is None or got + 1 > out.v[c]):  # type: ignore[operator]
                    out.v[c], out.p[c] = got + 1, (nx, src)
        return out

    def path(self, st: _St, ch: int) -> tuple[tuple[str, ...], str, bool]:
        """The ids along the longest path of a channel, how it ends and whether it is new."""
        sites, ids, new = self.g.sites, [], False
        while True:
            s = sites[st.site]
            if s.id:
                ids.append(s.id)
            new = new or s.new
            ptr = self.memo[st].p[ch]
            if ptr is None:
                return tuple(ids), self.step(st).leaf, new
            st, ch = ptr

    def cycle(self, st: _St) -> tuple[tuple[str, ...], bool]:
        """The ids from `st` to where a cycle with no yield closes, and whether it is new."""
        sites, ids, new, seen = self.g.sites, [], False, set()
        while st not in seen:
            seen.add(st)
            s = sites[st.site]
            if s.id:
                ids.append(s.id)
            new = new or s.new
            nxt = self.memo[st].unb
            if nxt is None:
                break
            st = nxt
        return tuple(ids), new


class Budget:
    """The longest runs of a resolved program, per entry."""

    def __init__(self, p: ir.Program) -> None:
        self.graph = _Graph(p)
        self.walks = [_Walk(self.graph, h) for h in p.headers()]
        self.runs: dict[str, Run] = {}
        """The longest run per entry: each base and interrupt routine, and `after <id>` for the
        resume point behind each enter."""
        self.new: dict[str, Run] = {}
        """The same, through new or edited code only; entries with no such path are left out."""
        self._roots: list[dict[_St, str]] = []
        self._conts: list[tuple[_Cont | None, _Cont | None]] = []
        for w in self.walks:
            self._header(w)

    @property
    def worst(self) -> Run | None:
        return max(self.runs.values(), key=lambda r: r.count, default=None)

    def _header(self, w: _Walk) -> None:
        roots: dict[_St, str] = {}
        self._solve(w, [(st, n) for n, st in w.entries()], roots)
        cont = self._cont(w)
        for st, label in roots.items():
            self._report(w, st, label, cont)
        self._roots.append(roots)
        self._conts.append(cont)

    @staticmethod
    def _solve(w: _Walk, starts: list[tuple[_St, str]], roots: dict[_St, str]) -> list[_St]:
        """Solve the starts and the resume points they lead to; the new roots, in order."""
        out: list[_St] = []
        todo = deque(starts)
        taken = len(w.found)
        while todo:
            st, label = todo.popleft()
            if st in roots:
                continue
            roots[st] = label
            out.append(st)
            w.solve(st)
            todo.extend((r, f"after {eid}") for r, eid in w.found[taken:])
            taken = len(w.found)
        return out

    @staticmethod
    def _cont(w: _Walk) -> tuple[_Cont | None, _Cont | None]:
        """The longest run from base[0], which an interrupt's end restarts in the same run: any,
        and through new code."""
        if w.base0 is None:
            return None, None
        st = w.root(w.names(0)[0] or "")
        val = w.solve(st)
        best: list[_Cont | None] = [None, None]
        for k, chans in enumerate(((0, 1), (2, 3))):
            got = [(v, ch) for ch in chans if (v := val.v[ch]) is not None]
            if got:
                v, ch = max(got)
                best[k] = _Cont(v, w.path(st, ch)[0])
        return best[0], best[1]

    def _report(
        self, w: _Walk, st: _St, label: str, cont: tuple[_Cont | None, _Cont | None]
    ) -> None:
        for out, new in ((self.runs, False), (self.new, True)):
            floor = out[label].count if label in out else -1
            if run := self._run(w, st, label, cont, new, floor):
                out[label] = run

    @cached_property
    def direct(self) -> dict[str, Run]:
        """The longest run per entry with no restart of base[0] in it."""
        out: dict[str, Run] = {}
        for w, roots, cont in zip(self.walks, self._roots, self._conts, strict=True):
            for st, label in roots.items():
                st = st._replace(again=True)
                w.solve(st)
                floor = out[label].count if label in out else -1
                if run := self._run(w, st, label, cont, False, floor):
                    out[label] = run
        return out

    @staticmethod
    def _run(
        w: _Walk,
        st: _St,
        label: str,
        cont: tuple[_Cont | None, _Cont | None],
        new: bool,
        floor: int,
    ) -> Run | None:
        """The longest run from `st`, or with `new` the longest through new code; None unless it
        is longer than `floor`."""
        val = w.memo[st]
        if val.unb is not None:
            if floor > LIMIT:
                return None
            ids, touched = w.cycle(val.unb)
            if new and not touched:
                return None
            return Run(label, w.h.name, LIMIT + 1, LIMIT + 1, ids, "unbounded", touched, True)
        any_, new_ = cont
        v = val.v
        cands: list[tuple[int, int | None, _Cont | None]] = (
            [(2, v[2], None), (3, v[3], any_), (1, v[1], new_)]
            if new
            else [(0, v[0], None), (1, v[1], any_)]
        )
        best: tuple[int, int, _Cont | None] | None = None
        for ch, got, tail in cands:
            if got is None or (new and ch == 1 and tail is None):
                continue
            n = got + (tail.ops if tail else 0)
            if best is None or n > best[0]:
                best = (n, ch, tail)
        if best is None or best[0] <= floor:
            return None
        n, ch, tail = best
        ids, ends, touched = w.path(st, ch)
        own = val.v[ch]
        assert own is not None
        if tail:
            ids += tail.path
        return Run(label, w.h.name, n, own, ids, "int" if ch in (1, 3) else ends, new or touched)

    @cached_property
    def restarts(self) -> list[Restart]:
        """Where base[0] can restart itself with no yield through a new or edited node."""
        found: dict[str, Restart] = {}
        sites = self.graph.sites
        if not any(s.new and s.kind in ("restart", "goto", "end") for s in sites):
            return []
        for w in self.walks:
            if w.base0 is None:
                continue
            key0 = (w.root(w.names(0)[0] or ""), False)
            parent: dict[tuple[_St, bool], tuple[_St, bool] | None] = {key0: None}
            todo = deque([key0])
            while todo:
                key = todo.popleft()
                st, decided = key
                s = sites[st.site]
                stp = w.step(st)
                if stp.restart:
                    if s.new and s.id not in found:
                        ids = []
                        at: tuple[_St, bool] | None = key
                        while at is not None:
                            ids.append(sites[at[0].site].id)
                            at = parent[at]
                        path = tuple(i for i in reversed(ids) if i)
                        found[s.id] = Restart(s.id, w.h.name, path, not decided)
                    continue
                decided = decided or s.kind == "open"
                for nx in stp.nxt:
                    if (nx, decided) not in parent:
                        parent[(nx, decided)] = key
                        todo.append((nx, decided))
        return list(found.values())

    @cached_property
    def draws(self) -> list[Draw]:
        """The new or edited randoms that can run after another draw in the same run, a restart of
        base[0] starting a new one."""
        found: dict[str, Draw] = {}
        sites = self.graph.sites
        if not any(s.dirty and s.arg for s in sites):
            return []
        for w, roots in zip(self.walks, self._roots, strict=True):
            seen: set[tuple[_St, bool]] = set()
            todo = deque((r, False) for r in roots)
            while todo:
                key = todo.popleft()
                if key in seen:
                    continue
                seen.add(key)
                st, drew = key
                s = sites[st.site]
                if s.kind == "open" and s.arg:
                    if drew and s.dirty and s.id not in found:
                        found[s.id] = Draw(s.id, w.h.name)
                    drew = True
                stp = w.step(st)
                if not stp.restart:  # a restart re-rolls: that pass is a run of its own
                    todo.extend((nx, drew) for nx in stp.nxt)
        return list(found.values())


_NEEDS = {
    "base": "base",
    "return": "call",
    "return2": "deep",
    "return_ret": "ret",
    "route": "node",
    "route2": "node",
    "loop_next": "loop",
    "walk_step": "walk",
    "walk_loop": "walk",
    "forget": "int_0x20",
    "notice": "int_0x20",
    **{n: n for n in END_KINDS.values() if n.startswith("int_")},
}
"""The context an end kind belongs to: the label of the list its routine runs from."""
_ENTRY_LABELS = frozenset({"base", *(s.name for s in SLOTS if s.interrupt)})


def needs(kind: int) -> str | None:
    """The context label the end kind `kind` needs, None for a kind the game ignores."""
    name = END_KINDS.get(kind)
    return _NEEDS.get(name) if name else None


def contexts(p: ir.Program) -> dict[str, frozenset[str]]:
    """What each routine can run as, by the labels `needs` names: the lists it is in (`base`,
    `call`, `ret`, `walk`, `loop`, `node`, an interrupt's name, `deep` for any call_deep list),
    and what reaches it: a call gives the callee its own label and the caller's `base` or
    interrupt, a goto gives everything. A routine nothing reaches has none."""
    tables = p.tables()
    lab: dict[str, set[str]] = {r.name: set() for r in p.routines()}
    edges: dict[str, list[tuple[str, str | None]]] = {r.name: [] for r in p.routines()}
    for h in p.headers():
        for f, t in enumerate(h.fields):
            sl = slot(f)
            if not isinstance(t, str) or t not in tables or sl.kind == "data":
                continue
            for e in tables[t].slots:
                if isinstance(e, str) and e in lab:
                    lab[e].add("deep" if sl.name.startswith("deep") else sl.name)
        for r in p.routines():
            for n in ir.walk(r.body):
                if isinstance(n, ir.Insn):
                    edges[r.name] += _calls(p, h, n, tables)
    todo = list(lab)
    while todo:
        name = todo.pop()
        for dst, how in edges[name]:
            want = lab[name] if how is None else (lab[name] & _ENTRY_LABELS) | {how}
            if not want <= lab[dst]:
                lab[dst] |= want
                todo.append(dst)
    return {k: frozenset(v) for k, v in lab.items()}


def _calls(
    p: ir.Program, h: ir.Header, n: ir.Insn, tables: dict[str, ir.Table]
) -> list[tuple[str, str | None]]:
    """The routines an instruction goes to under header `h`, each with the label a call gives
    it, None for a goto."""
    name = n.op.name
    out: list[tuple[str, str | None]] = []
    if name in _ROUTE_LISTS:
        f = LISTS[_ROUTE_LISTS[name]]
        t = h.fields[f] if f < len(h.fields) else None
        for e in tables[t].slots if isinstance(t, str) and t in tables else []:
            if isinstance(e, str):
                out.append((e, _ROUTE_LISTS[name]))
    elif n.op.flow in (Flow.GOTO, Flow.CALL):
        for r in ir.refs(n):
            found = p.entry(h, r, tables)
            if found and r.kind in ("goto", "call", "walk"):
                out.append((found, None if n.op.flow is Flow.GOTO else _CALL_LABEL[name]))
    return out
