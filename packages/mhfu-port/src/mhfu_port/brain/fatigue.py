# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The donor's fatigue as the framework's helper takes it: `mhfu.em_fatigue`'s table, read from
MHP3rd's files and mapped onto the host's pairs and the port's own moves.

    donor = read(p3rd, 23)                       # the species' row: parameters, costs per pair
    hosts = host_reactions(fu, 75)               # em75's reaction pairs by class
    fat, findings = table(manifest, data)        # `[brain] fatigue = true`
    fat.module()                                 # the moves module's `fatigue` table

MHP3rd spends stamina when a move ends, by the pair left, and when a reaction starts, by the
reaction's sub (three cost classes); at half while HP is at or below the species' low-HP percent.
At 0 the monster is tired for the row's frames. The helper charges when the monster enters a pair,
so each donor pair's cost lands on what `[brain.pairs]` plays in its place: a host pair, or an own
move (charged by its head, by name). The hub pair that carries own moves costs nothing.

em75's reaction pairs (the `EM75_REACTIONS` cases) fall in the same three classes: a knockout where
a trap's REACTION_CHECK code enters it (`TRAPS`), a fall where it plays `FALL_CLIPS` or more clips
(down, lying, up), else a flinch, which is also what a pair this cannot tell costs.
"""

from __future__ import annotations

import struct
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from mhfu import addresses
from mhfu.em.moveset import Moveset
from mhfu.em.script.check import Finding
from mhfu.files import Extracted
from mhfu.mips import Code, Gpr, Register
from rabbitizer import InstrId

from .. import moves
from ..data import Data
from ..manifest import Brain, Manifest, ManifestError
from . import p3
from .own import Report

Pair = tuple[int, int]
Entered = Mapping[int, frozenset[Pair]]

BLOCK = 0xA4
"""The species descriptor's pointer to its parameter block (the hit block's too)."""
FATIGUE_ROW, LOW_HP_ROW = 0x3C, 0x54
"""The block's pointers to the fatigue row and to the low-HP bytes."""
ROW_REFILLS, ROW_COSTS = 4, 8
"""The fatigue row is `{s16 tired frames, .., u32 start rows, u32 cost table}`."""
START = struct.Struct("<hHhHh")
"""A start row: a chance in percent and a stamina, twice, then the stamina `recover` adds. A roll
of 0..99 takes the first stamina below the first chance, else the second below the second."""
COST = struct.Struct("<BBh")
"""A cost row `{main, sub, stamina added}`, a negative amount a cost; the table ends at 0xFF."""
COST_END = 0xFF
LOW_HP_AT = 2
"""The byte of the low-HP row that is the percent."""
START_ROW = 0
"""Which of the start rows the spawn record picks; row 0 is the default."""
MAX_COSTS = 64
"""Rows read from a cost table before giving up on its end."""

STAMINA_RESET = 0x09CA30A4  # noaddr: (entity) stamina and its maximum to 1000, state 0
STAMINA_ADD = 0x09CA2F44  # noaddr: (entity, delta) the one spend and refill
REACTION_COST = 0x09C7A56C  # noaddr: (entity) the stamina a reaction costs, by its sub
STAMINA_MAX_AT = 0xBC0
"""The entity's stamina maximum, the u16 `STAMINA_RESET` stores."""

CLASSES = ("flinch", "fall", "knockout")
"""The reaction classes by the cost the donor puts on them, cheapest first."""
REACT_MAIN = 4
"""em75's reaction main state."""
TRAPS = (15, 16)
"""REACTION_CHECK's codes for a trap."""
FALL_CLIPS = 3
"""Clips a pair plays to be a fall or trip."""
HOSTS = (75,)
"""The species whose reaction table is read (`EM75_REACTIONS`)."""
VT_ENTER = 0x88
"""The species vtable slot that enters a pair: `f(entity, main, sub, mode)`."""
POOL = addresses.FATIGUE_CFG.PAIRS.count or 0
"""Pair costs the helper holds."""
CALM = True
"""MHP3rd starts no rage while a monster is tired."""


@dataclass(frozen=True)
class Donor:
    """One MHP3rd species' fatigue row."""

    max: int
    starts: tuple[int, int]
    """The stamina at spawn, one of them by chance."""
    even: bool
    """The row's chances are those of a coin flip between `starts`."""
    tired: int
    """Frames a tired spell lasts."""
    refill: int
    """What `recover` adds: the stamina after a spell."""
    low_hp: int
    """Costs halve at or below this percent of HP; 0 never."""
    costs: Mapping[Pair, int]
    """The stamina a pair costs when it ends, by (main, sub); positive."""
    reactions: Mapping[str, int]
    """The stamina a reaction costs, by class (`CLASSES`)."""


@dataclass(frozen=True)
class Fatigue:
    """The helper's table for a port."""

    max: int
    starts: tuple[int, int]
    tired: int
    refill: int
    low_hp: int
    calm: bool
    pairs: Mapping[Pair, int]
    """Host pair -> cost."""
    own: Mapping[str, int]
    """Own move name -> cost, of the move's head."""

    def module(self) -> dict[str, Any]:
        """The moves module's `fatigue` table: `mhfu.em_fatigue`'s, `own` keyed by name."""
        out: dict[str, Any] = {
            "max": self.max,
            "starts": list(self.starts),
            "tired": self.tired,
            "refill": self.refill,
            "low_hp": self.low_hp,
        }
        if self.calm:
            out["calm"] = True
        out["pairs"] = [[m, s, c] for (m, s), c in sorted(self.pairs.items())]
        if self.own:
            out["own"] = dict(sorted(self.own.items()))
        return out


# --- the donor ---


def _charge(code: Code, start: int) -> int | None:
    """What the code from `start` adds to the stamina: the `a1` of the call or tail jump to
    `STAMINA_ADD`; None where it makes none."""
    a1: int | None = None
    pc = start
    while pc in code.text:
        ins = code.at(pc)
        if ins.uniqueId in (InstrId.cpu_jal, InstrId.cpu_j):
            if ins.getInstrIndexAsVram() != STAMINA_ADD:
                return None
            slot = code.at(pc + 4)
            if slot.uniqueId == InstrId.cpu_addiu and slot.rt == Gpr.a1 and slot.rs == Gpr.zero:
                a1 = slot.getProcessedImmediate()
            return a1
        if ins.isReturn():
            return None
        if ins.uniqueId == InstrId.cpu_addiu and ins.rt == Gpr.a1 and ins.rs == Gpr.zero:
            a1 = ins.getProcessedImmediate()
        pc += 4
    return None


def reaction_costs(p3rd: Extracted) -> dict[int, int]:
    """The stamina a reaction costs, by sub: each case of `REACTION_COST`'s jump table."""
    task = p3rd.overlay(p3.GAME_TASK)
    code = Code(task, task.text)
    fn = code.function(REACTION_COST)
    table = next((s for s in code.switches.values() if s.jr in fn), None)
    if table is None:
        raise ValueError("no sub switch in the reaction cost function")
    out = {}
    for i, target in enumerate(table.targets):
        added = _charge(code, target)
        if added is not None:
            out[table.first + i] = -added
    return out


def stamina_max(p3rd: Extracted) -> int:
    """The maximum `STAMINA_RESET` gives a new monster."""
    task = p3rd.overlay(p3.GAME_TASK)
    code = Code(task, task.text)
    for ins in code.span(code.function(STAMINA_RESET)):
        if ins.uniqueId == InstrId.cpu_sh and ins.getProcessedImmediate() == STAMINA_MAX_AT:
            if (value := code.constant(ins.vram, ins.rt)) is not None:
                return value
    raise ValueError("no stamina maximum in the reset function")


def _starts(row: tuple[int, int, int, int, int]) -> tuple[tuple[int, int], bool]:
    """The two starting staminas of a start row and whether the roll is a coin flip."""
    first, a, second, b, _ = row
    if first >= 100:
        return (a, a), True
    return (a, b), first == 50 and second >= 100


def read(p3rd: Extracted, monster: int, slot: int = 0, row: int = START_ROW) -> Donor:
    """The fatigue row of `monster`'s overlay `slot`; ValueError where the species has none."""
    found = p3.Species.read(p3rd).slots.get(monster, ())
    if slot >= len(found):
        raise ValueError(f"monster {monster} has {len(found)} slots, not {slot + 1}")
    ovl = p3rd.overlay(found[slot].file)
    block = ovl.u32(found[slot].addr + BLOCK)
    at = ovl.u32(block + FATIGUE_ROW) if block in ovl.initialised else 0
    if at not in ovl.initialised:
        raise ValueError(f"monster {monster} has no fatigue row")
    spawn = START.unpack(ovl.read(ovl.u32(at + ROW_REFILLS) + START.size * row, START.size))
    starts, even = _starts(spawn)
    costs: dict[Pair, int] = {}
    cost_at = ovl.u32(at + ROW_COSTS)
    for k in range(MAX_COSTS):
        main, sub, added = COST.unpack(ovl.read(cost_at + COST.size * k, COST.size))
        if main == COST_END:
            break
        costs[main, sub] = -added
    low = ovl.u32(block + LOW_HP_ROW)
    classes = sorted(set(reaction_costs(p3rd).values()))
    if len(classes) != len(CLASSES):
        raise ValueError(f"the donor's reactions have {len(classes)} cost classes, not 3")
    return Donor(
        max=stamina_max(p3rd),
        starts=starts,
        even=even,
        tired=ovl.s16(at),
        refill=spawn[4],
        low_hp=ovl.s8(low + LOW_HP_AT) if low in ovl.initialised else 0,
        costs=costs,
        reactions=dict(zip(CLASSES, classes, strict=True)),
    )


# --- the host ---


def _arg(code: Code, site: int, reg: Register) -> int | None:
    """The literal `reg` holds at a call; one set in the delay slot counts."""
    value = code.constant(site, reg)
    return code.constant(site + 4, reg) if value is None else value


def entered(game: Extracted, species: int) -> dict[int, frozenset[Pair]]:
    """REACTION_CHECK's code -> the reaction pairs its case of the species' AI step enters
    (`EM75_REACTIONS`): every `vt+0x88` call reachable from the case before the code every case
    ends in, whose main is `REACT_MAIN` and sub a literal."""
    ovl = game.em(species)
    code = Code(ovl, ovl.text)
    table = addresses.EM75_REACTIONS
    cases = [ovl.u32(table + 4 * i) for i in range(table.count or 0)]
    enters = {c.site for c in code.calls if c.slot == VT_ENTER}
    done = cases[0]
    out: dict[int, frozenset[Pair]] = {}
    for number, start in enumerate(cases):
        pairs: set[Pair] = set()
        seen: set[int] = set()
        todo = [start]
        while todo:
            pc = todo.pop()
            while pc not in seen and pc != done and pc in code.text:
                seen.add(pc)
                ins = code.at(pc)
                if pc in enters:
                    main, sub = _arg(code, pc, Gpr.a1), _arg(code, pc, Gpr.a2)
                    if main == REACT_MAIN and sub is not None:
                        pairs.add((main, sub))
                if ins.isBranch():
                    todo.append(ins.getBranchVramGeneric())
                    if ins.isUnconditionalBranch():
                        break
                elif ins.uniqueId == InstrId.cpu_j:
                    todo.append(ins.getInstrIndexAsVram())
                    break
                elif ins.isReturn():
                    break
                pc += 4
        out[number] = frozenset(pairs)
    return out


def classify(codes: Entered, clips: Mapping[Pair, int]) -> dict[Pair, str]:
    """Each reaction pair's class: a knockout where a trap code enters it, else a fall where it
    plays `FALL_CLIPS` clips, else a flinch. `clips` counts a pair's clips."""
    traps = {p for c in TRAPS for p in codes.get(c, ())}
    out = {}
    for pair in {p for pairs in codes.values() for p in pairs}:
        if pair in traps:
            out[pair] = CLASSES[2]
        elif clips.get(pair, 0) >= FALL_CLIPS:
            out[pair] = CLASSES[1]
        else:
            out[pair] = CLASSES[0]
    return out


def host_reactions(game: Extracted, species: int) -> dict[Pair, str]:
    """The reaction pairs of host `species` by class; none for a species whose reaction table is
    not known (`HOSTS`)."""
    if species not in HOSTS:
        return {}
    codes = entered(game, species)
    ms = Moveset(game.em(species))
    clips = {
        pair: len(ms.animations(ms.pairs[pair]).ids)
        for pair in {p for pairs in codes.values() for p in pairs}
        if pair in ms.pairs
    }
    return classify(codes, clips)


# --- the table ---


def _line(pair: Pair) -> str:
    return f"{pair[0]},{pair[1]}"


def derive(
    donor: Donor,
    reactions: Mapping[Pair, str],
    b: Brain,
    hold: Collection[Pair] = (),
) -> tuple[Fatigue, list[Finding]]:
    """The helper's table from the donor's row: each `[brain.pairs]` donor pair's cost on what it
    plays, each host reaction pair's class cost. Two sources with different costs on one target:
    the larger. `hold` are pairs that carry own moves, which cost nothing."""
    found: list[Finding] = []

    def say(level: str, code: str, text: str) -> None:
        found.append(Finding(level, code, text, None, "fatigue"))

    sources: dict[Pair | str, list[tuple[str, int]]] = {}
    for key in b.pairs:
        pair = _pair(key)
        if (target := b.target(pair)) is not None:
            sources.setdefault(target, []).append((_line(pair), donor.costs.get(pair, 0)))
    free = sorted(p for p, c in donor.costs.items() if c and b.target(p) is None)
    if free:
        say(
            "info",
            "P3_FATIGUE_UNMAPPED",
            f"donor pairs {', '.join(map(_line, free))} cost stamina and `[brain.pairs]` has no "
            "host move for them: nothing is charged",
        )
    for pair, klass in reactions.items():
        sources.setdefault(pair, []).append((f"{klass} reaction", donor.reactions[klass]))
    pairs: dict[Pair, int] = {}
    own: dict[str, int] = {}
    for target, costs in sources.items():
        top = max(c for _, c in costs)
        if len({c for _, c in costs}) > 1:
            names = ", ".join(f"{n} ({c})" for n, c in costs)
            where = target if isinstance(target, str) else f"host pair {_line(target)}"
            say("info", "P3_FATIGUE_SHARED", f"{where} takes {names}: the larger, {top}")
        if not top:
            continue
        if isinstance(target, str):
            own[target] = top
        elif target in hold:
            say(
                "warning",
                "P3_FATIGUE_HUB",
                f"host pair {_line(target)} carries own moves, which are charged by slot: "
                f"its cost {top} is dropped",
            )
        else:
            pairs[target] = top
    if len(pairs) > POOL:
        say("error", "P3_FATIGUE_PAIRS", f"{len(pairs)} pair costs, the helper holds {POOL}")
    if not donor.even:
        text = f"the donor starts at {donor.starts[0]} or {donor.starts[1]} by an uneven roll"
        say("info", "P3_FATIGUE_STARTS", f"{text}: the helper rolls evenly")
    fat = Fatigue(
        max=donor.max,
        starts=donor.starts,
        tired=donor.tired,
        refill=donor.refill,
        low_hp=max(0, donor.low_hp),
        calm=CALM,
        pairs=pairs,
        own=own,
    )
    return fat, found


def carriers(m: Manifest) -> set[Pair]:
    """The pairs own moves ride: the hub for a move that names none, and `[brain.pairs]` playing an
    own move at all rides it."""
    species = m.port.host_species
    out = {p for mv in m.moves.values() if mv.own and (p := mv.carrier) is not None}
    hub = moves.hub(species)
    uses = any(mv.own and mv.carrier is None for mv in m.moves.values()) or (
        m.brain is not None
        and any(isinstance(m.brain.target(_pair(k)), str) for k in m.brain.pairs)
    )
    if uses and hub is not None:
        out.add(hub)
    return out


def _pair(key: str) -> Pair:
    main, _, sub = key.partition(",")
    return int(main), int(sub)


def table(m: Manifest, data: Data) -> tuple[Fatigue | None, list[Finding]]:
    """The table of a port whose `[brain] fatigue` is set, and the findings; None without."""
    b = m.brain
    if b is None or not b.fatigue:
        return None, []
    try:
        donor = read(data.p3rd, b.donor, b.slot)
    except ValueError as e:
        return None, [Finding("error", "P3_FATIGUE_DONOR", str(e), None, "fatigue")]
    species = m.port.host_species
    hosts = host_reactions(data.fu, species)
    found: list[Finding] = []
    if not hosts:
        text = f"no reaction table is known for host species {species}: hits cost nothing"
        found.append(Finding("warning", "P3_FATIGUE_REACTIONS", text, None, "fatigue"))
    fat, more = derive(donor, hosts, b, carriers(m))
    return fat, [*found, *more]


def expanded(m: Manifest, data: Data, report: Report | None = None) -> Fatigue | None:
    """`table`, each finding handed to `report`; `ManifestError` for the first error."""
    fat, found = table(m, data)
    for f in found:
        if report is not None:
            report(f)
    errors = [f for f in found if f.level == "error"]
    if errors:
        raise ManifestError(f"{errors[0].code}: {errors[0].text}")
    return fat
