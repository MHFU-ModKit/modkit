"""MIPS as the PSP's Allegrex CPU runs it, decoded by rabbitizer, and the analyses the tools that
read the game's code share. Registers are rabbitizer enums (`Gpr.a1`, `Fpr.fa0`); read an
instruction through its fields (`uniqueId`, `rs`, `getProcessedImmediate()`), never its text.
`uniqueId` names pseudo-instructions (`cpu_b`, `cpu_beqz`, `cpu_nop`, `cpu_move`, `cpu_negu`),
so test a kind with `isBranch()`, `isUnconditionalBranch()` or `move_source()` where one exists.

    code = Code(eboot, eboot.text)          # any Memory and the range holding its code
    code.at(va)                             # the instruction at va
    code.function(va)                       # the range of the function holding va
    code.callers(target)                    # sites that jal or tail-jump to target
    [c for c in code.calls if c.slot == 0x88]       # virtual calls through vtable + 0x88
    [p for p in code.pairs if p.value == address]   # lui/lo pairs that form address
    code.switch(jr)                         # the jump table a `jr` dispatches through
    code.constant(call, Gpr.a1)             # the literal a1 the call passes, or None
    code.table(load)                        # the table `lw v0, 0(table + index)` reads
"""

from __future__ import annotations

import bisect
import struct
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import cached_property

import rabbitizer
from rabbitizer import InstrId, Instruction, OperandType, RegistersTracker

from .memory import Memory, Unmapped

ALLEGREX = rabbitizer.InstrCategory.R4000ALLEGREX
Gpr = rabbitizer.RegGprO32
Fpr = rabbitizer.RegCop1O32
Register = rabbitizer.Enum
"""A GPR (`Gpr.a1`) or an FPU register (`Fpr.fa0`, which is $f12)."""

_MASK = 0xFFFFFFFF
CALL_CLOBBERS = frozenset(
    [Gpr.at, Gpr.v0, Gpr.v1, Gpr.a0, Gpr.a1, Gpr.a2, Gpr.a3, Gpr.t8, Gpr.t9, Gpr.ra]
    + [getattr(Gpr, f"t{n}") for n in range(8)]
    + [r for r in vars(Fpr).values() if isinstance(r, Register) and r.value < 20]
)
"""What a call may change: the caller-saved GPRs and $f0-$f19."""


def decode(word: int, va: int) -> Instruction:
    return Instruction(word, vram=va, category=ALLEGREX)


def instructions(mem: Memory, start: int, stop: int) -> Iterator[Instruction]:
    """The instructions from `start` up to `stop`, one per word."""
    for i, (word,) in enumerate(struct.iter_unpack("<I", mem.read(start, stop - start))):
        yield decode(word, start + 4 * i)


def is_prologue(ins: Instruction) -> bool:
    """`addiu sp, sp, -N`: a stack frame opens."""
    return (
        ins.uniqueId == InstrId.cpu_addiu
        and ins.rt == Gpr.sp
        and ins.rs == Gpr.sp
        and ins.getProcessedImmediate() < 0
    )


def writes(ins: Instruction, reg: Register) -> bool:
    """Whether `ins` itself sets `reg` (a call's effect on the callee's registers aside)."""
    if reg == Gpr.ra and ins.doesLink():
        return True
    if ins.getDestinationGpr() == reg:
        return True
    return (
        (ins.modifiesFd() and ins.fd == reg)
        or (ins.modifiesFt() and ins.ft == reg)
        or (ins.modifiesFs() and ins.fs == reg)
    )


def move_source(ins: Instruction) -> Register | None:
    """The register a move copies: `move`, or `addu`/`or` with $zero; else None."""
    if ins.uniqueId == InstrId.cpu_move:
        return ins.rs
    if ins.uniqueId in (InstrId.cpu_addu, InstrId.cpu_or) and Gpr.zero in (ins.rs, ins.rt):
        return ins.rt if ins.rs == Gpr.zero else ins.rs
    return None


def _ends_flow(ins: Instruction) -> bool:
    """Control never falls through past this instruction's delay slot."""
    return ins.isUnconditionalBranch() or (ins.isJump() and not ins.doesLink())


def _bound_check(ins: Instruction, index: Register | None) -> bool:
    """`sltiu at, index, N`, the guard in front of a jump table."""
    return ins.uniqueId == InstrId.cpu_sltiu and (index is None or ins.rs == index)


@dataclass(frozen=True)
class Call:
    """A jal, jalr, or a tail call (j out of the function, or a jr that is not a switch)."""

    site: int
    target: int | None
    """The callee when known: the jump target, or a register holding a literal address."""
    slot: int | None = None
    """For a virtual call, `lw R, slot(vptr)` with vptr loaded from +0: the byte offset."""
    link: bool = True


@dataclass(frozen=True)
class Pair:
    """A `lui` and an instruction taking the low half from its register."""

    hi: int
    lo: int
    value: int


@dataclass(frozen=True)
class Switch:
    """A jump table and the `jr` that dispatches through it."""

    jr: int
    table: int
    targets: tuple[int, ...]
    first: int
    """The case value of `targets[0]` (the bias the code subtracts before the bound check)."""
    operand: Instruction | None
    """What computed the switched value, before the bias and any mask: often a load."""


class Code:
    """The instructions in `text` of `mem`, decoded once, with the analyses over them."""

    def __init__(self, mem: Memory, text: range) -> None:
        self.mem = mem
        self.text = text
        self._ins = list(instructions(mem, text.start, text.stop))
        self._busy: set[tuple[int, int]] = set()

    def __iter__(self) -> Iterator[Instruction]:
        return iter(self._ins)

    def at(self, va: int) -> Instruction:
        if va not in self.text or va % 4:
            raise Unmapped(f"0x{va:08X} is not an instruction in {self!r}")
        return self._ins[(va - self.text.start) // 4]

    def span(self, where: range) -> list[Instruction]:
        """The instructions in `where` (clipped to the code)."""
        start = max(where.start, self.text.start) - self.text.start
        return self._ins[start // 4 : (min(where.stop, self.text.stop) - self.text.start) // 4]

    def __repr__(self) -> str:
        return f"<Code 0x{self.text.start:08X}-0x{self.text.stop:08X}>"

    # --- functions ---

    @cached_property
    def _branch_targets(self) -> frozenset[int]:
        return frozenset(i.getBranchVramGeneric() for i in self._ins if i.isBranch())

    @cached_property
    def entries(self) -> tuple[int, ...]:
        """Function starts: jal targets, the run of code each stack-frame prologue opens, and
        tail-call targets: a `j` to a run no branch reaches, in another function or right after
        the `j` and any padding (a jump there calls the function the linker placed next).

        A prologue scheduled after a few instructions starts its function where that straight
        run begins; one reached by a branch is a second frame inside a function, not a start.
        """
        starts = {self.text.start}
        for ins in self._ins:
            if ins.uniqueId == InstrId.cpu_jal and ins.getInstrIndexAsVram() in self.text:
                starts.add(ins.getInstrIndexAsVram())
        for ins in self._ins:
            if is_prologue(ins):
                start = self._run_start(ins.vram)
                if start is not None:
                    starts.add(start)
        known = sorted(starts)
        for ins in self._ins:
            if ins.uniqueId != InstrId.cpu_j:
                continue
            t = ins.getInstrIndexAsVram()
            if t not in self.text or t in starts or self._run_start(t) != t:
                continue
            i = bisect.bisect_right(known, ins.vram)
            elsewhere = not known[i - 1] <= t < (known[i] if i < len(known) else self.text.stop)
            if elsewhere or all(x.isNop() for x in self.span(range(ins.vram + 8, t))):
                starts.add(t)
        return tuple(sorted(starts))

    def _run_start(self, va: int) -> int | None:
        """Where the straight run of code holding `va` starts, or None if a branch enters it."""
        a = va
        while a - 4 >= self.text.start and not (
            a - 8 >= self.text.start and _ends_flow(self.at(a - 8))
        ):
            if a in self._branch_targets:
                return None
            a -= 4
        while a < va and self.at(a).isNop():
            a += 4
        return None if a in self._branch_targets else a

    def function(self, va: int) -> range:
        """From the entry at or before `va` to the next entry."""
        self.at(va)
        i = bisect.bisect_right(self.entries, va)
        stop = self.entries[i] if i < len(self.entries) else self.text.stop
        return range(self.entries[i - 1], stop)

    @cached_property
    def functions(self) -> tuple[range, ...]:
        e = self.entries
        return tuple(range(a, b) for a, b in zip(e, (*e[1:], self.text.stop), strict=True))

    # --- lui/lo pairs and jump tables, as spimdisasm finds them ---

    @cached_property
    def _tracked(self) -> tuple[tuple[Pair, ...], dict[int, int]]:
        pairs: dict[tuple[int, int], int] = {}
        tables: dict[int, int] = {}
        for fn in self.functions:
            _Walk(self, fn, pairs, tables).run()
        found = (Pair(hi, lo, value) for (hi, lo), value in pairs.items())
        return tuple(sorted(found, key=lambda p: (p.lo, p.hi))), tables

    @property
    def pairs(self) -> tuple[Pair, ...]:
        """Every lui/lo pair, by rabbitizer's RegistersTracker; `ori` forms a constant."""
        return self._tracked[0]

    @cached_property
    def switches(self) -> dict[int, Switch]:
        """Every jump-table dispatch, by its `jr`."""
        out = {}
        for jr, table in self._tracked[1].items():
            out[jr] = self._switch(jr, table)
        return out

    def switch(self, jr: int) -> Switch | None:
        return self.switches.get(jr)

    def _switch(self, jr: int, table: int) -> Switch:
        """Without a bound check, the table runs while its entries stay in the function."""
        count, first, operand = self._bound(jr)
        fn, targets = self.function(jr), list[int]()
        while count is None or len(targets) < count:
            try:
                target = self.mem.u32(table + 4 * len(targets))
            except Unmapped:
                break
            if count is None and target not in fn:
                break
            targets.append(target)
        return Switch(jr, table, tuple(targets), first, operand)

    def _bound(self, jr: int) -> tuple[int | None, int, Instruction | None]:
        """(case count, first case, operand) from the `sltiu` that guards the table index:
        the one on the index that the dispatch shifts, else the nearest before the `jr`."""
        shift = self._shift(jr)
        check = None
        if shift is not None:
            index = shift.rt
            check = self._search(shift.vram, index, lambda i: _bound_check(i, index))
        if check is None:
            check = self._search(jr, None, lambda i: _bound_check(i, None))
        if check is None:
            return None, 0, None
        first, reg = 0, check.rs
        operand = self._unique(check.vram, reg, self._into_branches)
        while operand is not None:
            u = operand.uniqueId
            if u in (InstrId.cpu_addiu, InstrId.cpu_andi) and operand.rs != Gpr.zero:
                reg = operand.rs
                if u == InstrId.cpu_addiu:
                    first -= operand.getProcessedImmediate()
            elif (moved := move_source(operand)) is not None:
                reg = moved
            else:
                break
            operand = self._unique(operand.vram, reg, self._into_branches)
        return check.getProcessedImmediate(), first, operand

    def _shift(self, jr: int) -> Instruction | None:
        """The `sll t, index, 2` of the dispatch `lw t, 0(table + t); jr t`."""
        into = self._into_branches
        load = self._unique(jr, self.at(jr).rs, into)
        if load is None or not load.doesLoad():
            return None
        add = self._unique(load.vram, load.rs, into)
        if add is None or add.uniqueId != InstrId.cpu_addu:
            return None
        for reg in (add.rs, add.rt):
            shift = self._unique(add.vram, reg, into)
            if shift is not None and shift.uniqueId == InstrId.cpu_sll and shift.sa == 2:
                return shift
        return None

    def _search(
        self, site: int, reg: Register | None, match: Callable[[Instruction], bool]
    ) -> Instruction | None:
        """The first instruction satisfying `match` on some path back from `site`, on which
        nothing writes `reg` in between."""
        found, _ = self._back(site, reg, match, self._into_branches)
        return next((i for i in found if match(i)), None)

    # --- data flow backwards from a site ---

    @cached_property
    def _into_branches(self) -> dict[int, list[int]]:
        """Branch and jump sites by target."""
        out: dict[int, list[int]] = {}
        for ins in self._ins:
            if (ins.isBranch() or ins.isJumpWithAddress()) and not ins.doesLink():
                out.setdefault(ins.getBranchVramGeneric(), []).append(ins.vram)
        return out

    @cached_property
    def _into(self) -> dict[int, list[int]]:
        """Branch, jump and jump-table sites by target."""
        out = {t: list(sites) for t, sites in self._into_branches.items()}
        for s in self.switches.values():
            for t in dict.fromkeys(s.targets):
                out.setdefault(t, []).append(s.jr)
        return out

    @cached_property
    def labels(self) -> frozenset[int]:
        """Where control arrives other than by falling through: branch, jump and case targets
        and function entries."""
        return frozenset(self._into) & frozenset(self.text) | frozenset(self.entries)

    def _back(
        self,
        site: int,
        reg: Register | None,
        match: Callable[[Instruction], bool] | None,
        into: dict[int, list[int]],
    ) -> tuple[list[Instruction], bool]:
        """Every path back from `site` inside its function, to the first instruction on each
        that writes `reg` or satisfies `match`: those, and whether some path got to the
        function's entry without one. Past a call, the call sets what it may clobber."""

        def hit(ins: Instruction) -> bool:
            return (reg is not None and writes(ins, reg)) or (match is not None and match(ins))

        fn = self.function(site)
        if self.at(site).isJump() and site + 4 in fn and hit(self.at(site + 4)):
            return [self.at(site + 4)], False
        found: dict[int, Instruction] = {}
        open_ = False
        todo, seen = [site], {site}
        while todo:
            a = todo.pop()
            open_ |= a == fn.start
            paths = [([b + 4, b], b) for b in into.get(a, ()) if b in fn]
            owner = self.at(a - 8) if a - 8 >= fn.start else None
            if owner is None or not owner.hasDelaySlot():
                if a - 4 >= fn.start:
                    paths.append(([a - 4], a - 4))
            elif owner.doesLink() and reg in CALL_CLOBBERS:
                found[owner.vram] = owner
            elif not _ends_flow(owner):
                paths.append(([a - 8] if owner.isBranchLikely() else [a - 4, a - 8], a - 8))
            for ran, back in paths:
                for ins in map(self.at, ran):
                    if hit(ins):
                        found[ins.vram] = ins
                        break
                else:
                    if back not in seen:
                        seen.add(back)
                        todo.append(back)
        return list(found.values()), open_

    def _unique(self, site: int, reg: Register, into: dict[int, list[int]]) -> Instruction | None:
        found, open_ = self._back(site, reg, None, into)
        return found[0] if len(found) == 1 and not open_ else None

    def source(self, site: int, reg: Register) -> Instruction | None:
        """The instruction whose write to `reg` reaches `site` on every path inside its
        function; None where paths disagree or one starts at the function's entry. For a jump
        or call, as its target sees `reg`: after the delay slot. Past a call, the call itself
        sets what it may clobber."""
        return self._unique(site, reg, self._into)

    def table(self, site: int) -> int | None:
        """The literal address the load or store at `site` reads from, or for `off(table +
        index)` the table's: what `lw v0, 0(v0)` after `addu v0, table, index` indexes."""
        ins = self.at(site)
        offset = ins.getProcessedImmediate()
        base = self.constant(site, ins.rs)
        if base is None:
            add = self.source(site, ins.rs)
            if add is None or add.uniqueId != InstrId.cpu_addu:
                return None
            base = self.constant(add.vram, add.rs)
            if base is None:
                base = self.constant(add.vram, add.rt)
        return None if base is None else (base + offset) & _MASK

    def constant(self, site: int, reg: Register) -> int | None:
        """The literal `reg` holds at `site` on every path (the writes `source` would see),
        unsigned 32-bit, or None.

        Follows `lui`, `addiu`, `ori`, register moves and `mtc1`; an FPU register's value is
        its bits."""
        if reg == Gpr.zero:
            return 0
        if (site, reg.value) in self._busy:
            return None  # a loop feeding the register back into itself
        self._busy.add((site, reg.value))
        try:
            found, open_ = self._back(site, reg, None, self._into)
            values = {self._value(ins) for ins in found}
        finally:
            self._busy.discard((site, reg.value))
        return values.pop() if len(values) == 1 and not open_ else None

    def _value(self, ins: Instruction) -> int | None:
        u, at = ins.uniqueId, ins.vram
        if u == InstrId.cpu_lui:
            return (ins.getProcessedImmediate() << 16) & _MASK
        if u in (InstrId.cpu_addiu, InstrId.cpu_ori):
            base = self.constant(at, ins.rs)
            if base is None:
                return None
            imm = ins.getProcessedImmediate()
            return (base | imm) if u == InstrId.cpu_ori else (base + imm) & _MASK
        if (moved := move_source(ins)) is not None:
            return self.constant(at, moved)
        if u == InstrId.cpu_mtc1:
            return self.constant(at, ins.rt)
        return None

    # --- calls ---

    @cached_property
    def calls(self) -> tuple[Call, ...]:
        out = []
        for ins in self._ins:
            u, site = ins.uniqueId, ins.vram
            if u == InstrId.cpu_jal:
                out.append(Call(site, ins.getInstrIndexAsVram()))
            elif u == InstrId.cpu_j:
                target = ins.getInstrIndexAsVram()
                if target not in self.function(site):
                    out.append(Call(site, target, link=False))
            elif u == InstrId.cpu_jalr or (u == InstrId.cpu_jr and not ins.isReturn()):
                if site in self.switches:
                    continue
                reg = ins.rs
                out.append(
                    Call(site, self.constant(site, reg), self._slot(site, reg), u != InstrId.cpu_jr)
                )
        return tuple(out)

    def _slot(self, site: int, reg: Register) -> int | None:
        """`lw reg, slot(vptr)` with vptr from `lw vptr, 0(object)`, on every path."""
        loads, open_ = self._back(site, reg, None, self._into)
        if open_ or not loads or any(i.uniqueId != InstrId.cpu_lw for i in loads):
            return None
        slots = {i.getProcessedImmediate() for i in loads}
        if len(slots) != 1:
            return None
        for load in loads:
            vptrs, open_ = self._back(load.vram, load.rs, None, self._into)
            if (
                open_
                or not vptrs
                or any(v.uniqueId != InstrId.cpu_lw or v.getProcessedImmediate() for v in vptrs)
            ):
                return None
        return int(slots.pop())

    @cached_property
    def _callers(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = {}
        for c in self.calls:
            if c.target is not None:
                out.setdefault(c.target, []).append(c.site)
        return out

    def callers(self, target: int) -> list[int]:
        """Sites that call or tail-jump to `target`, directly or through a literal register."""
        return list(self._callers.get(target, ()))


@dataclass
class _Frame:
    tracker: RegistersTracker
    at: int
    likely: bool
    resumed: bool = False


class _Walk:
    """spimdisasm's per-function RegistersTracker walk: the straight line plus a look-ahead
    into every branch target, each with the registers as they were at the branch."""

    def __init__(
        self, code: Code, fn: range, pairs: dict[tuple[int, int], int], tables: dict[int, int]
    ) -> None:
        self.code, self.fn, self.pairs, self.tables = code, fn, pairs, tables
        self.luis: dict[int, Instruction] = {}
        self.taken: set[tuple[int, bool]] = set()

    def run(self) -> None:
        tracker = RegistersTracker()
        prev: Instruction | None = None
        for ins in self.code.span(self.fn):
            if prev is None or not (prev.isBranchLikely() or prev.isUnconditionalBranch()):
                self.process(tracker, ins, prev)
            if prev is not None:
                self.look_ahead(ins, prev, tracker, prev.isBranchLikely())
                if (
                    prev.isJumpWithAddress()
                    and not prev.doesLink()
                    and prev.getBranchVramGeneric() not in self.fn
                ):
                    tracker = RegistersTracker()
                tracker.unsetRegistersAfterFuncCall(ins, prev)
                if _ends_flow(prev) or prev.isReturn():
                    tracker = RegistersTracker()
            prev = ins

    def look_ahead(
        self, ins: Instruction, prev: Instruction, outer: RegistersTracker, likely: bool
    ) -> None:
        """Depth first, in spimdisasm's order; a stack instead of recursion, which runs past
        Python's limit in BOOT.BIN."""
        stack: list[_Frame] = []
        self.enter(stack, ins, prev, outer, likely)
        while stack:
            f = stack[-1]
            if f.resumed:
                came, landed = self.code.at(f.at - 4), self.code.at(f.at)
                if came.isUnconditionalBranch() or (came.isJump() and not came.doesLink()):
                    stack.pop()
                    continue
                f.tracker.unsetRegistersAfterFuncCall(landed, came)
                f.at += 4
                f.resumed = False
            if f.at >= self.fn.stop:
                stack.pop()
                continue
            first = f.at - 4 < self.code.text.start
            before, target = None if first else self.code.at(f.at - 4), self.code.at(f.at)
            self.process(f.tracker, target, before)
            if before is None:
                f.at += 4
                continue
            f.resumed = True
            self.enter(stack, target, before, f.tracker, f.likely or before.isBranchLikely())

    def enter(
        self,
        stack: list[_Frame],
        ins: Instruction,
        prev: Instruction,
        outer: RegistersTracker,
        likely: bool,
    ) -> None:
        if not (prev.isBranch() or prev.isUnconditionalBranch()):
            return
        target = prev.getBranchVramGeneric()
        if target < self.fn.start:
            return
        tracker = RegistersTracker(outer)
        self.process(tracker, ins, None)
        if (ins.vram, likely) in self.taken:
            return
        self.taken.add((ins.vram, likely))
        stack.append(_Frame(tracker, target, likely))

    def process(
        self, tracker: RegistersTracker, ins: Instruction, prev: Instruction | None
    ) -> None:
        va = ins.vram
        if ins.isBranch() or ins.isUnconditionalBranch():
            tracker.processBranch(ins, va)
        elif ins.isJumpWithAddress():
            pass
        elif ins.hasOperandAlias(OperandType.cpu_immediate):
            self.symbol(tracker, ins, prev, va)
        elif ins.isJumptableJump():
            jr = tracker.getJrRegData(ins)
            if jr.hasInfo() and not jr.checkedForBranching():
                self.tables.setdefault(va, jr.address())
        tracker.overwriteRegisters(ins, va)

    def symbol(
        self, tracker: RegistersTracker, ins: Instruction, prev: Instruction | None, va: int
    ) -> None:
        if ins.canBeHi():
            if prev is None:
                tracker.processLui(ins, va)
            else:
                tracker.processLui(ins, va, prev)
            self.luis[va] = ins
            return
        if not ins.canBeLo():
            return
        if ins.isUnsigned():
            hi = tracker.getLuiOffsetForConstant(ins)
            if hi is None or hi not in self.luis:
                return
            value = (self.luis[hi].getProcessedImmediate() << 16) | ins.getProcessedImmediate()
            self.pairs[(hi, va)] = value
            tracker.processConstant(ins, value, va)
            return
        info = tracker.preprocessLoAndGetInfo(ins, va)
        if not info.shouldProcess or info.isGpRel or info.isGpGot:
            return
        value = (info.value + ins.getProcessedImmediate()) & _MASK
        self.pairs[(info.instrOffset, va)] = value
        tracker.processLo(ins, value, va)
