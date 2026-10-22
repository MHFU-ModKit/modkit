# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Spare host attack records for the donor records no host record matches.

em75's record table holds two more species' records (`hitbox.ID_OFFSETS`), on volume sets of
their own, which a species-75 port never spawns. A donor record with no match takes one of them:
`[[attack]]` sets its power, kind and active frames from the donor's and points it at one of
those sets, and `[[hitbox]]` makes that set the donor's volumes on the port's joints. `Spares`
allocates and appends both to the manifest it is given (`brain.own.expand` hands it the copy it
returns); nothing is saved.

A spare record keeps the bytes the game's own record has that no lever reaches (family, angle,
tag, element, +0x0C): the allocator takes the free record whose kind is wanted and those bytes
are the plainest, so a spare is not a knockdown by accident.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal, NamedTuple

import numpy as np
from mhfu import hitbox, hitzone
from numpy.typing import NDArray

from ..manifest import Attack, Hitbox, Manifest
from ..p3hits import Hits, Record, Volume

Floats = NDArray[np.float64]
Family = Literal["hit", "weak", "roar"]

NO_DAMAGE = 0x34
"""The donor record kind of the roar's and the slams' node, which hurts nobody."""
HOST_NO_DAMAGE = 0x1D
"""The host record kind it pairs with."""
WEAK = 1
"""The donor kind of the small hits (power 5 to 35)."""
HOST_KIND: dict[Family, int] = {"hit": 2, "weak": 1, "roar": HOST_NO_DAMAGE}
"""The host kind byte of each family."""
HOST_FAMILY_BYTE = {"hit": 0, "weak": 0, "roar": 0x10}
"""The family byte (+0x05) the host's own records of each family carry."""
RULED: dict[int, frozenset[int]] = {
    75: frozenset({5, 6, 21, 22, 24, 28, 31, 39, 40, 99, 100}),
}
"""By overlay species, the ids its attack-node code treats by id, read from the overlay: the
initialiser (24 starts at bone 43) and the two per-frame steps (5, 6, 21, 22, 28, 31 in the one,
39, 40, 99, 100 in the other; 6 is refreshed). A spare never takes one."""
POWER_SCALE = 0.78
"""Host power over donor power where no record of the port matched: em75's against em058's, the
median over the records the two share."""
MAX_ACTIVE = 255


class Spare(NamedTuple):
    """One donor record's host record."""

    id: int
    """The host record."""
    set: int
    """The volume set it is pointed at."""
    family: Family
    volumes: int
    """How many volumes the set carries."""
    attack: Attack
    """Its `[[attack]]` entry, in the manifest."""
    joints: tuple[int, ...]
    """The port joints the volumes sit on."""


def family(r: Record) -> Family:
    """What kind of node a donor record is."""
    if r.power == 0 or r.kind == NO_DAMAGE:
        return "roar"
    return "weak" if r.kind == WEAK else "hit"


class Row(NamedTuple):
    """One donor volume on the port's rig."""

    joint: int
    shape: Literal["sphere", "capsule"]
    radius: float
    offset: list[float]
    to: list[float] | None


class Spares:
    """The free records and volume sets of the host's table, and what has been taken."""

    def __init__(
        self,
        m: Manifest,
        hits: Hits | None,
        table: hitbox.AttackTable | None,
        joint_of: Mapping[int, int],
        bind: Floats,
        scale: float = POWER_SCALE,
    ) -> None:
        self.m, self.hits, self.table = m, hits, table
        self.joint_of, self.bind, self.scale = joint_of, bind, scale
        self._ids: list[int] = []
        self._sets: dict[int, int] = {}
        """Free set -> its record count."""
        self._taken: dict[int, Spare | str] = {}
        self._shared: dict[int, int] = {}
        """Donor volume set -> the host set that carries it."""
        if table is not None:
            self._free(m, table)

    def _free(self, m: Manifest, table: hitbox.AttackTable) -> None:
        species = m.port.host_species
        starts = sorted(o for o in hitbox.ID_OFFSETS.get(species, {}).values() if o > 0)
        if not starts:
            return
        asked = set() if m.brain is None else set(m.brain.attacks.values())
        used = {a.id for a in m.attacks} | asked | RULED.get(species, frozenset())
        n = len(table.attacks)
        self._ids = [i for i in range(starts[0] + 1, n) if i not in starts and i not in used]
        own = {table.attacks[i].volume_set for i in range(min(starts[0], n))}
        held = {h.set for h in m.hitboxes} | {a.volume for a in m.attacks if a.volume is not None}
        self._sets = {i: len(s.volumes) for i, s in enumerate(table.volumes) if i not in own | held}

    def take(self, record: int, frames: int = 0) -> Spare | str:
        """The spare for donor `record`, allocated at the first ask; `frames` is how long a move
        keeps the node, which its active frames grow to cover. A str says why there is none."""
        got = self._taken.get(record)
        if got is None:
            got = self._taken[record] = self._allocate(record)
        if isinstance(got, Spare) and got.attack.active is not None:
            got.attack.active = max(got.attack.active, min(frames, MAX_ACTIVE))
        return got

    def _allocate(self, record: int) -> Spare | str:
        if self.table is None or self.hits is None:
            return "the host has no attack table"
        if not self._ids:
            return "the host's table has no spare record"
        donor = self.hits.records[record] if 0 <= record < len(self.hits.records) else None
        if donor is None:
            return "the donor has no such record"
        rows = self._rows(self.hits.volumes(record))
        if not rows:
            return "none of its volumes lands on the port's rig"
        host_set = self._shared.get(donor.volumes)
        if host_set is None:
            fits = [(n, i) for i, n in self._sets.items() if n >= len(rows)]
            if not fits:
                return (
                    f"{len(rows)} volume(s), the largest free set holds "
                    f"{max(self._sets.values(), default=0)}"
                )
            host_set = min(fits)[1]
            self._shared[donor.volumes] = host_set
            del self._sets[host_set]
            label = f"donor {record}"
            self.m.hitboxes += [
                Hitbox(r.joint, r.radius, host_set, r.shape, r.offset, r.to, 0, label) for r in rows
            ]
        fam = family(donor)
        i = min(self._ids, key=lambda i: self._rank(i, fam))
        self._ids.remove(i)
        power = 0 if fam == "roar" else max(1, min(255, round(donor.power * self.scale)))
        active = max(1, min(MAX_ACTIVE, donor.length))
        attack = Attack(i, power, None, host_set, HOST_KIND[fam], active, f"donor {record}")
        self.m.attacks.append(attack)
        return Spare(i, host_set, fam, len(rows), attack, tuple(r.joint for r in rows))

    def _rank(self, i: int, fam: Family) -> tuple[bool, bool, bool, bool, int]:
        """Best first: the kind wanted, then the bytes no lever sets plain (`+0x0C` and the angle
        zero, the family byte the host's own records of the kind carry), then the lowest id."""
        assert self.table is not None
        a = self.table.attacks[i]
        return (
            a.kind != HOST_KIND[fam],
            a.half_0c != 0,
            a.angle != 0,
            a.family != HOST_FAMILY_BYTE[fam],
            i,
        )

    def _rows(self, volumes: Sequence[Volume]) -> list[Row]:
        """The donor's volumes on the port's joints: markers, bones off the rig and empty
        volumes are left out. A capsule across two bones keeps its far end as an offset from the
        first, in bind pose (the match is made there too)."""
        out = []
        for v in volumes:
            j = self.joint_of.get(v.bone)
            if v.bone in hitzone.MARKER_BONES or v.radius <= 0 or j is None:
                continue
            offset = [float(x) for x in v.offset]
            to = None
            j2 = self.joint_of.get(v.bone2)
            if v.shape == "capsule":
                to = [float(x) for x in v.to]
            elif v.shape == "capsule2" and j2 is not None:
                to = [float(x) for x in self.bind[j2] + np.asarray(v.to) - self.bind[j]]
            out.append(Row(j, "sphere" if to is None else "capsule", float(v.radius), offset, to))
        return out
