# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""What ends a big monster's action: the gates its handler waits on.

    from mhfu.em import phases
    g = phases.gates(ms.code, pair.handler)
    g.reached, g.crosses        # frames it tests the clip cursor against (None: not literal)
    g.ends_on                   # "clip", "clip+cursor", "budget", "cursor" or "unknown"
    phases.budget_seeds(ms.code, pair.handler)  # what phase 0 stores in ENTITY.ACTION_BUDGET

A handler is a phase machine on ENTITY.PHASE. Its last phase usually waits for the clip to
stop (ENTITY.CLIP_FLAGS bit 0), so a clip of any length plays out, while the frames it tests
the cursor against are literals a ported clip has to honour. A minority count
ENTITY.ACTION_BUDGET down instead; a post-hook on enter-action owns that budget unless phase 0
re-seeds it.
"""

from __future__ import annotations

import struct
import weakref
from dataclasses import dataclass

from rabbitizer import InstrId, Instruction

from .. import addresses as a
from ..mips import Code, Fpr
from .moveset import Walker

_HALF_OR_WORD = frozenset([InstrId.cpu_lh, InstrId.cpu_lhu, InstrId.cpu_lw])
_PREDICATES = (a.CLIP_CURSOR_REACHED, a.CLIP_CURSOR_CROSSES)
_WALKERS: weakref.WeakKeyDictionary[Code, Walker] = weakref.WeakKeyDictionary()


@dataclass(frozen=True)
class Gates:
    """The gates one handler function tests."""

    reached: tuple[float | None, ...]
    """Frames passed to CLIP_CURSOR_REACHED, in code order."""
    crosses: tuple[float | None, ...]
    """Frames passed to CLIP_CURSOR_CROSSES, the shape of a hitbox-active test."""
    clip_done: int
    """Loads of ENTITY.CLIP_FLAGS: waits for the clip to stop."""
    budget: int
    """Loads of ENTITY.ACTION_BUDGET."""
    phase_stores: tuple[int, ...]
    """Sites that store ENTITY.PHASE."""

    @property
    def ends_on(self) -> str:
        """What ends the action; the clip wins, since no later phase runs before it stops."""
        if self.clip_done:
            return "clip+cursor" if self.reached or self.crosses else "clip"
        if self.budget:
            return "budget"
        return "cursor" if self.reached or self.crosses else "unknown"


def body(code: Code, handler: int) -> list[int]:
    """The instructions that run as the handler, delay slots included: control flow from its
    entry over switches and tail jumps (the compiler shares tails between functions), not
    into calls."""
    runs: set[int] = set()
    slots: set[int] = set()
    todo = [handler]
    while todo:
        pc = todo.pop()
        while pc in code.text and pc not in runs:
            runs.add(pc)
            ins = code.at(pc)
            if not ins.hasDelaySlot():
                pc += 4
                continue
            slots.add(pc + 4)
            if ins.isBranch():
                todo.append(ins.getBranchVramGeneric())
                if ins.isUnconditionalBranch():
                    break
            elif ins.isReturn():
                break
            elif (sw := code.switch(pc)) is not None:
                todo += sw.targets
                break
            elif ins.uniqueId == InstrId.cpu_j:
                todo.append(ins.getInstrIndexAsVram())
                break
            elif not ins.doesLink():
                break
            pc += 8
    return sorted(va for va in runs | slots if va in code.text)


def gates(code: Code, handler: int) -> Gates:
    """The gates in the handler's body, not in the functions it calls.

    A frame is the literal $f12 holds at the predicate call on every path to it, else None."""
    reached: list[float | None] = []
    crosses: list[float | None] = []
    clip = budget = 0
    stores = []
    frames = _frames(code, handler)
    for ins in map(code.at, body(code, handler)):
        u = ins.uniqueId
        if u == InstrId.cpu_jal:
            target = ins.getInstrIndexAsVram()
            if target == a.CLIP_CURSOR_REACHED:
                reached.append(frames.get(ins.vram))
            elif target == a.CLIP_CURSOR_CROSSES:
                crosses.append(frames.get(ins.vram))
            continue
        if not (ins.doesLoad() or ins.doesStore()):
            continue
        off = ins.getProcessedImmediate()
        if u in _HALF_OR_WORD and off == a.ENTITY.CLIP_FLAGS:
            clip += 1
        elif u == InstrId.cpu_lw and off == a.ENTITY.ACTION_BUDGET:
            budget += 1
        elif u == InstrId.cpu_sb and off == a.ENTITY.PHASE:
            stores.append(ins.vram)
    return Gates(tuple(reached), tuple(crosses), clip, budget, tuple(stores))


def _frames(code: Code, handler: int) -> dict[int, float]:
    """{predicate call: frame} where every path agrees on a literal."""
    seen: dict[int, set[int | None]] = {}
    for s in _frame_walker(code).run(handler):
        seen.setdefault(s.site, set()).add(s.args[0])
    out = {}
    for site, values in seen.items():
        bits = values.pop() if len(values) == 1 else None
        if bits is not None:
            out[site] = round(float(struct.unpack("<f", struct.pack("<I", bits))[0]), 2)
    return out


def _frame_walker(code: Code) -> Walker:
    """At depth 0 a walk follows tail calls only: the handler's body."""
    if code not in _WALKERS:
        watch = dict.fromkeys(_PREDICATES, "frame")
        _WALKERS[code] = Walker(code, watch, depth=0, guards=False, args=(Fpr.fa0,))
    return _WALKERS[code]


def budget_seeds(code: Code, handler: int) -> tuple[int, ...]:
    """The literals the handler's phase-0 block stores in ENTITY.ACTION_BUDGET.

    That block runs on the first tick after enter-action: from the first store to ENTITY.PHASE
    to the first unconditional branch, its delay slot included. A seed there overwrites a
    budget a post-hook on enter-action wrote; seeds elsewhere are for a later phase or for the
    action the handler chains into. A store's value is read on every path to it."""
    block: list[Instruction] = []
    for ins in code.span(code.function(handler)):
        if block or (ins.uniqueId == InstrId.cpu_sb and _imm(ins) == a.ENTITY.PHASE):
            block.append(ins)
        if len(block) > 1 and block[-2].uniqueId == InstrId.cpu_b:
            break
    stores = {
        ins.vram: ins.rt
        for ins in block[1:]
        if ins.uniqueId == InstrId.cpu_sw and _imm(ins) == a.ENTITY.ACTION_BUDGET
    }
    if not stores:
        return ()
    walker = Walker(code, {}, depth=0, guards=False, probes=stores)
    return tuple(sorted({s.args[0] for s in walker.run(handler) if s.args[0] is not None}))


def _imm(ins: Instruction) -> int:
    return int(ins.getProcessedImmediate())
