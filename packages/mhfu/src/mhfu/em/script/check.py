# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""What an edited, resolved program does that the game will not forgive.

    for f in check.check(edit.resolve(p)):           # f.level, f.code, f.text, f.node
        print(f.level, f.code, f.text)

Only new code is held to the rules, and the native blocks it sits in: a node with no `pc` is
new, and a block is touched when anything inside it is. An unedited program has no findings,
the odd native stretches (a random_shifted inside a random, the 0x94 openers) included.
Image checks assemble the program, or take the image it was assembled to, and read the bytes
the way the game does (`walk`) and the way the decoder does. Run checks follow the program's
paths the way `budget` does and speak only of a path through new code: OPS_PER_RUN, RESTART_LOOP
and CORRELATED_DRAW. Anything that makes a routine run off its end, and a restart that never
yields, is an error; dead code, a random that follows another draw and a branch that never runs
are warnings. Every native routine ends each path with an end or a goto, so one with new code
in it that can run off its end is the edit's doing; a deleted `end` in a routine with nothing new
is left to the decoder (DECODE).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property

from ... import addresses
from . import asm, budget, ir, walk
from .disasm import SENTINEL, Reader
from .lift import LiftError, routine
from .opcodes import END_KINDS, OPCODES, Flow, Shape, slot

__all__ = ["Finding", "check", "decoded"]

_TOTAL = 32
"""What a random's weights add up to."""
_RANDOM = 0x80
"""The marker a random and a random_shifted share."""
_MAINS = range(8)
"""The `main` of a pair the game has an action table for; `addresses.OWN_MOVE_MAIN` is em_vhook's
own move, which the game never sees."""
Token = tuple[object, ...]


@dataclass(frozen=True)
class Finding:
    level: str
    """`error` or `warning`."""
    code: str
    text: str
    node: str | None = None
    """The id of the node it is about."""
    item: str | None = None
    """The routine or list it is in."""


def _reader(img: asm.Image) -> Reader:
    """The image's bytes at their addresses; past them, the byte a scan stops on."""

    def read(pc: int) -> int:
        i = pc - img.base
        return img.data[i] if 0 <= i < len(img.data) else SENTINEL

    return read


def _touched(nodes: Sequence[ir.Node], out: set[int]) -> bool:
    """Whether anything in `nodes` is new; `out` collects the `id()` of the touched blocks."""
    found = False
    for n in nodes:
        if isinstance(n, ir.Insn):
            found |= n.pc is None
            continue
        inner = n.pc is None
        inner |= _touched(n.head, out)
        for a in n.arms:
            inner |= a.pc is None
            inner |= _touched(a.body, out)
        if inner:
            out.add(id(n))
        found |= inner
    return found


class _Checks:
    def __init__(self, p: ir.Program) -> None:
        self.p = p
        self.out: list[Finding] = []
        self.touched: set[int] = set()
        self.reach = p.reach()
        self.tables = p.tables()
        self.headers = {h.name: h for h in p.headers()}

    def add(self, level: str, code: str, text: str, node: str | None, item: str | None) -> None:
        self.out.append(Finding(level, code, text, node or None, item))

    def run(self) -> list[Finding]:
        for r in self.p.routines():
            _touched(r.body, self.touched)
            self.nodes(r, r.body, [])
        for t in self.p.items:
            if isinstance(t, ir.Table) and len(t.slots) > ir.MAX_LIST:
                text = f"{t.name} holds {len(t.slots)} entries, and an index is a byte"
                self.add("error", "LIST_TOO_LONG", text, None, t.name)
        return self.out

    def nodes(self, r: ir.Routine, nodes: Sequence[ir.Node], stack: list[ir.Block]) -> None:
        for n in nodes:
            self.labels(r, n)
            if isinstance(n, ir.Insn):
                if n.pc is None:
                    self.refs(r, n)
                    self.insn(r, n)
                continue
            for a in n.arms:
                self.labels(r, a)
            self.nest(r, n, stack)
            if id(n) in self.touched:
                self.block(r, n)
            self.nodes(r, n.head, [*stack, n])
            for a in n.arms:
                self.nodes(r, a.body, [*stack, n])

    def labels(self, r: ir.Routine, n: ir.Located) -> None:
        what = n.op.name if not isinstance(n, ir.Arm) else "arm"
        for name, v in n.args.items():
            if isinstance(v, ir.Sym):
                text = f"{what}: {name} is the label {v.label!r}; resolve it first"
                self.add("error", "UNRESOLVED", text, n.id, r.name)

    def nest(self, r: ir.Routine, b: ir.Block, stack: list[ir.Block]) -> None:
        """Nesting that new code creates: a random_shifted shares its markers with a random,
        and a repeat keeps one loop return."""
        if b.op.code != b.op.marker and b.op.marker == _RANDOM:
            outer = [a for a in stack if a.op.marker == _RANDOM]
            code, why = "RANDOM_NESTED", "its markers are the outer block's"
        elif b.op.shape is Shape.REPEAT:
            outer = [a for a in stack if a.op.shape is Shape.REPEAT]
            code, why = "REPEAT_NESTED", "a repeat keeps one loop return"
        else:
            return
        if outer and (b.pc is None or any(a.pc is None for a in outer)):
            self.add("error", code, f"{b.op.name} inside {outer[-1].op.name}: {why}", b.id, r.name)

    def block(self, r: ir.Routine, b: ir.Block) -> None:
        op = b.op
        if op.shape is Shape.RANDOM:
            weights = [w for a in b.arms if isinstance(w := a.args.get("weight"), int)]
            if len(weights) == len(b.arms) and sum(weights) != _TOTAL:
                text = f"{op.name} weights add up to {sum(weights)}, not {_TOTAL}"
                self.add("error", "RANDOM_WEIGHTS", text, b.id, r.name)
            for a in b.arms:
                if a.pc is None and a.args.get("weight") == 0:
                    self.add(
                        "warning", "WEIGHT_ZERO", f"{op.name}: this branch never runs", a.id, r.name
                    )
        if op.code == walk.SHORT and b.pc is None:
            text = f"{op.name}: the game steps its opener onto the first case value"
            self.add("error", "SKIP_SHORT", text, b.id, r.name)
        count = b.args.get("count")
        if op.shape is Shape.REPEAT and isinstance(count, int) and count < 1:
            self.add("error", "REPEAT_COUNT", f"repeat runs {count} times", b.id, r.name)
        if op.ordered:
            self.ordered(r, b)

    def ordered(self, r: ir.Routine, b: ir.Block) -> None:
        """Adjacent cases, one of them new, must ascend: the game's scan stops at the first
        case above the value. A pair of native cases is as the game has it."""
        cases = [a for a in b.arms if a.sub == 1 and a.args]
        for prev, a in zip(cases, cases[1:], strict=False):
            lo, hi = next(iter(prev.args.values())), next(iter(a.args.values()))
            if (prev.pc is None or a.pc is None) and isinstance(lo, int) and isinstance(hi, int):
                if hi <= lo:
                    text = f"{b.op.name}: case {hi} follows case {lo}; cases must ascend"
                    self.add("error", "ORDERED", text, a.id, r.name)

    @cached_property
    def contexts(self) -> dict[str, frozenset[str]]:
        return budget.contexts(self.p)

    def insn(self, r: ir.Routine, n: ir.Insn) -> None:
        """What a new or edited instruction must not say: an `enter` gate (the game stores it in
        FREEZE_GATE's low byte), a pair with no action table, an `end` that does not belong."""
        if n.op.name == "enter":
            gate, main = n.args.get("gate"), n.args.get("main")
            if isinstance(gate, int) and gate != 0:
                text = f"enter: gate {gate} lands in the freeze gate; natively it is 0"
                self.add("error", "GATE_NONZERO", text, n.id, r.name)
            if isinstance(main, int) and main not in _MAINS and main != addresses.OWN_MOVE_MAIN:
                text = f"enter: main {main} is outside 0..{_MAINS[-1]}"
                self.add("error", "PAIR_RANGE", text, n.id, r.name)
        elif n.op.name == "end" and isinstance(kind := n.args.get("kind"), int):
            need = budget.needs(kind)
            have = self.contexts[r.name]
            if need is None:
                text = f"end {kind}: the game acts on no such kind"
                self.add("error", "END_KIND_CONTEXT", text, n.id, r.name)
            elif have and need not in have:
                at = ", ".join(sorted(have))
                text = f"end {END_KINDS[kind]} belongs to {need}; {r.name} runs as {at}"
                self.add("error", "END_KIND_CONTEXT", text, n.id, r.name)

    def refs(self, r: ir.Routine, n: ir.Insn) -> None:
        """The list entries a new instruction names, as each header that runs it has them."""
        for hn in self.reach[r.name]:
            fields = self.headers[hn].fields
            for ref in ir.refs(n):
                name = slot(ref.field).name
                table = fields[ref.field] if ref.field < len(fields) else None
                if isinstance(table, ir.External):
                    continue
                at = f"{n.op.name} {name}[{ref.index}] under {hn}"
                if table is None or table not in self.tables:
                    self.add("error", "INDEX_PAST_LIST", f"{at}: no {name} list", n.id, r.name)
                    continue
                slots = self.tables[table].slots
                if ref.index >= len(slots):
                    text = f"{at}: the list has {len(slots)} entries"
                    self.add("error", "INDEX_PAST_LIST", text, n.id, r.name)
                elif slots[ref.index] is None:
                    self.add("warning", "EMPTY_SLOT", f"{at}: the slot is empty", n.id, r.name)


def is_terminal(n: ir.Node, fresh: bool = True) -> bool:
    """Whether control never goes on past the node: an end or goto, or a block every way
    through which ends. `fresh` False does not count the ends and gotos that are new."""
    if isinstance(n, ir.Insn):
        return n.op.flow in (Flow.END, Flow.GOTO) and (fresh or n.pc is not None)
    bodies = [a.body for a in n.arms]
    match n.op.shape:
        case Shape.IF:
            return bool(n.arms) and _ends(n.head, fresh) and _ends(bodies[0], fresh)
        case Shape.SWITCH:
            return any(a.sub == 2 for a in n.arms) and all(_ends(b, fresh) for b in bodies)
        case Shape.REPEAT:
            return _ends(n.head, fresh)
    return bool(bodies) and all(_ends(b, fresh) for b in bodies)


def _ends(nodes: Sequence[ir.Node], fresh: bool = True) -> bool:
    return any(is_terminal(n, fresh) for n in nodes)


def _leaks(nodes: Sequence[ir.Node], owner: ir.Located | None) -> list[ir.Located | None]:
    """Where a list can run off its end, each as the node it runs off: the last instruction, the
    block whose condition or count lets it through, the arm with nothing in it. None for a
    routine with no body."""
    if _ends(nodes):
        return []
    if not nodes:
        return [owner]
    last = nodes[-1]
    if isinstance(last, ir.Insn):
        return [last]
    out: list[ir.Located | None] = []
    shape, arms = last.op.shape, last.arms
    if shape is Shape.IF:
        out += _leaks(last.head, last)
        out += _leaks(arms[0].body, arms[0]) if arms else [last]
    elif shape is Shape.REPEAT:
        out.append(last)
    else:
        for a in arms:
            out += _leaks(a.body, a)
        if not arms or (shape is Shape.SWITCH and not any(a.sub == 2 for a in arms)):
            out.append(last)
    return out


def _tokens(nodes: Sequence[ir.Node], out: list[Token], ids: list[str]) -> None:
    """The tree flattened, with every block's end marked, ignoring ids and addresses."""
    for n in nodes:
        if isinstance(n, ir.Insn):
            out.append(("insn", n.op.code, tuple(sorted(n.args.items()))))
            ids.append(n.id)
            continue
        out.append(("block", n.op.code, tuple(sorted(n.args.items()))))
        ids.append(n.id)
        _tokens(n.head, out, ids)
        for a in n.arms:
            out.append(("arm", a.sub, tuple(sorted(a.args.items()))))
            ids.append(a.id)
            _tokens(a.body, out, ids)
        out.append(("end",))
        ids.append(n.id)


def _text(tokens: Sequence[Token], k: int) -> str:
    """Token `k` in words."""
    match tokens[k] if k < len(tokens) else None:
        case ("insn" | "block", int(code), tuple(args)):
            return " ".join([OPCODES[code].name, *(str(v) for _, v in args)])
        case ("arm", int(sub), _):
            return f"arm {sub}"
        case ("end",):
            return "the end of a block"
    return "nothing"


def decoded(img: asm.Image, name: str) -> list[ir.Node]:
    """The tree the bytes of routine `name` decode to in an image, as `lift` reads it."""
    try:
        return routine(_reader(img), img.base + img.items[name][0])[0]
    except LiftError as e:
        raise LiftError(f"{name}: {e}") from e


def _image(c: _Checks, img: asm.Image) -> None:
    read = _reader(img)
    for r in c.p.routines():
        for b in (n for n in ir.walk(r.body) if isinstance(n, ir.Block) and id(n) in c.touched):
            if b.id not in img.nodes:
                continue
            found = walk.scan(read, img.base + img.nodes[b.id][0])
            if found.stop:
                text = f"{b.op.name}: the game's scan stops at a {found.stop}"
                c.add("error", f"SKIP_{found.stop.upper()}", text, b.id, r.name)
                continue
            want = [img.base + img.nodes[a.id][0] for a in b.arms] + [img.base + img.closes[b.id]]
            if found.markers != want:
                text = f"{b.op.name}: the game's scan finds markers elsewhere than the tree has"
                c.add("error", "SKIP_DISAGREES", text, b.id, r.name)
        _decode(c, img, r)


def _decode(c: _Checks, img: asm.Image, r: ir.Routine) -> None:
    try:
        got = decoded(img, r.name)
    except LiftError as e:
        c.add("error", "DECODE", str(e), None, r.name)
        return
    want: list[Token] = []
    ids: list[str] = []
    _tokens(r.body, want, ids)
    have: list[Token] = []
    _tokens(got, have, [])
    if want != have:
        pairs = enumerate(zip(want, have, strict=False))
        k = next((i for i, (a, b) in pairs if a != b), min(len(want), len(have)))
        text = f"{r.name} decodes to {_text(have, k)}, not {_text(want, k)}"
        c.add("error", "DECODE", text, ids[min(k, len(ids) - 1)] if ids else None, r.name)


def _dead(c: _Checks, r: ir.Routine, nodes: Sequence[ir.Node]) -> None:
    """DEAD_CODE: what follows an end or goto, or a block that every way through ends, when new
    code makes it end or is what follows. Native routines have their own (a defensive `end base`
    after gotos), which stay."""
    for n in nodes:
        if isinstance(n, ir.Block):
            _dead(c, r, n.head)
            for a in n.arms:
                _dead(c, r, a.body)
    for k, n in enumerate(nodes[:-1]):
        if is_terminal(n) and (not is_terminal(n, False) or _touched(nodes[k + 1 :], set())):
            text = f"{nodes[k + 1].op.name} follows an end or goto and never runs"
            c.add("warning", "DEAD_CODE", text, nodes[k + 1].id, r.name)
            return


def _structure(c: _Checks) -> None:
    for r in c.p.routines():
        _dead(c, r, r.body)
        if r.va is not None and not _touched(r.body, set()):
            continue
        for at in _leaks(r.body, None):
            text = f"{r.name} can run off its end: finish it with end or goto"
            c.add("error", "FALLS_OFF", text, at.id if at else None, r.name)


def _runs(c: _Checks, b: budget.Budget | None) -> None:
    """OPS_PER_RUN, RESTART_LOOP and CORRELATED_DRAW, from `budget`. Nothing new, nothing to say."""
    if all(n.pc is not None for n in c.p.nodes()):
        return
    b = budget.Budget(c.p) if b is None else b
    fresh = {n.id for n in c.p.nodes() if n.pc is None}
    index = ir.Index(c.p)
    seen: set[str | None] = set()
    for run in sorted(b.new.values(), key=lambda r: -r.count):
        if run.count <= budget.WARN:
            break
        first = next((i for i in run.path if i in fresh), None)
        if first in seen:
            continue
        seen.add(first)
        level = "error" if run.count > budget.LIMIT else "warning"
        what = "go round without yielding" if run.unbounded else f"dispatch {run.count}"
        text = f"a run from {run.entry} can {what}; the game stops a run at {budget.LIMIT}"
        item = index.routine(first).name if first else None
        c.add(level, "OPS_PER_RUN", text, first, item)
    for rs in b.restarts:
        text = "base[0] restarts with no yield" + (
            ": nothing on the path decides, so it spins to the run limit every time"
            if rs.forced
            else " when the conditions on the path hold"
        )
        c.add("error" if rs.forced else "warning", "RESTART_LOOP", text, rs.node, None)
    for d in b.draws:
        text = "this random follows another draw in the run, and a draw steps by 1: its odds follow"
        c.add("warning", "CORRELATED_DRAW", text, d.node, index.routine(d.node).name)


def check(
    p: ir.Program, img: asm.Image | None = None, runs: budget.Budget | None = None
) -> list[Finding]:
    """What is wrong with the new code of a resolved program. `img` is the program assembled,
    by default at its own base; asm refuses what it cannot encode. `runs` is `p`'s budget, when
    the caller keeps one."""
    c = _Checks(p)
    out = c.run()
    _structure(c)
    if not any(f.code == "UNRESOLVED" for f in out):
        _image(c, asm.assemble(p) if img is None else img)
        _runs(c, runs)
    return out
