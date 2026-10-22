# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The charge state of an MHP3rd monster that has one (the Zinogre), read from its species overlay:
what the framework's charge helper (`mhfu.em_charge`) needs of the donor.

    donor = read(p3rd, p3rd.read(source.anim), 40)
    donor.ups[1, 15]    # (25, 35, 40, 55): the gauge one whole charge-up gives, by HP band
    donor.transform     # (1, 4)

The gauge (+0xFF4) rises only through `CHARGE_ADD`, which a charge-up handler calls at keyframes
of its clip with an amount by HP band and, near a full gauge, a top-up; `Walk.settle` runs each
charge-up pair from an empty gauge to its end and the gain is the gauge left, so the amounts, the
bands, the repeats of a pair and the caps come from the code. The transform is the pair whose
handler reaches `SET_CHARGED`. A hit while the monster is not charged takes a step off the gauge,
one while it is charged a step off the durability, each by rank from a row of the species' data;
the cooldowns are constants the handlers store.

The values are the species' normal spawn's (`VARIANT` clear) and its lowest rank's (row 0 of the
chip rows).
"""

from __future__ import annotations

import struct
from collections.abc import Collection
from dataclasses import dataclass
from typing import NamedTuple

from mhfu.em.moveset import Walker, known
from mhfu.files import Extracted
from mhfu.mips import Code

from . import p3hits
from . import p3moveset as p3
from .p3hits import Walk

Pair = tuple[int, int]
Gains = tuple[int, int, int, int]
"""The gauge a charge-up gives at HP of at least 95 %, 75 %, 40 % of the maximum, and below."""

CHARGE_ADD = 0x09DC9694  # noaddr: (entity, amount, cap) raises the gauge; cap set: 99 at most
SET_CHARGED = 0x09DC905C  # noaddr: (entity) the transform's end: charged, durability and gauge 100
UNCHARGE = 0x09DB5068  # noaddr: (entity, flag) charged to not: gauge 0 and the cooldown starts
CHIP_GAUGE_ROWS = 0x09DD9D3C  # noaddr: by rank {u16 step, s16 cap}: what a hit takes off the gauge
CHIP_DURABILITY_ROWS = 0x09DD9D30  # noaddr: the same for the durability
GAUGE, DURABILITY, COOLDOWN = 0xFF4, 0xFF3, 0xFCC
"""The entity's charge gauge (s16), the charged durability (s8) and the cooldown (s16)."""
REPEATS, VARIANT = 0xF64, 0xF67
"""The entity bytes a charge-up counts its repeats in, and the species' variant flag."""
TRACK = ((REPEATS, 1), (GAUGE, 2))
"""The cells a charge-up's run carries from frame to frame."""
START = {(VARIANT, 1): 0, (GAUGE, 2): 0}
"""What a run starts with: the normal spawn and an empty gauge."""
BANDS = (1.0, 0.85, 0.6, 0.2)
"""An HP ratio inside each of a gain's bands (at least 95 %, 75 %, 40 %, below)."""
ROW = struct.Struct("<Hh")
"""A chip row: the step a hit takes and the most one reaction burst takes in steps."""
RANK = 0
"""The row of the chip rows taken: the lowest rank's."""
DEPTH = 2
"""Calls below a handler its search for `CHARGE_ADD` and `SET_CHARGED` follows."""
UP, TRANSFORM = "up", "transform"


class Chip(NamedTuple):
    step: int
    """What one hit takes."""
    cap: int
    """What one burst of hits takes at most."""


@dataclass(frozen=True)
class Donor:
    """One species' charge state."""

    ups: dict[Pair, Gains]
    """Each charge-up pair's gain from an empty gauge, by HP band."""
    transform: Pair
    """The pair that charges the monster: the lowest of those whose handler does (the others are
    the same handler with another argument)."""
    gauge: Chip
    """What hits take off the gauge while it is not charged."""
    durability: Chip
    """What hits take off the durability while it is charged."""
    cooldown_up: int
    """AI frames after a charge-up starts before another may."""
    cooldown_out: int
    """AI frames after a discharge."""


def _stored(code: Code, fn: int, offset: int) -> set[int]:
    """The constants the function at `fn` stores at `offset`."""
    out = set()
    for ins in code.span(code.function(fn)):
        if ins.uniqueId in p3hits.STORES and ins.getProcessedImmediate() == offset:
            if (v := code.constant(ins.vram, ins.rt)) is not None:
                out.add(v)
    return out


def _chip(walk: Walk, rows: int) -> Chip:
    ovl = walk.ms.ovl
    at = rows + ROW.size * RANK
    if at not in ovl.initialised:
        raise ValueError(f"no chip row at 0x{at:08X}")
    step, cap = ROW.unpack(ovl.read(at, ROW.size))
    return Chip(step, cap)


def _reaching(code: Code, targets: Collection[int], depth: int) -> set[int]:
    """The start of every function that calls one of `targets` through at most `depth` calls."""
    found: set[int] = set()
    frontier = set(targets)
    for _ in range(depth):
        frontier = {code.function(site).start for t in frontier for site in code.callers(t)}
        frontier -= found
        found |= frontier
    return found


def _pairs(ms: p3.Moveset) -> dict[str, list[p3.Pair]]:
    """The pairs whose handler reaches `CHARGE_ADD` (UP) and `SET_CHARGED` (TRANSFORM)."""
    walker = Walker(ms.code, {CHARGE_ADD: UP, SET_CHARGED: TRANSFORM}, depth=DEPTH, guards=False)
    near = _reaching(ms.code, [CHARGE_ADD, SET_CHARGED], DEPTH + 1)
    out: dict[str, list[p3.Pair]] = {UP: [], TRANSFORM: []}
    for pair in ms.pairs.values():
        if pair.handler is None or ms.code.function(pair.handler).start not in near:
            continue
        sites = walker.run(pair.handler, None, known({p3.MAIN: pair.main, p3.SUB: pair.sub}))
        for kind in sorted({s.kind for s in sites}):
            out[kind].append(pair)
    return out


def read(p3rd: Extracted, anim: bytes, monster: int, slot: int = 0) -> Donor:
    """The charge state of `monster`'s overlay `slot`, with `anim` its animation file (clip
    lengths); ValueError where the species has none or its code is not laid out as expected."""
    walk = Walk.of(p3rd, monster, anim, slot, track=TRACK, facts=START)
    ms = walk.ms
    ins = ms.code.at(CHARGE_ADD) if CHARGE_ADD in ms.code.text else None
    if ins is None or ins.getProcessedImmediate() != GAUGE or not ins.doesLoad():
        raise ValueError(f"monster {monster} has no charge state")
    found = _pairs(ms)
    if not found[UP] or len({p.handler for p in found[TRANSFORM]}) != 1:
        raise ValueError(
            f"monster {monster} has {len(found[UP])} charge-up pairs and "
            f"{len({p.handler for p in found[TRANSFORM]})} transform handlers, not some and one"
        )
    ups: dict[Pair, Gains] = {}
    cooldowns: set[int] = set()
    for pair in found[UP]:
        assert pair.handler is not None
        own = ms.code.function(pair.handler)
        cooldowns |= _stored(ms.code, pair.handler, COOLDOWN)
        got = []
        for hp in BANDS:
            s = walk.settle(pair, hp)
            if not s.left or any(pc in own for pc in s.undecided):
                raise ValueError(f"the charge-up {pair.main},{pair.sub} cannot be run to its end")
            got.append(s.cells.get((GAUGE, 2), 0))
        ups[pair.main, pair.sub] = (got[0], got[1], got[2], got[3])
    up = cooldowns
    out = _stored(ms.code, UNCHARGE, COOLDOWN)
    if len(up) != 1 or len(out) != 1:
        raise ValueError(f"monster {monster}'s cooldowns are {sorted(up)} and {sorted(out)}")
    first = min(found[TRANSFORM], key=lambda p: (p.main, p.sub))
    return Donor(
        ups=ups,
        transform=(first.main, first.sub),
        gauge=_chip(walk, CHIP_GAUGE_ROWS),
        durability=_chip(walk, CHIP_DURABILITY_ROWS),
        cooldown_up=up.pop(),
        cooldown_out=out.pop(),
    )
