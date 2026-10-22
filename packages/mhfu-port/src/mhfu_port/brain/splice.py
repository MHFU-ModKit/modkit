# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The donor's AI script spliced into a host row's: the `entry` routine and what it calls,
translated into MHFU nodes and set into the host's `splice` routine, as an edit set.

    donor = p3.lift(p3rd, 23)
    host = build.lift_row(fu, 75, 75)
    survey = vars.survey(host, fu.em(75))                       # which script variables are free
    spliced = splice.splice(donor, host, manifest.brain, survey, moves.slots(manifest))
    built = build.Built.of(host, spliced.edits)                    # vetted, laid out, packed

`Translator` needs no host: MHP3rd nodes in, MHFU nodes and findings out. `OPS` is the one table
that says what each MHP3rd opcode becomes. The code lands as follows:

- pasted: the entry's body, and a base routine it ends on with `call_base`, in place of the
  host routine's body (its leading plain instructions and its `end` stay);
- a new routine `p3_<name>` for each `call n` from pasted code, ending `end 1`;
- inlined: each `call n` from those, so no call goes deeper than one level.

A donor pair that `[brain.pairs]` maps to an own move becomes `enter OWN_MOVE_MAIN k 0`, `k` the
move's slot in `slots` (`moves.slots`, the one numbering the moves module and `mhfu_port.lua`
use); em_vhook plays that move in place of a host pair. A list there enters each item in turn.

With `[brain] fatigue` the donor's `if_tired`, and `if_stamina_low` at 0 (stamina 0 only precedes
tired), become the host's `if_gauge_a_low`, which the framework's fatigue helper drives
(`brain.fatigue`); no `[brain.when]` answer is asked for them. The host must leave gauge A alone.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum

from mhfu import addresses
from mhfu.em.script import budget, edit, editset, ir
from mhfu.em.script import vars as script_vars
from mhfu.em.script.check import Finding
from mhfu.em.script.editset import Anchor, EditSet, Insert
from mhfu.em.script.opcodes import Shape
from mhfu.overlay import Overlay

from .. import manifest
from . import p3, p3ops

Pair = manifest.Pair
TOTAL = 32
"""What an MHFU random's weights add up to; MHP3rd's add up to 100."""
ARMS = 13
"""Branches of an MHFU random, the padding one included."""
GROUP = ARMS - 1
"""Live arms of an inner random of a split one: the padding branch makes it ARMS."""
BUCKETS = 5
"""Buckets of an MHFU switch_range."""
MODES: Mapping[int, str] = {1: "if_mode2"}
"""MHP3rd's mode byte to the MHFU test that holds in that mode."""
WHEN = ("if_target_state", "if_tired", "if_stamina_low", "if_target_set")
"""The tests the manifest answers, in `[brain.when]`: MHFU has no state for them."""
SIDE = (0, 127)
"""The arc `if_target_in_arc` tests for `if_target_side`: the target's bearing minus the facing in
0 to 127/256 of a turn, MHP3rd's bit 15 clear (its upper bound is the byte below the half)."""
GLOBAL_540 = 2
"""The one value of the word `switch_global_540` reads that the donors' scripts test for."""
HP_SHIFT = 1
"""MHP3rd's `switch_hp` case matches an HP percent at or below it, MHFU's below it (both compare
the truncated integer percent), so each case moves up by one."""
QUERIES = (int(addresses.CHARGE_QUERY_CHARGED), int(addresses.CHARGE_QUERY_COOLDOWN))
"""The `switch_query` args the framework answers for a port with `[brain] charge`."""
GAUGE = "if_gauge_a_low"
"""The host test the fatigue helper answers."""
GAUGE_A_OPS = (GAUGE, "refill_gauge_a")
"""The host ops that use gauge A."""
FATIGUE_TESTS = ("if_tired", "if_stamina_low")
"""The `[brain.when]` tests `[brain] fatigue` takes over."""
HOST_OPS = (
    "set_dest",
    "transit",
    "call_list2",
    "switch_area",
    "switch_map",
    "switch_prev_area",
    "walk",
    "route_step",
    "if_route_armed",
    "if_intent",
    "switch_intent",
    "if_travelling",
    "switch_class",
    "if_same_species_present",
    "if_flag_3a8",
    "if_flag_bd5_0",
    "if_flag_bd5_1",
    "set_flag_eb8",
    "switch_dist_spot",
)
"""Map and area ops: the host's roaming owns what they do."""
TRANSFERS = ("call", "call_base")
"""The ops that run another routine, which `Translator.scan` follows."""

PASTE, CALLED, INLINE = range(3)
"""Where code lands: in the host routine, in a new routine, inlined into one."""


class Yields(Enum):
    NEVER = "never"
    ALWAYS = "always"
    MAYBE = "maybe"


@dataclass
class Body:
    """What a list of nodes has become so far."""

    out: list[ir.Node] = field(default_factory=list)
    latch: tuple[int, int] | None = None
    """Where in `out` the last set_latch fell, and its value."""


@dataclass(frozen=True)
class Where:
    level: int
    """PASTE, CALLED or INLINE."""
    item: str
    """The routine it lands in, for findings."""
    tail: bool
    """Nothing runs after the node in its routine."""
    call: int | None
    """The call index of the routine the node is in; None while pasting."""
    body: Body


def shares(weights: Sequence[int], whole: int = 100) -> tuple[list[int], list[int]]:
    """MHFU weights for MHP3rd's, which add up to `whole` or less: each `w * 32 / whole` by
    largest remainder, a non-zero one at least 1, the total at most 32; and the positions that
    were raised to 1."""
    exact = [w * TOTAL for w in weights]
    out = [e // whole for e in exact]
    spare = max(0, min(TOTAL, (sum(exact) + whole // 2) // whole) - sum(out))
    for i in sorted(range(len(out)), key=lambda j: (-(exact[j] % whole), j))[:spare]:
        out[i] += 1
    raised = [i for i, (w, s) in enumerate(zip(weights, out, strict=True)) if w and not s]
    for i in raised:
        out[i] = 1
    while sum(out) > TOTAL:
        out[out.index(max(out))] -= 1
    return out, raised


def _text(n: p3.Node) -> str:
    if isinstance(n, p3.Leave):
        return n.op.mnemonic(n.sub)
    return " ".join([n.op.name, *map(str, n.args.values())])


def _is_end(n: p3.Node) -> bool:
    return isinstance(n, p3.Insn) and n.op.code in p3ops.ENDS


def _branches(n: p3.Block) -> tuple[list[p3.Node], list[p3.Node]]:
    """An if's then and else bodies."""
    return n.head, next((a.body for a in n.arms if a.sub == p3ops.ELSE), [])


class Translator:
    """MHP3rd nodes as MHFU nodes: `paste` the entry, and `routines` holds the new routines."""

    def __init__(
        self,
        donor: p3.Brain,
        spec: manifest.Brain,
        survey: Mapping[int, script_vars.Var] | None = None,
        slots: Mapping[str, int] | None = None,
    ) -> None:
        self.donor, self.spec, self.survey = donor, spec, survey
        self.slots: Mapping[str, int] = slots or {}
        """The registry slot of each own move, by name."""
        self.findings: list[Finding] = []
        self.routines: dict[str, list[ir.Node]] = {}
        """The new routines, by name."""
        self.sources: dict[str, str] = {}
        """The donor routine each new routine comes from."""
        self.uses: Counter[tuple[Pair, Pair | str]] = Counter()
        """Each enter translated, by donor pair and what it plays: a host pair or an own move's
        name."""
        self.entered: set[Pair] = set()
        """The donor pairs the routines the entry reaches enter, whether or not they are kept."""
        self.asked: set[str] = set()
        """The `[brain.when]` tests the translation looked up."""
        self.var: int | None = None
        """The history variable, once the region is known to read the call before."""
        self._stack: list[str] = []
        self._placed: dict[int, ir.Located] = {}
        """The history reads whose routine has noted its call, by `id` (held, so no other node
        takes it): a routine that inlines one sees a call there, no read of its own."""

    def add(self, w: Where | str, level: str, code: str, text: str) -> None:
        item = w if isinstance(w, str) else w.item
        self.findings.append(Finding(level, code, text, None, item))

    # ---- entry

    def paste(self, name: str, item: str) -> list[ir.Node]:
        """The routine `name` as nodes for the host routine `item`."""
        self.scan(name, item)
        return self.pasted(name, item)

    def scan(self, name: str, item: str) -> None:
        """What the entry reaches: routines that do not decode, the pairs entered, and a read of
        the call before, which needs the history variable."""
        reads = False
        seen: set[str] = set()
        todo = [name]
        while todo:
            cur = todo.pop()
            r = self.donor.routines.get(cur)
            if cur in seen or r is None:
                continue
            seen.add(cur)
            for problem in r.problems:
                self.add(item, "error", "P3_UNSUPPORTED", f"{cur} does not decode: {problem}")
            for n in p3.walk(r.body):
                if not isinstance(n, p3.Insn | p3.Block):
                    continue
                reads = reads or n.op.name == "switch_prev_call"
                if n.op.name == "enter":
                    self.entered.add((n.args["main"], n.args["sub"]))
                at = n.op.entry(n.args) if n.op.name in TRANSFERS else None
                if at and (to := self.donor.entry(*at)):
                    todo.append(to)
        if reads:
            self.var = self.history_var(item)

    def history_var(self, item: str) -> int | None:
        """The variable that keeps the last call: the manifest's, which the survey may doubt, else
        the host's first free one."""
        want, survey = self.spec.history, self.survey
        if want is not None:
            v = None if survey is None else survey.get(want)
            if v is not None and v.kind is not script_vars.Kind.FREE:
                text = f"brain.history {want} is not free ({v.kind}): {'; '.join(v.reasons)}"
                self.add(item, "warning", "P3_HISTORY", text)
            return want
        if survey is None:
            self.add(
                item, "error", "P3_HISTORY", "switch_prev_call needs brain.history, a script var"
            )
            return None
        if not (free := script_vars.free(survey)):
            kinds = ", ".join(f"{v.number} {v.kind}" for v in survey.values())
            self.add(
                item, "error", "P3_HISTORY", f"no script variable is free for the history: {kinds}"
            )
            return None
        self.add(
            item,
            "info",
            "P3_HISTORY",
            f"the call history is script variable {free[0]}, the first free one",
        )
        return free[0]

    def pasted(self, name: str, item: str) -> list[ir.Node]:
        if name in self._stack:
            self.add(item, "error", "P3_UNSUPPORTED", f"{name} pastes itself")
            return []
        self._stack.append(name)
        try:
            return self.run(self.donor.routines[name].body, PASTE, item, True)
        finally:
            self._stack.pop()

    def routine(self, name: str, index: int, item: str) -> str | None:
        """The label of the new routine for donor routine `name`, made on first use."""
        label = f"p3_{name}"
        if label in self.routines:
            return label
        if name in self._stack:
            self.add(item, "error", "P3_UNSUPPORTED", f"{name} calls itself")
            return None
        self._stack.append(name)
        try:
            body = self.callee(name, index, CALLED, label)
        finally:
            self._stack.pop()
        self.routines[label] = [*body, edit.insn("end", 1)]
        self.sources[label] = name
        return label

    def callee(self, name: str, index: int, level: int, item: str) -> list[ir.Node]:
        """A called routine's body, which notes the call in the history variable."""
        out = self.run(self.donor.routines[name].body, level, item, True, index)
        return out if self.var is None else self.noted(out, index, item)

    def noted(self, nodes: list[ir.Node], index: int, item: str) -> list[ir.Node]:
        """`nodes`, a routine's body, with `set_var H index` first on every path, but after a read
        of the history (an `if_var` on H, from `_prev`), whose arms follow it: the variable holds
        the call before the newest until then. Right while no call precedes the read in the
        routine, which a call would make the newest (`P3_HISTORY` warns); two reads in a row
        break it too, the second seeing the first's note (an error). A routine inlined into this
        one is a call, whatever it reads."""
        var = self.var
        assert var is not None

        def note() -> ir.Node:
            return edit.insn("set_var", var, index)

        def is_read(x: ir.Located) -> bool:
            return isinstance(x, ir.Block) and x.op.name == "if_var" and x.args.get("var") == var

        def reads(ns: Sequence[ir.Node]) -> bool:
            return any(is_read(x) and id(x) not in self._placed for x in ir.walk(ns))

        def calls(ns: Sequence[ir.Node]) -> bool:
            return any(
                isinstance(x, ir.Insn)
                and (x.op.name == "call" or (x.op.name == "set_var" and x.args.get("var") == var))
                for x in ir.walk(ns)
            )

        def place(ns: list[ir.Node]) -> list[ir.Node]:
            at = next((i for i, x in enumerate(ns) if reads([x])), None)
            if at is None:
                return [note(), *ns]
            if calls(ns[:at]):
                text = "a call runs before switch_prev_call: it reads that call, MHP3rd this one"
                self.add(item, "warning", "P3_HISTORY", text)
            if reads(ns[at + 1 :]):
                text = "switch_prev_call twice in a row: the second would read the first's note"
                self.add(item, "error", "P3_HISTORY", text)
            return [*ns[:at], sink(ns[at]), *ns[at + 1 :]]

        def sink(n: ir.Node) -> ir.Node:
            """The note on each path through the block that does not read."""
            assert isinstance(n, ir.Block)
            if n.op.shape is Shape.IF:
                n.head = place(n.head)
                if not n.arms:
                    n.arms.append(ir.Arm(1, {}, []))
            for a in n.arms:
                a.body = place(a.body)
            if n.op.shape is Shape.SWITCH and not any(a.sub == 2 for a in n.arms):
                n.arms.append(ir.Arm(2, {}, [note()]))
            return n

        out = place(nodes)
        self._placed |= {id(x): x for x in ir.walk(out) if is_read(x)}
        return out

    # ---- nodes

    def run(
        self,
        nodes: Sequence[p3.Node],
        level: int,
        item: str,
        tail: bool,
        call: int | None = None,
    ) -> list[ir.Node]:
        b = Body()
        for i, n in enumerate(nodes):
            last = tail and all(_is_end(x) for x in nodes[i + 1 :])
            b.out += self.node(n, Where(level, item, last, call, b))
        return b.out

    def sub(self, nodes: Sequence[p3.Node], w: Where) -> list[ir.Node]:
        """A block's body, where its own node was."""
        return self.run(nodes, w.level, w.item, w.tail, w.call)

    def node(self, n: p3.Node, w: Where) -> list[ir.Node]:
        if isinstance(n, p3.Leave):
            self.add(w, "error", "P3_UNSUPPORTED", f"{n.op.mnemonic(n.sub)} leaves a loop")
            return []
        handler = OPS.get(n.op.name)
        if handler is None:
            self.add(w, "error", "P3_UNSUPPORTED", f"{n.op.name} has no MHFU counterpart")
            return []
        out: list[ir.Node] = handler(self, n, w)
        return out

    def if_(
        self,
        w: Where,
        what: str,
        name: str,
        args: Sequence[int],
        then: list[ir.Node],
        other: list[ir.Node],
    ) -> list[ir.Node]:
        """An if, unless both its arms are empty."""
        if not then and not other:
            self.add(w, "info", "P3_DROPPED", f"{what}: every arm is empty")
            return []
        return [edit.if_(name, *args, then=then, else_=other or None)]

    def fold(self, n: p3.Block, w: Where, answer: bool, why: str) -> list[ir.Node]:
        """An if whose test is decided: the arm it takes, in its place."""
        then, other = _branches(n)
        keep, drop, name = (then, other, "else") if answer else (other, then, "then")
        if drop:
            text = f"{_text(n)} taken as {answer} ({why}): its {name} arm is dropped"
            first = f"{p3.count(drop)} instructions, first {_text(drop[0])}"
            self.add(w, "warning", "P3_FOLDED", f"{text} ({first})")
        return self.sub(keep, w)

    def yields(self, nodes: Sequence[ir.Node]) -> Yields:
        """Whether running `nodes` ends in a yield."""
        found = [self._yields(n) for n in nodes]
        if Yields.ALWAYS in found:
            return Yields.ALWAYS
        return Yields.MAYBE if Yields.MAYBE in found else Yields.NEVER

    def _yields(self, n: ir.Node) -> Yields:
        if isinstance(n, ir.Insn):
            label = n.args.get("routine")
            if n.op.name == "call" and isinstance(label, ir.Sym):
                return (
                    self.yields(self.routines[label.label])
                    if label.label in self.routines
                    else Yields.MAYBE
                )
            return Yields.ALWAYS if n.op.name == "enter" else Yields.NEVER
        ways = [self.yields(a.body) for a in n.arms]
        if n.op.shape is Shape.IF:
            ways += [self.yields(n.head)] + ([] if n.arms else [Yields.NEVER])
        if all(y is Yields.ALWAYS for y in ways):
            return Yields.ALWAYS
        return Yields.NEVER if all(y is Yields.NEVER for y in ways) else Yields.MAYBE

    # ---- ops

    def _enter(self, n: p3.Insn, w: Where) -> list[ir.Node]:
        """One `enter` per target of the pair, in order."""
        pair = (n.args["main"], n.args["sub"])
        targets = self.spec.targets(pair)
        if not targets:
            self.add(w, "error", "P3_PAIR_UNMAPPED", f"enter {pair[0]},{pair[1]} has no host pair")
        out: list[ir.Node] = []
        for target in targets:
            if isinstance(target, str):
                slot = self.slots.get(target)
                if slot is None:
                    text = f"enter {pair[0]},{pair[1]} plays own move {target}, which has no slot"
                    self.add(w, "error", "P3_OWN_MISSING", text)
                    continue
                out.append(edit.insn("enter", addresses.OWN_MOVE_MAIN, slot, 0))
            else:
                out.append(edit.insn("enter", *target, 0))
            self.uses[pair, target] += 1
        return out

    def _end(self, n: p3.Insn, w: Where) -> list[ir.Node]:
        if not w.tail:
            self.add(w, "error", "P3_UNSUPPORTED", "end before the end of the routine")
        return []

    def _call(self, n: p3.Insn, w: Where) -> list[ir.Node]:
        index = n.args["routine"]
        name = self.donor.entry(p3ops.CALL, index)
        if name is None:
            self.add(w, "error", "P3_UNSUPPORTED", f"call {index}: the slot is empty")
            return []
        if w.level == PASTE:
            label = self.routine(name, index, w.item)
            return [] if label is None else [edit.insn("call", ir.Sym(label))]
        if name in self._stack:
            self.add(w, "error", "P3_UNSUPPORTED", f"{name} calls itself")
            return []
        self._stack.append(name)
        try:
            return self.callee(name, index, INLINE, w.item)
        finally:
            self._stack.pop()

    def _call_base(self, n: p3.Insn, w: Where) -> list[ir.Node]:
        index = n.args["routine"]
        name = self.donor.entry(p3ops.BASE, index)
        if w.level != PASTE or not w.tail or name is None:
            why = "the slot is empty" if name is None else "only a last call_base is pasted"
            self.add(w, "error", "P3_UNSUPPORTED", f"call_base {index}: {why}")
            return []
        return self.pasted(name, w.item)

    def _raised(self, w: Where, values: Sequence[int], raised: Sequence[int], what: str) -> None:
        for i in raised:
            text = f"{what} weight {values[i]} is raised to 1 of {TOTAL}"
            self.add(w, "info", "P3_WEIGHT", text)

    def _random(self, n: p3.Block, w: Where) -> list[ir.Node]:
        live = [a for a in n.arms if a.value]
        values = [a.value or 0 for a in live]
        weights, raised = shares(values)
        if len(live) + (sum(weights) < TOTAL) > ARMS:
            return self._split(live, w)
        self._raised(w, values, raised, "random")
        bodies = [self.sub(a.body, w) for a in live]
        if not any(bodies):
            self.add(w, "info", "P3_DROPPED", "random: every arm is empty")
            return []
        return [edit.random(list(zip(weights, bodies, strict=True)))]

    def _split(self, live: Sequence[p3.Arm], w: Where) -> list[ir.Node]:
        """A random with more live arms than MHFU's takes: an outer random over groups of at most
        `GROUP`, weighted by their shares, each group an inner random weighted within it. Each
        level rounds its own weights, so the sums the donor draws survive."""
        count = -(-len(live) // GROUP)
        size = -(-len(live) // count)
        groups = [live[i : i + size] for i in range(0, len(live), size)]
        sums = [sum(a.value or 0 for a in g) for g in groups]
        outer, raised = shares(sums)
        if len(groups) + (sum(outer) < TOTAL) > ARMS:
            text = (
                f"random has {len(live)} live arms, a split over {len(groups)} groups is too wide"
            )
            self.add(w, "error", "P3_WIDE", text)
            return []
        self._raised(w, sums, raised, "random group")
        branches: list[tuple[int, list[ir.Node]]] = []
        for group, whole, weight in zip(groups, sums, outer, strict=True):
            values = [a.value or 0 for a in group]
            inner, raised = shares(values, whole)
            self._raised(w, values, raised, "random")
            bodies = [self.sub(a.body, w) for a in group]
            pick: list[ir.Node] = []
            if any(bodies):
                pick.append(edit.random(list(zip(inner, bodies, strict=True))))
            branches.append((weight, pick))
        if not any(pick for _, pick in branches):
            self.add(w, "info", "P3_DROPPED", "random: every arm is empty")
            return []
        text = f"random with {len(live)} live arms is split into {len(groups)} inner randoms"
        self.add(w, "info", "P3_SPLIT", text)
        return [edit.random(branches)]

    def _clone(self, nodes: list[ir.Node]) -> list[ir.Node]:
        """Copies of translated `nodes`, the history reads among them still noted."""
        out = edit.clone(nodes)
        for a, b in zip(ir.walk(nodes), ir.walk(out), strict=True):
            if id(a) in self._placed:
                self._placed[id(b)] = b
        return out

    def _range(self, n: p3.Block, w: Where) -> list[ir.Node]:
        cases: dict[int, list[ir.Node]] = {}
        default: list[ir.Node] = []
        for a in n.arms:
            body = self.sub(a.body, w)
            if a.sub == p3ops.CASE and a.value is not None:
                cases.setdefault(a.value, body)
            else:
                default = body
        top = max(cases, default=-1)
        if top + 2 > BUCKETS:
            self.add(
                w,
                "error",
                "P3_WIDE",
                f"switch_range has cases up to {top}, MHFU has {BUCKETS} buckets",
            )
            return []
        buckets = []
        for k in range(top + 1):
            if k not in cases:
                self.add(
                    w,
                    "info",
                    "P3_DROPPED",
                    f"switch_range has no case {k}: it takes the default's body",
                )
            buckets.append(cases[k] if k in cases else self._clone(default))
        buckets.append(default)
        if not any(buckets):
            self.add(w, "info", "P3_DROPPED", "switch_range: every arm is empty")
            return []
        return [edit.switch("switch_range", cases=[(b,) for b in buckets])]

    def _cases(
        self, n: p3.Block, w: Where, bound: bool, shift: int = 0
    ) -> tuple[list[tuple[int, list[ir.Node]]], list[ir.Node]]:
        """A switch's cases, translated and ascending, and its default. A case no value reaches is
        dropped before it is translated: with `bound` (the first limit above the value wins) one
        at or under an earlier case, else one that repeats a value. `shift` moves each value up."""
        cases: dict[int, list[ir.Node]] = {}
        default: list[ir.Node] = []
        top = -1
        for a in n.arms:
            if a.sub != p3ops.CASE or a.value is None:
                default = self.sub(a.body, w)
                continue
            value = min(a.value + shift, 0xFF)
            if value <= top if bound else value in cases:
                self.add(w, "info", "P3_DROPPED", f"{n.op.name} case {a.value} is never reached")
                continue
            cases[value] = self.sub(a.body, w)
            top = max(top, value)
        return sorted(cases.items()), default

    def _switch(
        self, n: p3.Block, w: Where, *args: int, bound: bool = True, shift: int = 0
    ) -> list[ir.Node]:
        """A switch of the same name on the host, its cases kept in step."""
        cases, default = self._cases(n, w, bound, shift)
        if not default and not any(body for _, body in cases):
            self.add(w, "info", "P3_DROPPED", f"{n.op.name}: every arm is empty")
            return []
        return [edit.switch(n.op.name, *args, cases=cases, default=default or None)]

    def _answer(
        self, n: p3.Block, w: Where, value: int, why: str, say: bool = False
    ) -> list[ir.Node]:
        """A switch whose value is taken as `value`: the case with it, else the default, in its
        place; the arms with code that go are named (a note even when none does, with `say`)."""
        arms = [a for a in n.arms if a.sub == p3ops.CASE and a.value == value]
        keep = next(iter(arms or [a for a in n.arms if a.sub != p3ops.CASE]), None)
        gone = [a for a in n.arms if a is not keep and a.body]
        if gone:
            names = ", ".join(
                f"{f'case {a.value}' if a.sub == p3ops.CASE else 'default'} "
                f"({p3.count(a.body)} instructions, first {_text(a.body[0])})"
                for a in gone
            )
            text = f"{_text(n)} taken as answer {value} ({why}): arms dropped: {names}"
            self.add(w, "warning", "P3_FOLDED", text)
        elif say:
            self.add(w, "info", "P3_FOLDED", f"{_text(n)} taken as answer {value} ({why})")
        return self.sub(keep.body, w) if keep else []

    def _query(self, n: p3.Block, w: Where) -> list[ir.Node]:
        """The host's `switch_query` on an arg the charge helper answers; any other is taken as
        answer 0, as is every one without `[brain] charge`."""
        arg = n.args["arg"]
        if self.spec.charge and arg in QUERIES:
            return self._switch(n, w, arg, bound=False)
        why = "no charge helper" if not self.spec.charge else "the helper does not answer it"
        return self._answer(n, w, 0, why)

    def _dist(self, n: p3.Block, w: Where) -> list[ir.Node]:
        return self._switch(n, w)

    def _hp(self, n: p3.Block, w: Where) -> list[ir.Node]:
        return self._switch(n, w, shift=HP_SHIFT)

    def _side(self, n: p3.Block, w: Where) -> list[ir.Node]:
        then, other = _branches(n)
        return self.if_(
            w, n.op.name, "if_target_in_arc", SIDE, self.sub(then, w), self.sub(other, w)
        )

    def _global_540(self, n: p3.Block, w: Where) -> list[ir.Node]:
        return self._answer(n, w, GLOBAL_540, "the host has no such word", say=True)

    def _mode(self, n: p3.Block, w: Where) -> list[ir.Node]:
        cases: list[tuple[int, list[ir.Node]]] = []
        out: list[ir.Node] = []
        for a in n.arms:
            body = self.sub(a.body, w)
            if a.sub == p3ops.CASE and a.value is not None:
                cases.append((a.value, body))
            else:
                out = body
        for value, then in reversed(cases):
            if value not in MODES:
                self.add(w, "error", "P3_UNSUPPORTED", f"switch_mode case {value} has no MHFU mode")
                continue
            out = self.if_(w, "switch_mode", MODES[value], [], then, out)
        return out

    def _enraged(self, n: p3.Block, w: Where) -> list[ir.Node]:
        then, other = _branches(n)
        return self.if_(w, n.op.name, "if_enraged", [], self.sub(then, w), self.sub(other, w))

    def _facing(self, n: p3.Block, w: Where) -> list[ir.Node]:
        """The MHFU tests are for outside the cone, so the arms swap."""
        inside, outside = _branches(n)
        ang = n.args["ang"]
        deg = (ang * 360 + 128) // 256
        name = "if_pursuit_off_facing"
        if deg > 127:
            name = "if_off_facing"
            text = f"if_facing {ang} is {deg} degrees, over the 127 of if_pursuit_off_facing"
            self.add(w, "warning", "P3_APPROX", f"{text}: if_off_facing tests the hunter")
        out = self.sub(outside, w)
        return self.if_(w, n.op.name, name, [min(deg, 255)], out, self.sub(inside, w))

    def _gauge(self, n: p3.Block) -> bool:
        """Whether the fatigue helper answers this test."""
        if not self.spec.fatigue:
            return False
        return n.op.name == "if_tired" or (n.op.name == "if_stamina_low" and n.args["pct10"] == 0)

    def _when(self, n: p3.Block, w: Where) -> list[ir.Node]:
        """The host's gauge A where the helper answers; else the manifest's answer, none being
        needed when neither arm comes to anything."""
        if self._gauge(n):
            then, other = _branches(n)
            return self.if_(w, n.op.name, GAUGE, [], self.sub(then, w), self.sub(other, w))
        self.asked.add(n.op.name)
        answer = self.spec.when.get(n.op.name)
        if answer is not None:
            return self.fold(n, w, answer, f"when.{n.op.name}")
        if not any(self.sub(arm, w) for arm in _branches(n)):
            self.add(w, "info", "P3_DROPPED", f"{_text(n)}: every arm is empty")
            return []
        self.add(w, "error", "P3_UNDECIDED", f"{n.op.name} has no host state: set when.{n.op.name}")
        return []

    def _near(self, n: p3.Block, w: Where) -> list[ir.Node]:
        return self.fold(n, w, False, "the host has no stage objects")

    def _target(self, n: p3.Insn, w: Where) -> list[ir.Node]:
        if any(n.args.values()):
            self.add(w, "error", "P3_UNSUPPORTED", f"{_text(n)}: only set_target 0 0 0 is dropped")
        else:
            self.add(w, "info", "P3_DROPPED", f"{_text(n)}: the host picked its target before")
        return []

    def _set_latch(self, n: p3.Insn, w: Where) -> list[ir.Node]:
        w.body.latch = (len(w.body.out), n.args["value"])
        return []

    def _latch(self, n: p3.Block, w: Where) -> list[ir.Node]:
        """The latch is zero once something yielded since the set_latch, its value before."""
        if w.body.latch is None:
            self.add(w, "error", "P3_LATCH", "switch_latch has no set_latch before it in its block")
            return []
        start, value = w.body.latch
        since = self.yields(w.body.out[start:])
        if since is Yields.MAYBE:
            self.add(
                w, "error", "P3_LATCH", "something between set_latch and switch_latch may yield"
            )
            return []
        want = 0 if since is Yields.ALWAYS else value
        arms = [a for a in n.arms if a.sub == p3ops.CASE and a.value == want]
        arms = arms or [a for a in n.arms if a.sub != p3ops.CASE]
        return self.sub(arms[0].body if arms else [], w)

    def _prev(self, n: p3.Block, w: Where) -> list[ir.Node]:
        """`if_var` on the history, case by case; `noted` puts each arm's note first."""
        if w.call is None:
            self.add(w, "error", "P3_HISTORY", "switch_prev_call is only read in a called routine")
            return []
        if self.var is None:
            return []
        cases: list[tuple[int, list[ir.Node]]] = []
        out: list[ir.Node] = []
        for a in n.arms:
            body = self.sub(a.body, w)
            if a.sub == p3ops.CASE and a.value is not None:
                cases.append((a.value, body))
            else:
                out = body
        for value, then in reversed(cases):
            out = [edit.if_("if_var", self.var, value, then=then, else_=out)]
        return out

    def _host(self, n: p3.Insn | p3.Block, w: Where) -> list[ir.Node]:
        self.add(w, "error", "P3_HOST_OP", f"{n.op.name}: the host's roaming owns it")
        return []


OPS: Mapping[str, Callable[..., list[ir.Node]]] = {
    "enter": Translator._enter,
    "end": Translator._end,
    "end_alias": Translator._end,
    "call": Translator._call,
    "call_base": Translator._call_base,
    "random": Translator._random,
    "switch_range": Translator._range,
    "switch_mode": Translator._mode,
    "if_enraged": Translator._enraged,
    "if_facing": Translator._facing,
    "if_target_side": Translator._side,
    "switch_query": Translator._query,
    "switch_dist": Translator._dist,
    "switch_hp": Translator._hp,
    "switch_global_540": Translator._global_540,
    "if_object_near": Translator._near,
    "set_target": Translator._target,
    "set_latch": Translator._set_latch,
    "switch_latch": Translator._latch,
    "switch_prev_call": Translator._prev,
    **dict.fromkeys(WHEN, Translator._when),
    **dict.fromkeys(HOST_OPS, Translator._host),
}
"""What each MHP3rd opcode becomes, by name: a method of Translator. Nothing else classifies
ops; one that is not here is P3_UNSUPPORTED."""


@dataclass
class Spliced:
    edits: EditSet
    """The host row's edits: the splice routine's new body and the new routines."""
    findings: list[Finding]
    routines: dict[str, str]
    """Each new routine's name, and the donor routine it comes from."""
    uses: dict[tuple[Pair, Pair | str], int] = field(default_factory=dict)
    """Each donor pair entered, with the host pair or own move (its name) it became, and how
    often."""
    slots: dict[str, int] = field(default_factory=dict)
    """The slot of each own move the enters name."""


def _origin(donor: p3.Brain, name: str) -> str:
    return f"MHP3rd {donor.id} {name} 0x{donor.routines[name].va:08X}"


def _home(nodes: list[ir.Node], target: ir.Located) -> list[ir.Node] | None:
    """The list of nodes, `nodes` or one under it, that holds `target` itself."""
    if any(n is target for n in nodes):
        return nodes
    for n in nodes:
        if isinstance(n, ir.Block):
            for body in (n.head, *(a.body for a in n.arms)):
                if (found := _home(body, target)) is not None:
                    return found
    return None


def _steps(host: ir.Program, es: EditSet) -> None:
    """A random that can follow another draw in a run is preceded by `rng_step2`, as the host's
    own picks are: the draws are found on the program the edits make."""
    applied = editset.apply(host, es)
    if not applied.ok:
        return
    todo = []
    for d in budget.Budget(applied.program).draws:
        key, _, k = d.node.rpartition(".")
        nodes = es.inserts[key].nodes if key in es.inserts else es.routines.get(key)
        if nodes is None or not k.isdecimal():
            continue
        found = list(edit.everything(nodes))[int(k)]
        if (home := _home(nodes, found)) is not None:
            todo.append((home, found))
    for home, found in todo:
        home.insert(next(i for i, x in enumerate(home) if x is found), edit.insn("rng_step2"))


def splice(
    donor: p3.Brain,
    host: ir.Program,
    spec: manifest.Brain,
    survey: Mapping[int, script_vars.Var] | None = None,
    slots: Mapping[str, int] | None = None,
    overlay: Overlay | None = None,
    named: Collection[Pair] = (),
) -> Spliced:
    """The host's `spec.splice` routine with the donor's `spec.entry` in its place, as edits to
    `host`; `findings` say what could not carry over, an error that it cannot be built. `survey`
    (`vars.survey` of the host) picks the call history's variable when `spec.history` is None;
    `slots` (`moves.slots`) numbers the own moves that `spec.pairs` plays; `overlay` (the host's
    species overlay) lets `spec.fatigue` see whether its code drains gauge A."""
    es = EditSet.of(host)
    target = next((r for r in host.routines() if r.name == spec.splice), None)
    last = target.body[-1] if target and target.body else None
    if target is None:
        return _failed(es, spec, f"the host has no routine {spec.splice}")
    if spec.entry not in donor.routines:
        return _failed(es, spec, f"the donor has no routine {spec.entry}")
    if not (isinstance(last, ir.Insn) and last.op.name == "end"):
        return _failed(es, spec, f"{spec.splice} has no end to keep")
    keep = 0
    while keep < len(target.body) - 1 and isinstance(target.body[keep], ir.Insn):
        keep += 1
    for n in target.body[keep:-1]:
        es.node(n.id).delete = True
    t = Translator(donor, spec, survey, slots)
    nodes = t.paste(spec.entry, spec.splice)
    for label, body in t.routines.items():
        es.routines[label] = body
        es.notes[label] = _origin(donor, t.sources[label])
    if nodes:
        key = es.new_key()
        where = (
            Anchor("after", target.body[keep - 1].id) if keep else Anchor("into", spec.splice, 0)
        )
        es.inserts[key] = Insert(where, nodes)
        es.notes[key] = _origin(donor, spec.entry)
    _steps(host, es)
    own = {n: t.slots[n] for _, n in t.uses if isinstance(n, str)}
    found = [*t.findings, *_unused(spec, t, named), *_gauge_a(host, spec, overlay)]
    return Spliced(es, found, dict(t.sources), dict(t.uses), own)


def _failed(es: EditSet, spec: manifest.Brain, text: str) -> Spliced:
    return Spliced(es, [Finding("error", "P3_ROUTINE", text, None, spec.splice)], {})


def _unused(spec: manifest.Brain, t: Translator, named: Collection[Pair] = ()) -> list[Finding]:
    """Manifest entries that match nothing in the donor's code, that the charge table does not name
    and whose move no other entry plays."""
    met = {f"{m},{s}" for m, s in [*t.entered, *named, *spec.shared()]}
    out = [
        Finding("warning", "P3_PAIR_UNUSED", f"{k} is mapped but the donor code never enters it")
        for k in spec.pairs
        if k not in met
    ]
    out += [
        Finding(
            "warning", "P3_WHEN", f"when.{k} is no test the translation folds: {', '.join(WHEN)}"
        )
        for k in spec.when
        if k not in WHEN
    ]
    if spec.fatigue:
        out += [
            Finding(
                "warning",
                "P3_WHEN_IGNORED",
                f"when.{k} is not asked: `[brain] fatigue` answers {k}"
                + (" at 0" if k == "if_stamina_low" else ""),
                None,
                spec.splice,
            )
            for k in spec.when
            if k in FATIGUE_TESTS and k not in t.asked
        ]
    return out


def _gauge_a(host: ir.Program, spec: manifest.Brain, overlay: Overlay | None) -> list[Finding]:
    """With `spec.fatigue`, what already keeps gauge A: the host species' code that calls
    `GAUGE_A_LATCH` (not looked at without `overlay`), and a host routine other than `spec.splice`
    that tests or refills it."""
    if not spec.fatigue:
        return []
    out = []
    latch = addresses.GAUGE_A_LATCH
    if overlay is not None and (n := script_vars.scan(overlay).calls.get(latch, 0)):
        text = f"the host's species code calls {latch.name} {n}x and drains gauge A itself"
        out.append(Finding("error", "P3_FATIGUE_HOST", text, None, spec.splice))
    users = Counter(
        n.op.name
        for r in host.routines()
        if r.name != spec.splice
        for n in ir.walk(r.body)
        if isinstance(n, ir.Insn | ir.Block) and n.op.name in GAUGE_A_OPS
    )
    for op, count in sorted(users.items()):
        text = f"the host's script uses gauge A: {op} {count}x outside {spec.splice}"
        out.append(Finding("error", "P3_FATIGUE_HOST", text, None, spec.splice))
    return out
