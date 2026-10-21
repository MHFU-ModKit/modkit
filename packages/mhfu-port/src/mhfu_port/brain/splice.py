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
use); em_vhook plays that move in place of a host pair.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum

from mhfu import addresses
from mhfu.em.script import budget, edit, editset, ir
from mhfu.em.script import vars as script_vars
from mhfu.em.script.check import Finding
from mhfu.em.script.editset import Anchor, EditSet, Insert
from mhfu.em.script.opcodes import Shape

from .. import manifest
from . import p3, p3ops

Pair = manifest.Pair
TOTAL = 32
"""What an MHFU random's weights add up to; MHP3rd's add up to 100."""
ARMS = 13
"""Branches of an MHFU random, the padding one included."""
BUCKETS = 5
"""Buckets of an MHFU switch_range."""
MODES: Mapping[int, str] = {1: "if_mode2"}
"""MHP3rd's mode byte to the MHFU test that holds in that mode."""
WHEN = ("if_target_state", "if_tired", "if_stamina_low", "if_target_set")
"""The tests the manifest answers, in `[brain.when]`: MHFU has no state for them."""
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
    entered: int | None
    """The call index, when the node opens the body of a called routine."""
    body: Body


def shares(weights: Sequence[int]) -> tuple[list[int], list[int]]:
    """MHFU weights for MHP3rd's: each `w * 32 / 100` by largest remainder, a non-zero one at least
    1, the total at most 32; and the positions that were raised to 1."""
    exact = [w * TOTAL for w in weights]
    out = [e // 100 for e in exact]
    spare = max(0, min(TOTAL, (sum(exact) + 50) // 100) - sum(out))
    for i in sorted(range(len(out)), key=lambda j: (-(exact[j] % 100), j))[:spare]:
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


def _first(nodes: Sequence[p3.Node], name: str) -> bool:
    return bool(nodes) and isinstance(nodes[0], p3.Block) and nodes[0].op.name == name


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
        self.var: int | None = None
        """The history variable, once the region is known to read the call before."""
        self._stack: list[str] = []

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
        """A called routine's body, which first notes the call in the history variable."""
        nodes = self.donor.routines[name].body
        out = self.run(nodes, level, item, True, index)
        if self.var is not None and not _first(nodes, "switch_prev_call"):
            out.insert(0, edit.insn("set_var", self.var, index))
        return out

    # ---- nodes

    def run(
        self,
        nodes: Sequence[p3.Node],
        level: int,
        item: str,
        tail: bool,
        entered: int | None = None,
    ) -> list[ir.Node]:
        b = Body()
        for i, n in enumerate(nodes):
            last = tail and all(_is_end(x) for x in nodes[i + 1 :])
            b.out += self.node(n, Where(level, item, last, entered if i == 0 else None, b))
        return b.out

    def sub(self, nodes: Sequence[p3.Node], w: Where) -> list[ir.Node]:
        """A block's body, where its own node was."""
        return self.run(nodes, w.level, w.item, w.tail)

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
        pair = (n.args["main"], n.args["sub"])
        target = self.spec.target(pair)
        if target is None:
            self.add(w, "error", "P3_PAIR_UNMAPPED", f"enter {pair[0]},{pair[1]} has no host pair")
            return []
        if isinstance(target, str):
            slot = self.slots.get(target)
            if slot is None:
                text = f"enter {pair[0]},{pair[1]} plays own move {target}, which has no slot"
                self.add(w, "error", "P3_OWN_MISSING", text)
                return []
            enter = edit.insn("enter", addresses.OWN_MOVE_MAIN, slot, 0)
        else:
            enter = edit.insn("enter", *target, 0)
        self.uses[pair, target] += 1
        return [enter]

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

    def _random(self, n: p3.Block, w: Where) -> list[ir.Node]:
        live = [a for a in n.arms if a.value]
        weights, raised = shares([a.value or 0 for a in live])
        if len(live) + (sum(weights) < TOTAL) > ARMS:
            self.add(w, "error", "P3_WIDE", f"random has {len(live)} live arms, MHFU takes {ARMS}")
            return []
        for i in raised:
            self.add(
                w, "info", "P3_WEIGHT", f"random weight {live[i].value} is raised to 1 of {TOTAL}"
            )
        bodies = [self.sub(a.body, w) for a in live]
        if not any(bodies):
            self.add(w, "info", "P3_DROPPED", "random: every arm is empty")
            return []
        return [edit.random(list(zip(weights, bodies, strict=True)))]

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
            buckets.append(cases[k] if k in cases else edit.clone(default))
        buckets.append(default)
        if not any(buckets):
            self.add(w, "info", "P3_DROPPED", "switch_range: every arm is empty")
            return []
        return [edit.switch("switch_range", cases=[(b,) for b in buckets])]

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

    def _when(self, n: p3.Block, w: Where) -> list[ir.Node]:
        """The manifest's answer; none is needed when neither arm comes to anything."""
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
        """`if_var` on the history, each arm noting the call after it has read it."""
        if w.entered is None:
            self.add(
                w, "error", "P3_HISTORY", "switch_prev_call is only read at the start of a call"
            )
            return []
        if self.var is None:
            return []
        var, index = self.var, w.entered

        def note() -> ir.Node:
            return edit.insn("set_var", var, index)

        cases: list[tuple[int, list[ir.Node]]] = []
        out: list[ir.Node] = []
        for a in n.arms:
            body = self.sub(a.body, w)
            if a.sub == p3ops.CASE and a.value is not None:
                cases.append((a.value, body))
            else:
                out = body
        out = [note(), *out]
        for value, then in reversed(cases):
            out = [edit.if_("if_var", var, value, then=[note(), *then], else_=out)]
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
) -> Spliced:
    """The host's `spec.splice` routine with the donor's `spec.entry` in its place, as edits to
    `host`; `findings` say what could not carry over, an error that it cannot be built. `survey`
    (`vars.survey` of the host) picks the call history's variable when `spec.history` is None;
    `slots` (`moves.slots`) numbers the own moves that `spec.pairs` plays."""
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
    return Spliced(es, [*t.findings, *_unused(spec, t)], dict(t.sources), dict(t.uses), own)


def _failed(es: EditSet, spec: manifest.Brain, text: str) -> Spliced:
    return Spliced(es, [Finding("error", "P3_ROUTINE", text, None, spec.splice)], {})


def _unused(spec: manifest.Brain, t: Translator) -> list[Finding]:
    """Manifest entries that match nothing in the donor's code."""
    met = {f"{m},{s}" for m, s in t.entered}
    out = [
        Finding("warning", "P3_PAIR_UNUSED", f"{k} is mapped but the donor code never enters it")
        for k in spec.pairs
        if k not in met
    ]
    return out + [
        Finding(
            "warning", "P3_WHEN", f"when.{k} is no test the translation folds: {', '.join(WHEN)}"
        )
        for k in spec.when
        if k not in WHEN
    ]
