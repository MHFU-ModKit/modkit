# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Which attack each handler of a big-monster overlay spawns. The attack itself is data
(`mhfu.hitbox`); the id a handler asks for is the literal it passes to its species' attack
spawner, one of game_task's per-species node constructors (ATTACK_SPAWNERS).

Only em75's spawner-to-table join is traced in the running game (TIGREX_ATTACK_SPAWN); for the
others the spawner is the constructor the overlay calls with a moveset's worth of literal ids,
and `fit` says whether those ids fit the table. The literal is the handler's id: an entity of
another species served by the overlay reads record `id + hitbox.id_offset(...)`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .. import addresses as a
from ..hitbox import AttackTable
from ..mips import Code, Gpr
from .effects import literal

SPAWNERS = range(a.ATTACK_SPAWNERS, a.ATTACK_SPAWNERS_END)
ID = Gpr.a2
"""The register every spawner takes the attack id in."""
MIN_IDS = 3
MAX_FIRST_ID = 2
"""A moveset's spawner is called with at least MIN_IDS distinct literals, the lowest at most
MAX_FIRST_ID; the constructors several overlays share are not."""


@dataclass(frozen=True)
class Site:
    site: int
    id: int | None
    """The literal attack id, None where the code computes it."""
    fn: int
    """The function holding the call."""


@dataclass
class Spawner:
    fn: int
    sites: list[Site] = field(default_factory=list)

    @property
    def ids(self) -> list[int]:
        """The distinct literal ids."""
        return sorted({s.id for s in self.sites if s.id is not None})

    @property
    def literal(self) -> int:
        """Sites passing a literal id."""
        return sum(s.id is not None for s in self.sites)

    @property
    def moveset(self) -> bool:
        return len(self.ids) >= MIN_IDS and self.ids[0] <= MAX_FIRST_ID


def family(code: Code) -> dict[int, Spawner]:
    """Every ATTACK_SPAWNERS function the overlay calls, with its call sites."""
    out: dict[int, Spawner] = {}
    for c in code.calls:
        if c.target is not None and c.target in SPAWNERS:
            sp = out.setdefault(c.target, Spawner(c.target))
            sp.sites.append(Site(c.site, literal(code, c.site, ID), code.function(c.site).start))
    return dict(sorted(out.items()))


def spawner(code: Code) -> Spawner | None:
    """The species' own spawner: of those called like a moveset, the one with the most
    literal sites. None for em01 and em33, which have no attack table either."""
    return max(
        (s for s in family(code).values() if s.moveset),
        key=lambda s: (s.literal, -s.fn),
        default=None,
    )


def extras(code: Code, main: Spawner | None) -> list[Spawner]:
    """The other spawners the overlay calls (em75's projectiles); not joined to a table."""
    return [s for fn, s in family(code).items() if main is None or fn != main.fn]


def by_handler(code: Code) -> dict[int, list[int]]:
    """Function -> the literal attack ids it passes to the species' spawner."""
    sp = spawner(code)
    out: dict[int, set[int]] = defaultdict(set)
    for s in [] if sp is None else sp.sites:
        if s.id is not None:
            out[s.fn].add(s.id)
    return {fn: sorted(ids) for fn, ids in sorted(out.items())}


def fit(sp: Spawner | None, table: AttackTable | None) -> str:
    """How far the spawner's ids join the table: `measured` (em75, traced in the running game),
    `consistent`, `ids_exceed_table`, `no_spawner` or `no_table`."""
    if sp is None:
        return "no_spawner"
    if table is None:
        return "no_table"
    if sp.fn == a.TIGREX_ATTACK_SPAWN:
        return "measured"
    return "consistent" if sp.ids and sp.ids[-1] < len(table.attacks) else "ids_exceed_table"
