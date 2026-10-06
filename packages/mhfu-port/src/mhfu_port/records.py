# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Which donor bone each MHP3rd animation record drives.

Records are not positional: record i drives the i-th bone counted from a per-monster offset,
stepping over the bones the moveset never drives (the rule of Kurogami2134/blender_p3rd_anim).
The offset follows the fork rule: every record with location channels lands at or above the
body fork, else it lifts one half of the animal off the other (`loc_below_fork`).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from mhp_formats.anim import Clip

SKIPPED: dict[int, tuple[int, ...]] = {
    1: (8, 15, 25),
    2: (8, 15, 25),
    7: (21, 22),
    15: (8, 15, 25),
    16: (22, 23, 24),
    18: (8, 15, 25),
    22: (8,),
    40: (9, 10, 16, 17, 24, 31, 37, 38, 42, 47),
    41: (8, 9, 15, 16, 25, 27, 30, 31, 32, 34, 43, 48),
    47: (17,),
    56: (22, 23, 24),
}
"""Bones the moveset does not drive, by em id (blender_p3rd_anim's skipped_bones.md)."""

OFFSET: dict[int, int] = {40: 0, 58: 0}
"""The first driven bone, by em id, pinned with the fork rule (both rigs fork at bone 1)."""

DEFAULT_OFFSET = 2
"""blender_p3rd_anim's default; wrong for every monster in OFFSET, so pin a new one."""

EM_BY_MODEL: dict[int, int] = {5248: 58, 5339: 40}
"""em id by MHP3rd model PAC file. The stride varies, so this is a table; the two files before
a model PAC are its AI overlays, whose header names the em id."""

GEO, ANIM = 1, 2
"""A donor's geometry and moveset (`emNNN`) files follow its model PAC file by these."""


def em_for_model(file_id: int) -> int | None:
    """The em id of an MHP3rd model PAC, None where it is not mapped."""
    return EM_BY_MODEL.get(file_id)


def record_to_bone(
    n_records: int, n_bones: int, offset: int, skip: Iterable[int]
) -> dict[int, int]:
    """`{record: bone}`, walking the bones from `offset` and stepping over `skip`."""
    skipped = set(skip)
    out: dict[int, int] = {}
    bone = offset
    for record in range(n_records):
        while bone in skipped:
            bone += 1
        if bone >= n_bones:
            break
        out[record] = bone
        bone += 1
    return out


def bone_to_record(
    n_records: int, n_bones: int, offset: int, skip: Iterable[int]
) -> dict[int, int]:
    """`{bone: record}`; a bone missing from it is undriven and stays at rest."""
    return {b: r for r, b in record_to_bone(n_records, n_bones, offset, skip).items()}


def for_monster(
    em: int | None,
    n_records: int,
    n_bones: int,
    offset: int | None = None,
    skip: Sequence[int] | None = None,
) -> dict[int, int]:
    """`{bone: record}` for an em id; `offset` and `skip` override its table rows."""
    if offset is None:
        offset = OFFSET.get(em, DEFAULT_OFFSET) if em is not None else DEFAULT_OFFSET
    if skip is None:
        skip = SKIPPED.get(em, ()) if em is not None else ()
    return bone_to_record(n_records, n_bones, offset, skip)


def for_moveset(
    clips: Iterable[Clip | None],
    em: int | None,
    n_bones: int,
    offset: int | None = None,
    skip: Sequence[int] | None = None,
) -> dict[int, int]:
    """`for_monster` on a moveset's record count, which every clip must share."""
    counts = {len(c.tracks) for c in clips if c is not None}
    if len(counts) != 1:
        raise ValueError(f"the moveset's clips disagree on their record count: {sorted(counts)}")
    return for_monster(em, counts.pop(), n_bones, offset, skip)


def body_fork(parents: Sequence[int]) -> int:
    """The lowest joint with more than one child, where the rig splits front from rear."""
    kids: dict[int, int] = {}
    for p in parents:
        if p >= 0:
            kids[p] = kids.get(p, 0) + 1
    return min((j for j, n in kids.items() if n > 1), default=0)


def loc_below_fork(parents: Sequence[int], loc_bones: Iterable[int]) -> list[int]:
    """The bones of `loc_bones` below the body fork, which translate only one half of the
    animal; empty when the mapping passes the fork rule."""
    fork = body_fork(parents)
    above = _ancestors(parents, fork)
    return sorted(b for b in loc_bones if b >= 0 and b not in above)


def _ancestors(parents: Sequence[int], j: int) -> set[int]:
    """`j` and every joint above it."""
    out: set[int] = set()
    while j >= 0 and j not in out:
        out.add(j)
        j = parents[j] if j < len(parents) else -1
    return out
