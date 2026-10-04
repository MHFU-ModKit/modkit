# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The effects a big-monster overlay spawns. Effects are code, not data: a handler calls a
game_task spawn with literal arguments, so each call site gives an effect id, a bone and, for
the framed spawn, a frame.

Effect ids index one library shared by all species (EFFECT_SPAWN offsets them only for a few
subspecies, `bias`), but a bone is the species' own joint index.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from rabbitizer import InstrId

from .. import addresses as a
from ..mips import Code, Gpr, Register


@dataclass(frozen=True)
class Entry:
    """A spawn function and the registers its caller passes id, bone and frame in."""

    via: str
    id: Register
    bone: Register
    frame: Register | None = None


ENTRIES = {
    a.EFFECT_SPAWN: Entry("biased", Gpr.a1, Gpr.t0),
    a.EFFECT_SPAWN_FRAMED: Entry("framed", Gpr.a3, Gpr.t0, Gpr.a1),
    a.EFFECT_SPAWN_AT: Entry("positional", Gpr.a1, Gpr.a2),
}
"""game_task's spawn entries; EFFECT_SPAWN_AT's bone is a base the call offsets."""
LOCAL = Entry("local", Gpr.a1, Gpr.a2)
"""An overlay's own `spawn_effect(entity, id, bone)` forwarding to EFFECT_SPAWN."""
WRAPPER_SIZE = 60
"""A function calling EFFECT_SPAWN that returns within this many instructions forwards its
caller's arguments; a longer one is a handler that computes them."""


@dataclass(frozen=True)
class Spawn:
    site: int
    target: int
    via: str
    """How: `biased`, `framed`, `positional` or `local` (through a wrapper)."""
    id: int | None
    """The literal effect id, None where the code computes it."""
    bone: int | None
    frame: int | None
    fn: int
    """The function holding the call."""


def _short(code: Code, start: int) -> bool:
    for n, ins in enumerate(code.span(range(start, start + 4 * WRAPPER_SIZE))):
        if ins.isReturn():
            return n + 2 <= WRAPPER_SIZE
    return False


def wrappers(code: Code) -> list[int]:
    """The overlay's own forwarders to EFFECT_SPAWN."""
    fns = {code.function(c.site).start for c in code.calls if c.target == a.EFFECT_SPAWN}
    return sorted(fn for fn in fns if _short(code, fn))


def literal(code: Code, site: int, reg: Register | None) -> int | None:
    """The signed literal `reg` holds at `site`, or None."""
    if reg is None:
        return None
    value = code.constant(site, reg)
    return None if value is None else value - (1 << 32) if value >> 31 else value


def spawns(code: Code) -> list[Spawn]:
    """Every call to a spawn entry or a wrapper, with its literal arguments."""
    entries = dict(ENTRIES) | dict.fromkeys(wrappers(code), LOCAL)
    out = []
    for c in code.calls:
        target = c.target
        if target is None or target not in entries:
            continue
        e = entries[target]
        out.append(
            Spawn(
                c.site,
                target,
                e.via,
                literal(code, c.site, e.id),
                literal(code, c.site, e.bone),
                literal(code, c.site, e.frame),
                code.function(c.site).start,
            )
        )
    return out


def census(by_species: dict[int, list[Spawn]]) -> dict[int, Counter[int]]:
    """Effect id -> {species: literal sites}: the ids several species share."""
    out: dict[int, Counter[int]] = defaultdict(Counter)
    for species, sites in by_species.items():
        for s in sites:
            if s.id is not None:
                out[s.id][species] += 1
    return dict(sorted(out.items()))


def bias(game_task: Code) -> dict[int, int]:
    """Entity species -> what EFFECT_SPAWN adds to its effect ids.

    The spawn compares the species byte against literals; the taken path adds the bias to the
    id, in a likely branch's delay slot or at the branch target."""
    out: dict[int, int] = {}
    for ins in game_task.span(game_task.function(a.EFFECT_SPAWN)):
        if not ins.isBranch() or ins.isUnconditionalBranch() or ins.rt == Gpr.zero:
            continue
        values = [game_task.constant(ins.vram, r) for r in (ins.rs, ins.rt)]
        if values.count(None) != 1:
            continue
        taken = ins.vram + 4 if ins.isBranchLikely() else ins.getBranchVramGeneric()
        add = game_task.at(taken)
        if add.uniqueId == InstrId.cpu_addiu and add.rs == add.rt != Gpr.zero:
            species = next(v for v in values if v is not None)
            out[species] = add.getProcessedImmediate()
    return out
