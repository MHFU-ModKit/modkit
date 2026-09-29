"""A big monster's move table, read from its overlay: the action tick's (main, sub) pairs, the
handler each pair runs, and the animation ids the handler hands ACTION_EXECUTOR.

    from mhfu.em.moveset import Moveset
    ms = Moveset(game.em(75))
    ms.tick, ms.tick_entry      # the Switch on ENTITY.MAIN_STATE and its function, or None
    ms.mains[1]                 # Main(main, dispatcher, switch): its switch on SUB_STATE
    ms.pairs[1, 4]              # Pair(main, sub, case, handler, args)
    ms.animations(ms.pairs[1, 4])   # Animations(ids, computed)

A handler is the behaviour channel's code; what it passes the executor is the animation
channel. `Walker` interprets a function path by path; `animations` and `mhfu.em.chain` run it.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from functools import cached_property

import rabbitizer
from rabbitizer import InstrId, Instruction

from .. import addresses as a
from ..mips import CALL_CLOBBERS, Call, Code, Fpr, Gpr, Register, Switch
from ..overlay import Overlay

CASE_SETUP = 4
"""Instructions from a case label to the call it makes."""
ARGS = ("a1", "a2", "a3", "t0")
"""The registers a dispatcher case passes literals in; main 3 of em75 takes a variant in t0."""
_MASK = 0xFFFFFFFF


def _signed(v: int) -> int:
    return v - (1 << 32) if v & 0x80000000 else v


def loads(ins: Instruction | None, offset: int) -> bool:
    """`ins` is a load from `offset` off some base register."""
    return ins is not None and ins.doesLoad() and ins.getProcessedImmediate() == offset


@dataclass(frozen=True)
class Main:
    """One case of the action tick: the function it calls and that function's sub switch."""

    main: int
    dispatcher: int
    switch: Switch | None
    """The switch on SUB_STATE; None where the dispatcher branches without a table."""


@dataclass(frozen=True)
class Pair:
    """A (main, sub) the tick can dispatch."""

    main: int
    sub: int
    case: int
    """The dispatcher's case label."""
    handler: int | None
    """The overlay function the case calls; None when the case runs inline."""
    args: Mapping[str, int] = field(default_factory=dict)
    """Literals the case passes, by register name (a1-a3, t0)."""


@dataclass(frozen=True)
class Animations:
    """The animation ids a handler passes ACTION_EXECUTOR."""

    ids: tuple[int, ...]
    computed: bool
    """Some executor call takes an id that is not a literal on its path."""


class Moveset:
    """The move table of one em overlay."""

    def __init__(self, ovl: Overlay, code: Code | None = None) -> None:
        self.ovl = ovl
        self.code = code or Code(ovl, ovl.text)

    @cached_property
    def _calls(self) -> dict[int, Call]:
        return {c.site: c for c in self.code.calls}

    def case_call(self, label: int) -> Call | None:
        """The first linking call within CASE_SETUP instructions of a case label."""
        for i in range(CASE_SETUP):
            c = self._calls.get(label + 4 * i)
            if c is not None and c.link:
                return c
        return None

    def switch_in(self, fn: int, offset: int | None = None) -> Switch | None:
        """The switch in the function holding `fn`: the one on a load from `offset` if there
        is one, else the first."""
        where = self.code.function(fn)
        found = sorted((s for s in self.code.switches.values() if s.jr in where), key=_jr)
        keyed = [s for s in found if offset is not None and loads(s.operand, offset)]
        return (keyed or found)[0] if found else None

    @cached_property
    def tick(self) -> Switch | None:
        """The switch on MAIN_STATE whose cases reach the most switches on SUB_STATE.

        Its operand can be unknown (a branch-likely reloads MAIN_STATE on one path), so a
        function that loads MAIN_STATE anywhere qualifies."""
        best, most = None, -1
        for s in sorted(self.code.switches.values(), key=_jr):
            if not loads(s.operand, a.ENTITY.MAIN_STATE) and not (
                s.operand is None
                and any(
                    loads(i, a.ENTITY.MAIN_STATE) for i in self.code.span(self.code.function(s.jr))
                )
            ):
                continue
            fanout = sum(1 for m in self._mains(s).values() if m.switch is not None)
            if fanout > most:
                best, most = s, fanout
        return best

    @property
    def tick_entry(self) -> int | None:
        """The function holding the tick's switch."""
        return None if self.tick is None else self.code.function(self.tick.jr).start

    def _mains(self, tick: Switch) -> dict[int, Main]:
        out = {}
        for i, label in enumerate(tick.targets):
            c = self.case_call(label)
            if c is None or c.target is None:
                continue
            sub = (
                self.switch_in(c.target, a.ENTITY.SUB_STATE) if c.target in self.code.text else None
            )
            out[i + tick.first] = Main(i + tick.first, c.target, sub)
        return out

    @cached_property
    def mains(self) -> dict[int, Main]:
        return {} if self.tick is None else self._mains(self.tick)

    @cached_property
    def pairs(self) -> dict[tuple[int, int], Pair]:
        out = {}
        for m in self.mains.values():
            if m.switch is None:
                continue
            for i, label in enumerate(m.switch.targets):
                sub = i + m.switch.first
                c = self.case_call(label)
                handler = c.target if c is not None and c.target in self.code.text else None
                args = {}
                if c is not None:
                    for name in ARGS:
                        v = self.code.constant(c.site, getattr(Gpr, name))
                        if v is not None:
                            args[name] = _signed(v)
                out[m.main, sub] = Pair(m.main, sub, label, handler, args)
        return out

    def animations(self, pair: Pair) -> Animations:
        """Every path through the pair's handler and the functions it calls, with the pair's
        own MAIN_STATE, SUB_STATE and case arguments; the species byte forks."""
        if pair.handler is None:
            return Animations((), False)
        sites = self._anim_walker.run(pair.handler, seed_of(pair.args), pair_facts(pair))
        ids = {s.args[0] for s in sites if s.args[0] is not None}
        return Animations(tuple(sorted(ids)), any(s.args[0] is None for s in sites))

    @cached_property
    def _anim_walker(self) -> Walker:
        return Walker(self.code, {a.ACTION_EXECUTOR: "anim"}, depth=1, guards=False)


def _jr(s: Switch) -> int:
    return s.jr


def seed_of(args: Mapping[str, int]) -> dict[Register, Value]:
    """A case's literal arguments as a walker seed."""
    return {getattr(Gpr, name): v & _MASK for name, v in args.items()}


def pair_facts(pair: Pair) -> dict[Cell, Known]:
    """The pair's own MAIN_STATE and SUB_STATE: a handler shared by several subs reads them."""
    return known({a.ENTITY.MAIN_STATE: pair.main, a.ENTITY.SUB_STATE: pair.sub})


# --- the walker ---


@dataclass(frozen=True)
class Tag:
    """A value the walker knows by origin, not by number."""

    kind: str
    """entity, vtable, method (a vtable slot), cell (a load from the entity), result."""
    n: int | None = None
    """The slot, the cell's offset, or the callee a result came from."""
    frame: float | None = None
    """For a clip predicate's result, the frame it tested."""
    size: int = 0
    """For a cell, the load's width in bytes."""


Value = int | Tag
ENTITY = Tag("entity")
VTABLE = Tag("vtable")
Cell = tuple[int, int]
"""An entity cell: (offset, width in bytes)."""
Known = int | frozenset[int]
"""What a path knows of a cell: its value, or values it is not."""
Facts = Mapping[Cell, Known]
_FRAMED = frozenset([a.CLIP_CURSOR_CROSSES, a.CLIP_CURSOR_REACHED])
_WIDTH: dict[rabbitizer.Enum, int] = {
    InstrId.cpu_lb: 1,
    InstrId.cpu_lbu: 1,
    InstrId.cpu_sb: 1,
    InstrId.cpu_lh: 2,
    InstrId.cpu_lhu: 2,
    InstrId.cpu_sh: 2,
    InstrId.cpu_lw: 4,
    InstrId.cpu_sw: 4,
}


def known(cells: Mapping[int, int]) -> dict[Cell, Known]:
    """Byte cells with a fixed value, as facts."""
    return {(off, 1): v & 0xFF for off, v in cells.items()}


@dataclass(frozen=True)
class Site:
    """A watched call one path reaches."""

    site: int
    kind: str
    args: tuple[int | None, ...]
    """The walker's `args` registers (a1, a2, a3 by default) when literal on that path."""
    guards: tuple[str, ...] = ()
    via: tuple[int, ...] = ()
    """The functions the walk descended into to reach it, outermost first."""
    facts: frozenset[tuple[Cell, Known]] = frozenset()
    """What the path knows of entity cells at the call."""


@dataclass(frozen=True)
class _Operand:
    """Where the walker keeps the value a switch's operand loaded."""

    jr: int


_Env = dict["Register | _Operand", Value]
_PASSED = (Gpr.a0, Gpr.a1, Gpr.a2, Gpr.a3, Fpr.fa0)
"""What a descended-into callee is seeded with."""


@dataclass(frozen=True)
class _Fork:
    """A conditional branch as the walker sees it."""

    outcome: bool | None = None
    """Taken or not, when the operands are known."""
    taken: tuple[str, ...] = ()
    fell: tuple[str, ...] = ()
    cell: Cell | None = None
    """The cell compared with `value`: taken learns it equal when `equal`, else unequal."""
    value: int = 0
    equal: bool = True


@dataclass
class _Path:
    pc: int
    env: _Env
    facts: dict[Cell, Known]
    guards: tuple[str, ...] = ()
    visited: frozenset[int] = frozenset()
    steps: int = 0

    def fork(self, pc: int, guards: tuple[str, ...] = ()) -> _Path:
        return _Path(
            pc, dict(self.env), dict(self.facts), self.guards + guards, self.visited, self.steps
        )

    def learn(self, cell: Cell, value: int, equal: bool) -> None:
        if equal:
            self.facts[cell] = value
        else:
            old = self.facts.get(cell)
            self.facts[cell] = (old if isinstance(old, frozenset) else frozenset()) | {value}


def _bits(value: int) -> float:
    return float(struct.unpack("<f", struct.pack("<I", value & _MASK))[0])


class Walker:
    """A bounded path walk of one function that interprets what it can.

    It knows literals, the entity pointer (a0 on entry), its vtable and methods, loads from the
    entity and what calls return. A path learns entity cells from its stores and branches and
    keeps them across calls: a callee is taken not to change a cell its caller tested. A
    branch or switch on a value it cannot evaluate forks; with `guards`, a fork on a named
    subject records a guard on each side.

    It descends into calls to `follow` (default: any overlay function; a function's start
    stands for all of it) up to `depth` nested calls, a tail call not counting as one. It
    records every call to a `watch` target or a `methods` vtable slot, tail calls included,
    with the values of `args` there, and the value of a `probes` instruction's register as it
    runs (a Site of kind "probe"). A pc runs at most once per path; `max_paths` path ends and
    `max_steps` per path bound a walk, and `truncated` names the functions that hit one.
    """

    def __init__(
        self,
        code: Code,
        watch: Mapping[int, str],
        *,
        methods: Mapping[int, str] | None = None,
        follow: Collection[int] | None = None,
        depth: int = 1,
        facts: Facts | None = None,
        names: Mapping[int, str] | None = None,
        args: tuple[Register, ...] = (Gpr.a1, Gpr.a2, Gpr.a3),
        probes: Mapping[int, Register] | None = None,
        guards: bool = True,
        max_paths: int = 4000,
        max_steps: int = 700,
    ) -> None:
        self.code, self.watch = code, dict(watch)
        self.methods = dict(methods or {})
        self.follow = follow
        self.depth, self.facts, self.names = depth, dict(facts or {}), dict(names or {})
        self.args, self.probes = args, dict(probes or {})
        self._found: list[tuple[dict[Site, None], tuple[int, ...]]] = []
        self.guards, self.max_paths, self.max_steps = guards, max_paths, max_steps
        self.truncated: set[int] = set()
        self._memo: dict[tuple[object, ...], tuple[Site, ...]] = {}
        self._operands = {s.operand.vram: s.jr for s in code.switches.values() if s.operand}
        self._tables = {
            s.table + 4 * i: t for s in code.switches.values() for i, t in enumerate(s.targets)
        }

    def run(
        self, fn: int, seed: Mapping[Register, Value] | None = None, facts: Facts | None = None
    ) -> list[Site]:
        """Every watched call some path from `fn` reaches, once per distinct Site."""
        env: _Env = {r: v for r, v in (seed or {}).items()}
        return list(self._run(fn, env, {**self.facts, **(facts or {})}, 0, ()))

    def _run(
        self, fn: int, seed: _Env, facts: dict[Cell, Known], depth: int, via: tuple[int, ...]
    ) -> tuple[Site, ...]:
        key = (fn, frozenset(seed.items()), frozenset(facts.items()), depth, via)
        if key not in self._memo:
            start = _Path(fn, {Gpr.a0: ENTITY, **seed}, facts)
            self._memo[key] = self._walk(start, depth, via)
        return self._memo[key]

    def _walk(self, start: _Path, depth: int, via: tuple[int, ...]) -> tuple[Site, ...]:
        code, fn = self.code, start.pc
        out: dict[Site, None] = {}
        self._found.append((out, via))
        states: set[tuple[object, ...]] = set()
        work, ends = [start], 0
        while work:
            if ends >= self.max_paths:
                self.truncated.add(fn)
                break
            p = work.pop()
            while True:
                if p.pc not in code.text or p.pc in p.visited or p.steps > self.max_steps:
                    if p.steps > self.max_steps:
                        self.truncated.add(fn)
                    ends += 1
                    break
                state = (
                    p.pc,
                    frozenset(p.env.items()),
                    frozenset(p.facts.items()),
                    p.guards if self.guards else (),
                )
                if state in states:
                    ends += 1
                    break
                states.add(state)
                p.visited, p.steps = p.visited | {p.pc}, p.steps + 1
                ins = code.at(p.pc)
                if not ins.hasDelaySlot():
                    self._exec(ins, p)
                    p.pc += 4
                    continue
                slot = code.at(p.pc + 4) if p.pc + 4 in code.text else None
                if ins.isBranch():
                    self._branch(ins, slot, p, work)
                    continue
                if ins.isReturn():
                    ends += 1
                    break
                sw = code.switch(p.pc)
                if sw is not None:
                    if self._switch(sw, ins, slot, p, work):
                        continue
                    ends += 1
                    break
                jump = ins.getInstrIndexAsVram() if ins.isJumpWithAddress() else None
                if (
                    jump is not None
                    and ins.uniqueId == InstrId.cpu_j
                    and jump in code.function(p.pc)
                ):
                    self._exec(slot, p)
                    p.pc = jump
                    continue
                target: Value | None = p.env.get(ins.rs) if jump is None else jump
                self._exec(slot, p)
                for s in self._call(target, p, depth, via, ins.doesLink()):
                    out.setdefault(s)
                if not ins.doesLink():
                    ends += 1
                    break
                self._after_call(target, p)
                p.pc += 8
        self._found.pop()
        return tuple(out)

    def _branch(
        self, ins: Instruction, slot: Instruction | None, p: _Path, work: list[_Path]
    ) -> None:
        """Step `p` past a branch, queueing the other side of a fork."""
        target = ins.getBranchVramGeneric()
        if ins.isUnconditionalBranch():
            self._exec(slot, p)
            p.pc = target
            return
        f = self._cond(ins, p)
        likely = ins.isBranchLikely()
        if f.outcome is not None:
            if f.outcome or not likely:
                self._exec(slot, p)
            p.pc = target if f.outcome else p.pc + 8
            return
        taken = p.fork(target, f.taken)
        if f.cell is not None:
            taken.learn(f.cell, f.value, f.equal)
            p.learn(f.cell, f.value, not f.equal)
        self._exec(slot, taken)
        work.append(taken)
        if not likely:
            self._exec(slot, p)
        p.guards += f.fell
        p.pc += 8

    def _switch(
        self, sw: Switch, ins: Instruction, slot: Instruction | None, p: _Path, work: list[_Path]
    ) -> bool:
        """Follow a jump table: to its case when the index is known, else fork every case.
        True when `p` goes on."""
        dest = p.env.get(ins.rs)
        raw = p.env.get(_Operand(sw.jr))
        self._exec(slot, p)
        if isinstance(raw, int) and 0 <= raw - sw.first < len(sw.targets):
            dest = sw.targets[raw - sw.first]
        if isinstance(dest, int) and dest in self.code.text:
            p.pc = dest
            return True
        by_label: dict[int, list[int]] = {}
        for i, label in enumerate(sw.targets):
            by_label.setdefault(label, []).append(i + sw.first)
        name = self._describe(raw)
        cell = _cell(raw)
        for label, values in by_label.items():
            guards: tuple[str, ...] = ()
            if name is not None and self.guards:
                one = len(values) == 1
                guards = (f"{name}=={values[0]}" if one else f"{name} in {set(values)}",)
            case = p.fork(label, guards)
            if cell is not None and len(values) == 1:
                case.learn(cell, values[0], True)
            work.append(case)
        return False

    def _call(
        self, target: Value | None, p: _Path, depth: int, via: tuple[int, ...], link: bool
    ) -> list[Site]:
        """The watched calls a call reaches; a tail call continues at the same depth."""
        kind = None
        if isinstance(target, int):
            kind = self.watch.get(target)
        elif isinstance(target, Tag) and target.kind == "method" and target.n is not None:
            kind = self.methods.get(target.n)
        site = p.pc
        if kind is not None:
            args = tuple(v if isinstance(v := p.env.get(r), int) else None for r in self.args)
            return [Site(site, kind, args, p.guards, via, frozenset(p.facts.items()))]
        deeper = depth + link
        if not isinstance(target, int) or deeper > self.depth or target not in self.code.text:
            return []
        if target in via or (
            self.follow is not None and self.code.function(target).start not in self.follow
        ):
            return []
        seed: _Env = {r: p.env[r] for r in _PASSED if r in p.env}
        found = self._run(target, seed, dict(p.facts), deeper, (*via, target))
        return [Site(s.site, s.kind, s.args, p.guards + s.guards, s.via, s.facts) for s in found]

    def _after_call(self, target: Value | None, p: _Path) -> None:
        frame = None
        if target in _FRAMED:
            bits = p.env.get(Fpr.fa0)
            frame = round(_bits(bits), 2) if isinstance(bits, int) else None
        for r in CALL_CLOBBERS:
            p.env.pop(r, None)
        p.env[Gpr.v0] = Tag("result", target if isinstance(target, int) else None, frame)

    # --- conditions ---

    def _cond(self, ins: Instruction, p: _Path) -> _Fork:
        u = ins.uniqueId
        if u in _TWO:
            x = _value(p.env, ins.rs)
            y = 0 if u in (InstrId.cpu_beqz, InstrId.cpu_bnez) else _value(p.env, ins.rt)
            eq = _TWO[u]
            if isinstance(x, int) and isinstance(y, int):
                return _Fork((x == y) == eq)
            subject, lit = (x, y) if isinstance(x, Tag) else (y, x)
            if not isinstance(lit, int):
                return _Fork()
            cell = _cell(subject)
            if cell is not None and lit in _excluded(p.facts.get(cell)):
                return _Fork(not eq)
            name = self._describe(subject) if self.guards else None
            yes: tuple[str, ...] = ()
            no: tuple[str, ...] = ()
            if name is None:
                pass
            elif isinstance(subject, Tag) and subject.kind == "result" and lit in (0, 1):
                yes, no = ((f"!{name}",), (name,)) if lit == 0 else ((name,), (f"!{name}",))
            else:
                yes, no = (f"{name}=={_signed(lit)}",), (f"{name}!={_signed(lit)}",)
            taken, fell = (yes, no) if eq else (no, yes)
            return _Fork(None, taken, fell, cell, lit & _MASK, eq)
        if u in _ONE:
            x = _value(p.env, ins.rs)
            op, when, otherwise = _ONE[u]
            if isinstance(x, int):
                return _Fork(op(_signed(x)))
            name = self._describe(x) if self.guards else None
            if name is None:
                return _Fork()
            return _Fork(None, (name + when,), (name + otherwise,))
        return _Fork()

    def _describe(self, v: Value | None) -> str | None:
        """A guard's subject: an entity cell or a call's result."""
        if not isinstance(v, Tag) or v.n is None:
            return None
        if v.kind == "cell":
            return _FIELD_NAMES.get(v.n, f"+0x{v.n:X}")
        if v.kind == "result":
            name = self.names.get(v.n)
            if name is None:
                return f"0x{v.n:08X}()"
            return name.format(frame="?" if v.frame is None else f"{v.frame:g}")
        return None

    # --- data flow ---

    def _exec(self, ins: Instruction | None, p: _Path) -> None:
        """Interpret one non-control instruction into the path."""
        if ins is None or ins.isNop():
            return
        if ins.vram in self.probes:
            v = _value(p.env, self.probes[ins.vram])
            out, via = self._found[-1]
            out.setdefault(
                Site(ins.vram, "probe", (v if isinstance(v, int) else None,), p.guards, via)
            )
        u = ins.uniqueId
        if ins.doesStore():
            self._store(ins, u, p)
            return
        value = self._eval(ins, u, p)
        if ins.vram in self._operands:
            key = _Operand(self._operands[ins.vram])
            p.env.pop(key, None)
            if value is not None:
                p.env[key] = value
        dests = _dests(ins)
        for d in dests:
            p.env.pop(d, None)
        if value is not None and dests:
            p.env[dests[0]] = value

    def _store(self, ins: Instruction, u: rabbitizer.Enum, p: _Path) -> None:
        if _value(p.env, ins.rs) != ENTITY or u not in _WIDTH:
            return
        off, width = ins.getProcessedImmediate(), _WIDTH[u]
        for cell in [c for c in p.facts if c[0] < off + width and off < c[0] + c[1]]:
            del p.facts[cell]
        value = _value(p.env, ins.rt)
        if isinstance(value, int):
            p.facts[off, width] = value & ((1 << 8 * width) - 1)

    def _eval(self, ins: Instruction, u: rabbitizer.Enum, p: _Path) -> Value | None:
        env = p.env
        if ins.doesLoad():
            base = _value(env, ins.rs)
            off = ins.getProcessedImmediate()
            if base == ENTITY:
                if u == InstrId.cpu_lw and off == 0:
                    return VTABLE
                width = _WIDTH.get(u, 0)
                fact = p.facts.get((off, width))
                if isinstance(fact, int):
                    signed = u in (InstrId.cpu_lb, InstrId.cpu_lh)
                    return _extend(fact, width) & _MASK if signed else fact
                return Tag("cell", off, size=width)
            if base == VTABLE and u == InstrId.cpu_lw:
                return Tag("method", off)
            if isinstance(base, int) and u == InstrId.cpu_lw:
                return self._tables.get((base + off) & _MASK)
            return None
        if u == InstrId.cpu_lui:
            return (ins.getProcessedImmediate() << 16) & _MASK
        if u in _IMM:
            x = _value(env, ins.rs)
            imm = ins.getProcessedImmediate()
            if u == InstrId.cpu_andi and isinstance(x, Tag) and imm in (0xFF, 0xFFFF):
                return x
            return _IMM[u](x, imm) if isinstance(x, int) else None
        if u in _REG:
            x, y = _value(env, ins.rs), _value(env, ins.rt)
            if u in (InstrId.cpu_addu, InstrId.cpu_or) and (x == 0 or y == 0):
                return y if x == 0 else x
            return _REG[u](x, y) & _MASK if isinstance(x, int) and isinstance(y, int) else None
        if u in _SHIFT:
            x = _value(env, ins.rt)
            return _SHIFT[u](x, ins.sa) & _MASK if isinstance(x, int) else None
        if u in _EXTEND:
            x = _value(env, ins.rt)
            return _extend(x, _EXTEND[u]) & _MASK if isinstance(x, int) else x
        if u == InstrId.cpu_move:
            return _value(env, ins.rs)
        if u == InstrId.cpu_mtc1:
            x = _value(env, ins.rt)
            return x if isinstance(x, int) else None
        if u == InstrId.cpu_mfc1:
            x = env.get(ins.fs)
            return x if isinstance(x, int) else None
        if u == InstrId.cpu_mov_s:
            return env.get(ins.fs)
        return None


def _cell(v: Value | None) -> Cell | None:
    if isinstance(v, Tag) and v.kind == "cell" and v.n is not None and v.size:
        return (v.n, v.size)
    return None


def _excluded(fact: Known | None) -> frozenset[int]:
    return fact if isinstance(fact, frozenset) else frozenset()


def _value(env: _Env, reg: Register) -> Value | None:
    return 0 if reg == Gpr.zero else env.get(reg)


def _extend(x: int, width: int) -> int:
    bits = 8 * width
    x &= (1 << bits) - 1
    return x - (1 << bits) if x >> (bits - 1) else x


def _dests(ins: Instruction) -> list[Register]:
    """The registers `ins` writes, the main one first."""
    out = []
    gpr = ins.getDestinationGpr()
    if gpr is not None and gpr != Gpr.zero:
        out.append(gpr)
    if ins.modifiesFd():
        out.append(ins.fd)
    if ins.modifiesFt():
        out.append(ins.ft)
    if ins.modifiesFs():
        out.append(ins.fs)
    return out


def _slt(x: int, y: int) -> int:
    return int(_signed(x & _MASK) < _signed(y & _MASK))


def _sltu(x: int, y: int) -> int:
    return int((x & _MASK) < (y & _MASK))


_Op2 = Callable[[int, int], int]
_IMM: dict[rabbitizer.Enum, _Op2] = {
    InstrId.cpu_addiu: lambda x, i: (x + i) & _MASK,
    InstrId.cpu_addi: lambda x, i: (x + i) & _MASK,
    InstrId.cpu_andi: lambda x, i: x & i,
    InstrId.cpu_ori: lambda x, i: x | i,
    InstrId.cpu_xori: lambda x, i: x ^ i,
    InstrId.cpu_slti: _slt,
    InstrId.cpu_sltiu: _sltu,
}
_REG: dict[rabbitizer.Enum, _Op2] = {
    InstrId.cpu_addu: lambda x, y: x + y,
    InstrId.cpu_add: lambda x, y: x + y,
    InstrId.cpu_subu: lambda x, y: x - y,
    InstrId.cpu_sub: lambda x, y: x - y,
    InstrId.cpu_and: lambda x, y: x & y,
    InstrId.cpu_or: lambda x, y: x | y,
    InstrId.cpu_xor: lambda x, y: x ^ y,
    InstrId.cpu_nor: lambda x, y: ~(x | y),
    InstrId.cpu_slt: _slt,
    InstrId.cpu_sltu: _sltu,
}
_SHIFT: dict[rabbitizer.Enum, _Op2] = {
    InstrId.cpu_sll: lambda x, s: x << s,
    InstrId.cpu_srl: lambda x, s: (x & _MASK) >> s,
    InstrId.cpu_sra: lambda x, s: _signed(x & _MASK) >> s,
}
_EXTEND: dict[rabbitizer.Enum, int] = {InstrId.r4000allegrex_seb: 1, InstrId.r4000allegrex_seh: 2}
_TWO: dict[rabbitizer.Enum, bool] = {
    InstrId.cpu_beq: True,
    InstrId.cpu_beql: True,
    InstrId.cpu_beqz: True,
    InstrId.cpu_bne: False,
    InstrId.cpu_bnel: False,
    InstrId.cpu_bnez: False,
}
"""Branches on (in)equality of rs and rt: True when they branch on equal."""
_ONE: dict[rabbitizer.Enum, tuple[Callable[[int], bool], str, str]] = {
    InstrId.cpu_blez: (lambda x: x <= 0, "<=0", ">0"),
    InstrId.cpu_blezl: (lambda x: x <= 0, "<=0", ">0"),
    InstrId.cpu_bgtz: (lambda x: x > 0, ">0", "<=0"),
    InstrId.cpu_bgtzl: (lambda x: x > 0, ">0", "<=0"),
    InstrId.cpu_bltz: (lambda x: x < 0, "<0", ">=0"),
    InstrId.cpu_bltzl: (lambda x: x < 0, "<0", ">=0"),
    InstrId.cpu_bgez: (lambda x: x >= 0, ">=0", "<0"),
    InstrId.cpu_bgezl: (lambda x: x >= 0, ">=0", "<0"),
}
"""Branches comparing rs with zero: (outcome, guard if taken, guard if not)."""
_FIELD_NAMES: dict[int, str] = {int(f): n.lower() for n, f in a.ENTITY.fields.items()}
"""Guard subjects by entity offset: the ENTITY field starting there."""
