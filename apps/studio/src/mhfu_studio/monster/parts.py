# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Editing where the port is hit: part names, the damage grid and the hurtbox volumes.

The grid is species data: riding a host means inheriting its grid, and the runtime writes an
authored one over the host's. Volumes are bone indices into the rig the PORT ships, so adopting
the host's gives a starting point to see in the viewport, not a correct answer. The runtime
writes the list in place over the host's own set, so at most `capacity` volumes fit.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from mhfu import hitzone
from mhfu.em.intel import HitSphere
from mhfu_port.manifest import ROWS, SHAPES, HitzoneState, Hurtbox, Manifest, ManifestError, Part

from mhfu_studio.monster.clips import check_name
from mhfu_studio.monster.document import PortDocument

SUGGESTED = {
    1: "head",
    2: "body",
    3: "tail",
    4: "left_wing",
    5: "left_leg",
    6: "right_wing",
    7: "right_leg",
}
"""Names for the eight accumulators where MH's own are obvious; slot 0 is nobody."""
STATE_NAMES = ("normal", "enraged", "third")
ADOPTED = "adopted from the host species; the port inherits these at runtime either way"
U16 = range(0x10000)


class Rows(Protocol):
    """A grid state from either side: intel's `GridState` or the manifest's."""

    @property
    def rows(self) -> Sequence[Sequence[int]]: ...


def blank_grid() -> list[list[int]]:
    return [[0] * len(hitzone.COLUMNS) for _ in ROWS]


@dataclass
class VolumeAdoption:
    """What an adoption did: never just a count."""

    adopted: int = 0
    off_rig: list[int] = field(default_factory=list)
    """Bones off the end of the port's rig; written anyway, so the validator can say so."""
    source: str = ""

    @property
    def clean(self) -> bool:
        return not self.off_rig

    def describe(self) -> str:
        if not self.adopted:
            return "nothing to copy"
        if self.clean:
            return (
                f"copied {self.adopted} from {self.source}. Every joint number exists on your "
                "skeleton, which is not the same as the right joint."
            )
        bones = ", ".join(map(str, self.off_rig[:8]))
        return (
            f"copied {self.adopted} from {self.source}; {len(self.off_rig)} name a joint your "
            f"skeleton does not have ({bones}): they are the base monster's numbers."
        )


def volume_fields(cur: Any, fields: dict[str, Any], allowed: Iterable[str]) -> dict[str, Any]:
    """`fields` checked against what the loader does not check, with the manifest's types."""
    bad = sorted(set(fields) - set(allowed))
    if bad:
        raise ManifestError(f"a volume has no field {bad[0]!r}")
    out = dict(fields)
    if "radius" in out:
        out["radius"] = float(out["radius"])
        if out["radius"] < 0:
            raise ManifestError("a negative radius is not a volume")
    if "bone" in out and int(out["bone"]) not in U16:
        raise ManifestError(f"bone {out['bone']} does not fit the record's u16")
    for key in ("offset", "to"):
        if out.get(key) is not None:
            out[key] = [float(v) for v in out[key]]
    if out.get("shape", cur.shape) not in SHAPES:
        raise ManifestError(f"shape {out['shape']!r} is not one of {', '.join(SHAPES)}")
    return out


def hurtbox_of(s: HitSphere, label: str = "") -> Hurtbox:
    capsule = s.is_capsule and s.b is not None
    return Hurtbox(
        bone=s.bone,
        radius=s.radius,
        part=s.part & hitzone.PART_MASK,
        hitzone_row=s.hitzone_row,
        shape="capsule" if capsule else "sphere",
        offset=list(s.a),
        to=list(s.b) if capsule and s.b is not None else None,
        flags=s.flags,
        label=label,
    )


class PartSession:
    """Part, grid and hurtbox edits on a document, each one undo step. `n_bones` (the rig the
    port ships) and `capacity` (the host set's record count) enable their checks."""

    _FIELDS = ("bone", "radius", "part", "hitzone_row", "shape", "offset", "to", "flags", "label")

    def __init__(self, doc: PortDocument, n_bones: int | None = None) -> None:
        self.doc = doc
        self.n_bones = n_bones
        self.capacity: int | None = None

    @property
    def m(self) -> Manifest:
        return self.doc.manifest

    def part(self, index: int) -> tuple[str, Part] | None:
        return next(((n, p) for n, p in self.m.parts.items() if p.index == index), None)

    def name_of(self, index: int) -> str:
        found = self.part(index)
        return found[0] if found else ""

    def states(self) -> list[HitzoneState]:
        return list(self.m.hitzones)

    def state(self, index: int) -> HitzoneState | None:
        return self.m.hitzones[index] if 0 <= index < len(self.m.hitzones) else None

    def volumes(self) -> list[Hurtbox]:
        return list(self.m.hurtboxes)

    def volume_changed(self, index: int) -> bool:
        """Volume `index` differs from the one at that position on disk."""
        saved = self.doc.saved_manifest.hurtboxes
        return index >= len(saved) or saved[index] != self.m.hurtboxes[index]

    @property
    def over_capacity(self) -> int:
        """Volumes that would not fit in place at runtime."""
        return 0 if self.capacity is None else max(0, len(self.m.hurtboxes) - self.capacity)

    def name_part(
        self,
        index: int,
        name: str,
        label: str = "",
        severable: bool = False,
        hitzone_row: int | None = None,
    ) -> None:
        """Name accumulator `index`, replacing its old name."""
        name = check_name(name)
        clash = self.m.parts.get(name)
        if clash is not None and clash.index != index:
            raise ManifestError(f"part {name!r} already names slot {clash.index}")

        def apply(m: Manifest) -> None:
            m.parts = {n: p for n, p in m.parts.items() if p.index != index}
            m.parts[name] = Part(index, hitzone_row, severable, label)

        self.doc.edit(apply)

    def set_hitzone(self, state: int, row: int, column: str | int, value: int) -> None:
        """One percentage; `column` a name or an index."""
        if self.state(state) is None:
            raise ManifestError(f"no grid state {state}: adopt or add one first")
        col = hitzone.COLUMNS.index(column) if isinstance(column, str) else column
        if row not in ROWS or col not in range(len(hitzone.COLUMNS)):
            raise ManifestError(f"row {row}, column {column!r} is not in the grid")

        def apply(m: Manifest) -> None:
            m.hitzones[state].rows[row][col] = int(value)

        self.doc.edit(apply)

    def fill_row(
        self, state: int, row: int, value: int, columns: Sequence[str] = ("cut", "impact", "shot")
    ) -> None:
        """Several columns of one row, as one edit."""
        cols = [hitzone.COLUMNS.index(c) for c in columns]
        if self.state(state) is None or row not in ROWS:
            raise ManifestError(f"no grid row {state}/{row}")

        def apply(m: Manifest) -> None:
            for c in cols:
                m.hitzones[state].rows[row][c] = int(value)

        self.doc.edit(apply)

    def edit_volume(self, index: int, **fields: Any) -> Hurtbox:
        """Change fields of volume `index`; refused here when the loader would refuse it."""
        cur = self._volume(index)
        new = dataclasses.replace(cur, **volume_fields(cur, fields, self._FIELDS))
        self.doc.edit(lambda m: m.hurtboxes.__setitem__(index, new))
        return new

    def scale_volume(self, index: int, factor: float) -> Hurtbox:
        return self.edit_volume(index, radius=self._volume(index).radius * factor)

    def remove_volume(self, index: int) -> None:
        self._volume(index)
        self.doc.edit(lambda m: m.hurtboxes.__delitem__(index))

    def keep_only(self, index: int) -> int:
        """Drop every volume but `index`; returns how many went."""
        keep = self._volume(index)
        gone = len(self.m.hurtboxes) - 1
        self.doc.edit(lambda m: setattr(m, "hurtboxes", [keep]))
        return gone

    def add_volume(self, h: Hurtbox) -> int:
        h = dataclasses.replace(h, **volume_fields(h, dataclasses.asdict(h), self._FIELDS))
        self.doc.edit(lambda m: m.hurtboxes.append(h))
        return len(self.m.hurtboxes) - 1

    def add_state(
        self, name: str, rows: Sequence[Sequence[int]] | None = None, label: str = ""
    ) -> int:
        if any(s.name == name for s in self.m.hitzones):
            raise ManifestError(f"a state called {name!r} is already there")
        grid = blank_grid() if rows is None else [[int(v) for v in r] for r in rows]
        self.doc.edit(lambda m: m.hitzones.append(HitzoneState(name, grid, label)))
        return len(self.m.hitzones) - 1

    def adopt_grid(self, states: Sequence[Rows], names: Sequence[str] = ()) -> int:
        """Replace the grid with the host's: the numbers the port inherits anyway."""
        out = []
        for i, st in enumerate(states):
            if len(st.rows) != len(ROWS):
                continue
            name = names[i] if i < len(names) else STATE_NAMES[i] if i < 3 else f"state{i}"
            out.append(HitzoneState(name, [[int(v) for v in r] for r in st.rows], ADOPTED))
        self.doc.edit(lambda m: setattr(m, "hitzones", out))
        return len(out)

    def adopt_volumes(
        self,
        spheres: Iterable[HitSphere],
        source: str = "the host overlay",
        parts: Iterable[int] | None = None,
    ) -> VolumeAdoption:
        """Append the host's volumes (optionally only `parts`), labelled by part name."""
        keep = None if parts is None else {p & hitzone.PART_MASK for p in parts}
        new = [
            hurtbox_of(s, self.name_of(s.part & hitzone.PART_MASK))
            for s in spheres
            if keep is None or s.part & hitzone.PART_MASK in keep
        ]
        self.doc.edit(lambda m: m.hurtboxes.extend(new))
        off = [h.bone for h in new if not h.is_marker and not self._on_rig(h.bone)]
        return VolumeAdoption(len(new), off, source)

    def _on_rig(self, bone: int) -> bool:
        return self.n_bones is None or 0 <= bone < self.n_bones

    def _volume(self, index: int) -> Hurtbox:
        if not 0 <= index < len(self.m.hurtboxes):
            raise ManifestError(f"no volume {index}: the list has {len(self.m.hurtboxes)}")
        return self.m.hurtboxes[index]


def summarise(m: Manifest, host_states: int | None = None) -> list[str]:
    """A few lines about the port's parts, volumes and grid."""
    named = sorted(m.parts.items(), key=lambda kv: kv[1].index)
    out = [
        "parts: " + ", ".join(f"{p.index}={n}" for n, p in named)
        if named
        else "parts: none named; the engine has eight slots and no names",
        f"volumes: {len(m.hurtboxes)} authored",
    ]
    if m.hitzones:
        out.append(f"grid: {len(m.hitzones)} state(s): " + ", ".join(s.name for s in m.hitzones))
    elif host_states:
        out.append(f"grid: none authored; the port INHERITS the host's {host_states} state(s)")
    else:
        out.append("grid: none authored")
    return out
