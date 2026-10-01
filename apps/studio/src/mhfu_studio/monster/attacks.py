# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Editing where the port hits: the attack volume sets and the levers on attack records.

A handler spawns an attack by id, the record names a volume set, and the set is the same volume
record a hurtbox is, keyed by `set`. Bones index the port's own rig. The runtime writes each set
in place over the host's set of that index, so a set holds at most its `capacities` entry; only
`power`, `element` and `volume` of a record are decoded and authorable. Both are species data.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from typing import Any

from mhfu.em.intel import HitSphere
from mhfu_port.manifest import BYTE, Attack, Hitbox, Manifest, ManifestError

from mhfu_studio.monster.document import PortDocument
from mhfu_studio.monster.parts import VolumeAdoption, volume_fields

LEVERS = ("power", "element", "volume")


def hitbox_of(s: HitSphere, set_index: int, label: str = "") -> Hitbox:
    capsule = s.is_capsule and s.b is not None
    return Hitbox(
        bone=s.bone,
        radius=s.radius,
        set=set_index,
        shape="capsule" if capsule else "sphere",
        offset=list(s.a),
        to=list(s.b) if capsule and s.b is not None else None,
        flags=s.flags,
        label=label,
    )


class AttackSession:
    """Hitbox and attack-record edits on a document, each one undo step. `capacities` maps a set
    to the records that fit in place; a set missing from it is not checked."""

    _FIELDS = ("bone", "radius", "set", "shape", "offset", "to", "flags", "label")

    def __init__(self, doc: PortDocument, n_bones: int | None = None) -> None:
        self.doc = doc
        self.n_bones = n_bones
        self.capacities: dict[int, int] = {}

    @property
    def m(self) -> Manifest:
        return self.doc.manifest

    def volumes(self) -> list[Hitbox]:
        return list(self.m.hitboxes)

    def sets(self) -> list[int]:
        return sorted({h.set for h in self.m.hitboxes})

    def volumes_of(self, set_index: int) -> list[tuple[int, Hitbox]]:
        """`(index into volumes(), volume)` for one set."""
        return [(i, h) for i, h in enumerate(self.m.hitboxes) if h.set == set_index]

    def volume_changed(self, index: int) -> bool:
        saved = self.doc.saved_manifest.hitboxes
        return index >= len(saved) or saved[index] != self.m.hitboxes[index]

    def attack(self, id: int) -> Attack | None:
        return next((a for a in self.m.attacks if a.id == id), None)

    def attacks(self) -> list[Attack]:
        return sorted(self.m.attacks, key=lambda a: a.id)

    def over_capacity(self, set_index: int) -> int:
        cap = self.capacities.get(set_index)
        return 0 if cap is None else max(0, len(self.volumes_of(set_index)) - cap)

    def over_capacity_all(self) -> dict[int, int]:
        return {s: n for s in self.sets() if (n := self.over_capacity(s))}

    def edit_volume(self, index: int, **fields: Any) -> Hitbox:
        cur = self._volume(index)
        new = dataclasses.replace(cur, **volume_fields(cur, fields, self._FIELDS))
        self.doc.edit(lambda m: m.hitboxes.__setitem__(index, new))
        return new

    def scale_volume(self, index: int, factor: float) -> Hitbox:
        return self.edit_volume(index, radius=self._volume(index).radius * factor)

    def remove_volume(self, index: int) -> None:
        self._volume(index)
        self.doc.edit(lambda m: m.hitboxes.__delitem__(index))

    def keep_only(self, index: int) -> int:
        """Drop every other volume of the same set (the other sets are other attacks)."""
        st = self._volume(index).set
        keep = [h for i, h in enumerate(self.m.hitboxes) if h.set != st or i == index]
        gone = len(self.m.hitboxes) - len(keep)
        self.doc.edit(lambda m: setattr(m, "hitboxes", keep))
        return gone

    def add_volume(self, h: Hitbox) -> int:
        h = dataclasses.replace(h, **volume_fields(h, dataclasses.asdict(h), self._FIELDS))
        self.doc.edit(lambda m: m.hitboxes.append(h))
        return len(self.m.hitboxes) - 1

    def drop_set(self, set_index: int) -> int:
        """Remove the port's volumes of one set: the host's stands at runtime."""
        keep = [h for h in self.m.hitboxes if h.set != set_index]
        gone = len(self.m.hitboxes) - len(keep)
        if gone:
            self.doc.edit(lambda m: setattr(m, "hitboxes", keep))
        return gone

    def adopt_set(
        self,
        set_index: int,
        spheres: Iterable[HitSphere],
        source: str = "the host overlay",
        label: str = "",
    ) -> VolumeAdoption:
        """Replace the port's volumes of one set with the host's, markers kept in order."""
        new = [hitbox_of(s, set_index, label) for s in spheres]
        keep = [h for h in self.m.hitboxes if h.set != set_index] + new
        self.doc.edit(lambda m: setattr(m, "hitboxes", keep))
        off = [
            h.bone
            for h in new
            if not h.is_marker and self.n_bones is not None and not 0 <= h.bone < self.n_bones
        ]
        return VolumeAdoption(len(new), off, source)

    def set_attack(self, id: int, **levers: Any) -> Attack:
        """Set levers on record `id`; a lever given as None goes back to the host's byte."""
        bad = sorted(set(levers) - {*LEVERS, "label"})
        if bad:
            raise ManifestError(f"an attack record has no lever {bad[0]!r}: only {LEVERS}")
        for k in LEVERS:
            v = levers.get(k)
            if v is not None and int(v) not in BYTE:
                raise ManifestError(f"{k} {v!r} is outside a byte")
        new = dataclasses.replace(self.attack(id) or Attack(id), **levers)

        def apply(m: Manifest) -> None:
            at = next((i for i, a in enumerate(m.attacks) if a.id == id), len(m.attacks))
            m.attacks[at : at + 1] = [new]

        self.doc.edit(apply)
        return new

    def clear_attack(self, id: int) -> None:
        """Drop the port's record `id`: the host's stands."""
        if self.attack(id) is not None:
            self.doc.edit(lambda m: setattr(m, "attacks", [a for a in m.attacks if a.id != id]))

    def _volume(self, index: int) -> Hitbox:
        if not 0 <= index < len(self.m.hitboxes):
            raise ManifestError(f"no volume {index}: the list has {len(self.m.hitboxes)}")
        return self.m.hitboxes[index]


def summarise(m: Manifest, host_records: int | None = None) -> list[str]:
    sets = sorted({h.set for h in m.hitboxes})
    out = [
        f"hitboxes: {len(m.hitboxes)} volume(s) over set(s) {', '.join(map(str, sets))}"
        if m.hitboxes
        else "hitboxes: none authored; the port hits with the host's own sets"
    ]
    if m.attacks:
        tuned = (
            f"{a.id}({','.join(k for k in LEVERS if getattr(a, k) is not None)})" for a in m.attacks
        )
        out.append(f"attacks: {len(m.attacks)} record(s) tuned: " + ", ".join(tuned))
    elif host_records is not None:
        out.append(f"attacks: none tuned; the host's {host_records} record(s) stand")
    return out
