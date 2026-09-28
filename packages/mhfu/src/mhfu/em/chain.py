"""What a big monster does after an action: the enter-action call a pair's handler makes when it
ends, resolved to the pair it enters, with the guards on the path.

    from mhfu.em.chain import Chain
    from mhfu.em.moveset import Moveset
    ch = Chain(Moveset(game.em(75)))    # the species byte is the overlay's own unless given
    ch.pairs[1, 4].next                 # Edge(site, kind, main, id, mode, via, to, guards, alts)
    ch.enter.resolve(1, 4)              # the pairs enter(1, 4) lands in
    ch.enter.table()                    # {main: {id: pairs}} over each translator's switch
    ch.brain                            # {function: edges} for enter calls outside the handlers
    ch.predecessors()                   # {pair: pairs that hand off to it}

A handler ends by calling the species' enter-action, vtable slot ENTER_SLOT, directly, through
ENTER_ACTION or through a small helper, often as a tail call. Enter-action switches on the main
into a per-main translator that calls ACT_SET with the pair. Both hops are walked with the
species byte, the pair's own MAIN_STATE and SUB_STATE and its case arguments fixed, so the
successor is read from the code, not from a table.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cached_property

from rabbitizer import InstrId

from .. import addresses as a
from ..mips import Call, Gpr, Switch
from .moveset import Facts, Moveset, Site, Walker, known, pair_facts, seed_of

ENTER_SLOT = 0x88
"""Vtable offset of the species' enter-action(entity, main, id, mode)."""
ACT_SETS = frozenset([a.ACT_SET, a.SET_AI_STATE, a.SET_AI_STATE_ALERT])
"""Calls that set a pair directly."""
PREDICATES = {
    a.CLIP_CURSOR_CROSSES: "cursor crosses {frame}",
    a.CLIP_CURSOR_REACHED: "cursor reached {frame}",
    a.BUDGET_STEP: "budget spent",
}
"""Guard names for the calls handlers branch on; `frame` is the $f12 literal."""
LOCAL_PREDICATES = {75: {a.EM75_COLLIDED: "collided"}}
"""Per overlay species: predicates that live in that overlay."""
Pair = tuple[int, int]


@dataclass(frozen=True)
class Edge:
    """One call that sets the next pair, with every guard set it is reached under."""

    site: int
    kind: str
    """"enter" (through enter-action) or "act_set" (the pair directly)."""
    main: int | None
    id: int | None
    mode: int | None
    via: tuple[int, ...]
    """Helpers the call sits in, outermost first."""
    to: tuple[Pair, ...]
    """The pairs it lands in; empty when it is computed or its id enters nothing."""
    guards: tuple[str, ...]
    """The plainest guard set that reaches the call."""
    alts: tuple[tuple[str, ...], ...]
    """The other guard sets that reach it."""
    computed: bool = False
    """Where it lands is not a literal: main or id, or a pair the translator sets."""


@dataclass(frozen=True)
class Link:
    """A pair's handler and where it hands off."""

    handler: int
    args: Mapping[str, int]
    next: tuple[Edge, ...]


class Enter:
    """The species' enter-action and the translators it switches into."""

    def __init__(self, ms: Moveset, cells: Mapping[int, int]) -> None:
        self.ms, self.code = ms, ms.code
        self.cells = dict(cells)
        """Byte cells every walk takes as given: the species byte."""

    @cached_property
    def translators(self) -> frozenset[int]:
        """Functions that call ACT_SET or SET_AI_STATE themselves."""
        code = self.code
        return frozenset(
            code.function(c.site).start
            for c in code.calls
            if c.target in ACT_SETS and c.site in code.text
        )

    @cached_property
    def function(self) -> int | None:
        """The function that calls the most translators, at least two; with one translator
        and no such caller, that translator switches on the main itself."""
        callers: dict[int, set[int]] = {}
        for c in self.code.calls:
            if c.target in self.translators:
                fn = self.code.function(c.site).start
                if fn != c.target:
                    callers.setdefault(fn, set()).add(c.target)
        found = sorted((-len(ts), fn) for fn, ts in callers.items() if len(ts) >= 2)
        if found:
            return found[0][1]
        return next(iter(self.translators)) if len(self.translators) == 1 else None

    @property
    def functions(self) -> frozenset[int]:
        return frozenset() if self.function is None else frozenset([self.function])

    @cached_property
    def switch(self) -> Switch | None:
        """The switch on main in enter-action."""
        return None if self.function is None else self.ms.switch_in(self.function)

    @cached_property
    def _walker(self) -> Walker:
        watch = dict.fromkeys(ACT_SETS, "act_set")
        return Walker(self.code, watch, depth=2, facts=known(self.cells))

    def landings(
        self, main: int, id: int, mode: int | None = None, facts: Facts | None = None
    ) -> list[Site]:
        """The ACT_SET calls enter(main, id, mode) reaches, with the guards on each path;
        `facts` are what the caller's path knows of entity cells."""
        if self.function is None:
            return []
        seed = {Gpr.a1: main, Gpr.a2: id} | ({} if mode is None else {Gpr.a3: mode})
        return self._walker.run(self.function, seed, facts)

    def resolve(
        self, main: int, id: int, mode: int | None = None, facts: Facts | None = None
    ) -> tuple[Pair, ...] | None:
        """The pairs enter(main, id, mode) can set, none for an id that enters nothing;
        None when one is not a literal."""
        pairs = set()
        for s in self.landings(main, id, mode, facts):
            m, sub = s.args[0], s.args[1]
            if m is None or sub is None:
                return None
            pairs.add((m, sub))
        return tuple(sorted(pairs))

    def table(self) -> dict[int, dict[int, tuple[Pair, ...] | None]]:
        """{main: {id: pairs}} for the mains whose translator switches on the id."""
        out: dict[int, dict[int, tuple[Pair, ...] | None]] = {}
        if self.switch is None:
            return out
        for i, label in enumerate(self.switch.targets):
            call = self.ms.case_call(label)
            if call is None or call.target not in self.translators:
                continue
            sw = self.ms.switch_in(call.target)
            if sw is None:
                continue
            main = i + self.switch.first
            ids = range(sw.first, sw.first + len(sw.targets))
            out[main] = {n: self.resolve(main, n) for n in ids}
        return out


class Chain:
    """The successor graph of one em overlay."""

    def __init__(self, ms: Moveset, species: int | None = None) -> None:
        self.ms, self.code = ms, ms.code
        self.species = ms.ovl.species if species is None else species
        self.cells = {} if self.species is None else {a.ENTITY.SPECIES: self.species}
        self.enter = Enter(ms, self.cells)

    @cached_property
    def wrappers(self) -> frozenset[int]:
        """Functions that make an enter or act_set call, or tail-call a function that does."""
        code = self.code
        found = {
            code.function(c.site).start
            for c in code.calls
            if c.target in ACT_SETS or c.target == a.ENTER_ACTION or self._enter_slot(c)
        }
        tails = [
            (code.function(c.site).start, code.function(c.target).start)
            for c in code.calls
            if not c.link and c.target is not None and c.target in code.text
        ]
        grew = True
        while grew:
            before = len(found)
            found |= {fn for fn, target in tails if target in found}
            grew = len(found) > before
        return frozenset(found)

    def _enter_slot(self, call: Call) -> bool:
        """A call through ENTER_SLOT of some vtable; `Call.slot` misses one whose vtable load
        sits in branch-likely delay slots."""
        if call.slot is not None or call.target is not None:
            return call.slot == ENTER_SLOT
        load = self.code.source(call.site, self.code.at(call.site).rs)
        return (
            load is not None
            and load.uniqueId == InstrId.cpu_lw
            and (load.getProcessedImmediate() == ENTER_SLOT)
        )

    @cached_property
    def helpers(self) -> frozenset[int]:
        """The wrappers a handler's walk descends into: not the translators or enter-action."""
        return self.wrappers - self.enter.translators - self.enter.functions

    @cached_property
    def walker(self) -> Walker:
        names = dict(PREDICATES) | LOCAL_PREDICATES.get(self.ms.ovl.species or -1, {})
        watch = dict.fromkeys(ACT_SETS, "act_set") | {a.ENTER_ACTION: "enter"}
        return Walker(
            self.code,
            watch,
            methods={ENTER_SLOT: "enter"},
            follow=self.helpers,
            depth=2,
            facts=known(self.cells),
            names=names,
        )

    def hands_off(self, fn: int) -> bool:
        """Whether `fn` makes an enter call itself or calls a helper that does."""
        code = self.code
        where = code.function(fn)
        return where.start in self.wrappers or any(
            c.target is not None
            and c.target in code.text
            and code.function(c.target).start in self.helpers
            for c in code.calls
            if c.site in where
        )

    @cached_property
    def pairs(self) -> dict[Pair, Link]:
        """Every pair with a handler; `next` is empty when the handler never hands off."""
        out = {}
        for key, pair in sorted(self.ms.pairs.items()):
            if pair.handler is None:
                continue
            sites: list[Site] = []
            if self.hands_off(pair.handler):
                sites = self.walker.run(pair.handler, seed_of(pair.args), pair_facts(pair))
            out[key] = Link(pair.handler, pair.args, self.edges(sites))
        return out

    @cached_property
    def brain(self) -> dict[int, tuple[Edge, ...]]:
        """Enter calls in functions that are neither handlers, translators nor enter-action."""
        handlers = {p.handler for p in self.ms.pairs.values()}
        skip = handlers | self.enter.translators | self.enter.functions
        found = {fn: self.edges(self.walker.run(fn)) for fn in sorted(self.wrappers - skip)}
        return {fn: edges for fn, edges in found.items() if edges}

    def predecessors(self) -> dict[Pair, list[Pair]]:
        """{pair: the pairs whose handlers can land in it}."""
        out: dict[Pair, list[Pair]] = {}
        for key, link in self.pairs.items():
            for e in link.next:
                for t in e.to:
                    if key not in out.setdefault(t, []):
                        out[t].append(key)
        return {k: sorted(v) for k, v in out.items()}

    def edges(self, sites: Iterable[Site]) -> tuple[Edge, ...]:
        """One edge per (site, landing pairs); the shortest guard set leads, the rest are alts."""
        found: dict[tuple[int, tuple[Pair, ...] | None], tuple[Site, list[tuple[str, ...]]]] = {}
        for s in sites:
            main, id, mode = s.args
            to: tuple[Pair, ...] | None = None
            if main is not None and id is not None:
                if s.kind == "enter":
                    to = self.enter.resolve(main, id, mode, dict(s.facts))
                else:
                    to = ((main, id),)
            guards = simplify(s.guards)
            _, sets = found.setdefault((s.site, to), (s, []))
            if guards not in sets:
                sets.append(guards)
        out = []
        for (site, to), (s, sets) in found.items():
            sets.sort(key=len)
            main, id, mode = s.args
            alts = tuple(sets[1:])
            edge = Edge(site, s.kind, main, id, mode, s.via, to or (), sets[0], alts, to is None)
            out.append(edge)
        return tuple(out)


def simplify(guards: Iterable[str]) -> tuple[str, ...]:
    """A path's guards, readable: a subject pinned with `==` drops its `!=` tests, and repeats
    go. A bare cell test stays."""
    guards = list(guards)
    pinned = {g.split("==")[0] for g in guards if "==" in g}
    out: list[str] = []
    for g in guards:
        if "!=" in g and g.split("!=")[0] in pinned:
            continue
        if g not in out:
            out.append(g)
    return tuple(out)
