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
from dataclasses import dataclass

from rabbitizer import InstrId

from .. import addresses as a
from ..mips import Code, Fpr

_HALF_OR_WORD = frozenset([InstrId.cpu_lh, InstrId.cpu_lhu, InstrId.cpu_lw])


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


def _frame(code: Code, site: int) -> float | None:
    bits = code.constant(site, Fpr.fa0)
    if bits is None:
        return None
    return round(float(struct.unpack("<f", struct.pack("<I", bits))[0]), 2)


def gates(code: Code, handler: int) -> Gates:
    """The gates in the function at `handler`, not in the functions it calls."""
    reached: list[float | None] = []
    crosses: list[float | None] = []
    clip = budget = 0
    stores = []
    for ins in code.span(code.function(handler)):
        u = ins.uniqueId
        if u == InstrId.cpu_jal:
            target = ins.getInstrIndexAsVram()
            if target == a.CLIP_CURSOR_REACHED:
                reached.append(_frame(code, ins.vram))
            elif target == a.CLIP_CURSOR_CROSSES:
                crosses.append(_frame(code, ins.vram))
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


def budget_seeds(code: Code, handler: int) -> tuple[int, ...]:
    """The literals the handler's phase-0 block stores in ENTITY.ACTION_BUDGET.

    That block runs on the first tick after enter-action: from the first store to ENTITY.PHASE
    to the first unconditional branch, its delay slot included. A seed there overwrites a
    budget a post-hook on enter-action wrote; seeds elsewhere are for a later phase or for the
    action the handler chains into."""
    seeds = set()
    block = False
    body = code.span(code.function(handler))
    for i, ins in enumerate(body):
        u = ins.uniqueId
        if not block:
            block = u == InstrId.cpu_sb and ins.getProcessedImmediate() == a.ENTITY.PHASE
            continue
        if u == InstrId.cpu_sw and ins.getProcessedImmediate() == a.ENTITY.ACTION_BUDGET:
            v = code.constant(ins.vram, ins.rt)
            if v is not None:
                seeds.add(v)
        if body[i - 1].uniqueId == InstrId.cpu_b:
            break
    return tuple(sorted(seeds))
