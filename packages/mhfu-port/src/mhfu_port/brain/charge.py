# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The donor's charge state as the framework's helper takes it: `mhfu.em_charge`'s table, read from
MHP3rd's files (`p3charge`) and mapped onto the port's own moves.

    ch, findings = table(manifest, data)         # `[brain] charge = true`
    ch.module()                                  # the moves module's `charge` table

The helper keeps a gauge, a charged state and a cooldown for the monster and answers the script's
`switch_query` (charged, cooldown running). Each donor charge-up pair becomes the own move
`[brain.pairs]` plays for it, with the gauge a whole charge-up gives by HP band; the transform pair
becomes the own move the helper plays when the gauge is full. Hits wear the gauge or, while charged,
the durability, by the donor's per-hit step. A charged monster discharges at once in em75's
knockout reactions, as MHP3rd's does in a knockout or a trap (`brain.fatigue`'s classes, the one
classifier).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from mhfu import addresses
from mhfu.em.script.check import Finding

from .. import p3charge
from ..data import Data
from ..manifest import Brain, Manifest, ManifestError
from . import fatigue
from .own import Report

Pair = tuple[int, int]
Gains = p3charge.Gains

UPS = addresses.CHARGE_CFG.UPS.count or 0
"""Charge-up moves the helper holds."""
PAIRS = addresses.CHARGE_CFG.PAIRS.count or 0
"""Discharging pairs it holds."""
KNOCKOUT = fatigue.CLASSES[2]
"""The reaction class that discharges."""


@dataclass(frozen=True)
class Charge:
    """The helper's table for a port."""

    ups: Mapping[str, Gains]
    """Own move name -> the gauge it gives by HP band."""
    transform: str
    chip_gauge: int
    chip_durability: int
    cooldown_up: int
    cooldown_out: int
    discharge: tuple[Pair, ...]
    """Host pairs that discharge a charged monster."""
    named: frozenset[Pair] = field(default_factory=frozenset, compare=False)
    """The donor pairs it reads, whether or not the script enters them."""

    def module(self) -> dict[str, Any]:
        """The moves module's `charge` table: `mhfu.em_charge`'s, `ups` and `transform` by name."""
        return {
            "ups": {name: list(g) for name, g in sorted(self.ups.items())},
            "transform": self.transform,
            "chip_gauge": self.chip_gauge,
            "chip_durability": self.chip_durability,
            "cooldown_up": self.cooldown_up,
            "cooldown_out": self.cooldown_out,
            "discharge": [list(p) for p in self.discharge],
        }


def _line(pair: Pair) -> str:
    return f"{pair[0]},{pair[1]}"


def derive(
    donor: p3charge.Donor,
    reactions: Mapping[Pair, str],
    b: Brain,
    moves: Mapping[str, bool] | None = None,
) -> tuple[Charge | None, list[Finding]]:
    """The helper's table from the donor's charge state: each charge-up pair's gain on the own move
    `[brain.pairs]` plays for it, the transform on its own move, the host's knockout reactions as
    the discharge pairs. Two pairs on one move: the larger gain in each band. `moves` (name -> is
    an own move, the expanded manifest's `[moves]`) makes a name the manifest lacks an error; None
    does not look."""
    found: list[Finding] = []

    def say(level: str, code: str, text: str) -> None:
        found.append(Finding(level, code, text, None, "charge"))

    def own(pair: Pair, what: str) -> str | None:
        """The own move the donor `pair` plays, or None, said: the transform's is an error."""
        level = "error" if what == "transform" else "warning"
        target = b.target(pair)
        if isinstance(target, str):
            if moves is None or moves.get(target, False):
                return target
            say(level, "P3_CHARGE_MOVE", f"{what} {_line(pair)} plays {target}, no own move")
            return None
        if target is None:
            text = f"{what} {_line(pair)} has no entry in `[brain.pairs]`"
        else:
            text = f"{what} {_line(pair)} plays host pair {_line(target)}, not an own move"
        say(level, "P3_CHARGE_UNMAPPED", text)
        return None

    ups: dict[str, Gains] = {}
    sources: dict[str, list[Pair]] = {}
    for pair, gains in sorted(donor.ups.items()):
        if (name := own(pair, "charge-up")) is None:
            continue
        sources.setdefault(name, []).append(pair)
        old = ups.get(name)
        if old is not None and old != gains:
            gains = (
                max(old[0], gains[0]),
                max(old[1], gains[1]),
                max(old[2], gains[2]),
                max(old[3], gains[3]),
            )
        ups[name] = gains
    for name, pairs in sources.items():
        if len({donor.ups[p] for p in pairs}) > 1:
            names = ", ".join(f"{_line(p)} {donor.ups[p]}" for p in pairs)
            say("info", "P3_CHARGE_SHARED", f"{name} takes {names}: the larger, {ups[name]}")
    transform = own(donor.transform, "transform")
    if len(ups) > UPS:
        say("error", "P3_CHARGE_UPS", f"{len(ups)} charge-up moves, the helper holds {UPS}")
    knockouts = tuple(sorted(p for p, k in reactions.items() if k == KNOCKOUT))
    if len(knockouts) > PAIRS:
        say(
            "error",
            "P3_CHARGE_PAIRS",
            f"{len(knockouts)} discharge pairs, the helper holds {PAIRS}",
        )
    if any(f.level == "error" for f in found) or transform is None:
        return None, found
    named = frozenset([donor.transform, *donor.ups])
    return (
        Charge(
            ups=ups,
            transform=transform,
            chip_gauge=donor.gauge.step,
            chip_durability=donor.durability.step,
            cooldown_up=donor.cooldown_up,
            cooldown_out=donor.cooldown_out,
            discharge=knockouts,
            named=named,
        ),
        found,
    )


def table(m: Manifest, data: Data) -> tuple[Charge | None, list[Finding]]:
    """The table of a port whose `[brain] charge` is set, and the findings; None without. `m` is
    the manifest with its derived own moves (`own.expand`)."""
    b = m.brain
    if b is None or not b.charge:
        return None, []
    try:
        donor = p3charge.read(data.p3rd, data.p3rd.read(m.source.anim), b.donor, b.slot)
    except ValueError as e:
        return None, [Finding("error", "P3_CHARGE_DONOR", str(e), None, "charge")]
    species = m.port.host_species
    hosts = fatigue.host_reactions(data.fu, species)
    found: list[Finding] = []
    if not hosts:
        text = (
            f"no reaction table is known for host species {species}: a knockout does not discharge"
        )
        found.append(Finding("warning", "P3_CHARGE_REACTIONS", text, None, "charge"))
    ch, more = derive(donor, hosts, b, {n: mv.own for n, mv in m.moves.items()})
    return ch, [*found, *more]


def expanded(m: Manifest, data: Data, report: Report | None = None) -> Charge | None:
    """`table`, each finding handed to `report`; `ManifestError` for the first error."""
    ch, found = table(m, data)
    for f in found:
        if report is not None:
            report(f)
    errors = [f for f in found if f.level == "error"]
    if errors:
        raise ManifestError(f"{errors[0].code}: {errors[0].text}")
    return ch
