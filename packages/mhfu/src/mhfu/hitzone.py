# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Where a big monster can be hit, and for how much: the part system.

Two tables joined by one number. The species overlay (`emNN.ovl`) holds sets of HIT_VOLUME
spheres and capsules on bones, each with a damage `part` and a `hitzone_row`; game_task.ovl's
species row points at the set the species walks and at its hitzone grids, seven rows of ten
damage-type percentages, one grid per ENTITY.HITZONE_STATE. A hit resolves sphere -> row ->
column for the damage type; separately sphere -> part -> ENTITY.PENDING_DAMAGE[part].

Read through a `Space` of game_task.ovl and the em overlay (`space`), or the live game.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import addresses as a
from . import files
from .files import Extracted
from .memory import Memory, Space, Unmapped
from .overlay import Overlay
from .structs import Species
from .views import View, f32, u8s, u16, u32, vec3

SPECIES_IDS = range(0x100)

SENTINEL_BONE = 0xFFFF
"""Ends a set: the engine walks records until this bone."""
MARKER_BONES = (0x7D, 0x7E, 0x7F)
"""Bones that are coordinate spaces, not joints: a joiner, the node's own capsule, the node's
own sphere (see `hitbox`)."""
SPHERE, CAPSULE = 0, 1
MAX_BONE = 0x7F
MAX_ROW = 6
MAX_PART = 7
PART_MASK = 7
"""The damage deposit uses `part & PART_MASK`."""
MAX_RADIUS = 1000.0
MAX_OFFSET = 4000.0
MIN_SET = 3
"""Shorter runs are more likely float arrays that happen to validate."""
MAX_WALK = 512
"""A walk longer than this ran off the set."""

HURTBOX, VOLUME, UNKNOWN = "hurtbox", "volume", "unknown"
"""What a structurally found set is: parts and rows (a hurtbox), neither (an attack volume,
see `hitbox`), or nothing a hit could use."""

GRID_ROWS, GRID_COLS = 7, 10
MAX_STATES = 8
"""A walk bound for the grid pointer table; the game uses at most three."""

COLUMNS = ("raw", "cut", "impact", "shot", "fire", "water", "dragon", "thunder", "ice", "ko")
COLUMN_PROVENANCE = {
    "raw": "unnamed: the damage path reads no column 0",
    "cut": "read by the damage path, scaled by the weapon's cut share",
    "impact": "read by the damage path, scaled by the blunt share; the larger product wins",
    "shot": "read by the damage path",
    "fire": "inferred from the element bit; the native Tigrex's lowest element",
    "water": "inferred from the element bit alone",
    "dragon": "inferred from the element bit alone",
    "thunder": "inferred from the element bit; the native Tigrex's highest element",
    "ice": "inferred from the element bit alone",
    "ko": "inferred: nonzero on about one row per grid, scaled by the node's stun value",
}
ELEMENT_BITS = {"fire": 0x10, "water": 0x20, "dragon": 0x80, "thunder": 0x40, "ice": 0x100}
"""Element column -> the attack node's element-mask bit that gates it; not in bit order."""


class HitVolume(View):
    """One sphere at `offset`, or capsule from `offset` to `far`, bone-relative."""

    struct = a.HIT_VOLUME
    bone = u16(a.HIT_VOLUME.BONE)
    shape = u16(a.HIT_VOLUME.SHAPE)
    row = u16(a.HIT_VOLUME.HITZONE_ROW)
    part = u16(a.HIT_VOLUME.PART)
    flags = u32(a.HIT_VOLUME.FLAGS)
    radius = f32(a.HIT_VOLUME.RADIUS)
    offset = vec3(a.HIT_VOLUME.OFFSET_A)
    far = vec3(a.HIT_VOLUME.OFFSET_B)

    @property
    def capsule(self) -> bool:
        return self.shape == CAPSULE

    @property
    def raw(self) -> bytes:
        return self.mem.read(self.base, a.HIT_VOLUME.step)


STRIDE: int = a.HIT_VOLUME.step


@dataclass
class VolumeSet:
    """HIT_VOLUME records from `va` up to (not including) the sentinel."""

    va: int
    volumes: list[HitVolume] = field(default_factory=list)
    kind: str = HURTBOX

    @property
    def parts(self) -> list[int]:
        return sorted({v.part & PART_MASK for v in self.volumes})

    @property
    def rows(self) -> list[int]:
        return sorted({v.row for v in self.volumes})

    @property
    def bones(self) -> list[int]:
        return sorted({v.bone for v in self.volumes})

    @property
    def rigged(self) -> bool:
        """Some record sits on a real joint rather than a marker bone."""
        return any(v.bone not in MARKER_BONES for v in self.volumes)


def sentinel(mem: Memory, va: int) -> bool:
    return mem.u16(va + a.HIT_VOLUME.BONE) == SENTINEL_BONE


def _plausible(v: HitVolume) -> bool:
    """A record, or a sentinel, as far as its values go."""
    if (v.bone, v.shape, v.row, v.part) == (SENTINEL_BONE,) * 4:
        return True
    if v.bone > MAX_BONE or v.shape > CAPSULE or v.row > MAX_ROW or v.part > MAX_PART:
        return False
    return 0.0 <= v.radius < MAX_RADIUS and all(abs(c) <= MAX_OFFSET for c in (*v.offset, *v.far))


def _member(ovl: Overlay, va: int) -> bool:
    if va < ovl.base or va + STRIDE > ovl.end:
        return False
    return _plausible(HitVolume(ovl, va)) and any(ovl.read(va, STRIDE))


def find_sets(ovl: Overlay) -> list[VolumeSet]:
    """Every set in the overlay's data section, found by shape.

    Nothing in the overlay points at its sets, so: take every maximal run of plausible records
    at every 4-byte phase, keep the longest where runs overlap, cut them at the sentinels.
    `own_set` follows the species row instead and is the authority.
    """
    data = range(ovl.initialised.start, min(ovl.initialised.stop, ovl.end))
    runs = []
    for va in range(data.start, data.stop - STRIDE, 4):
        if not _member(ovl, va) or _member(ovl, va - STRIDE):
            continue
        n = 0
        while _member(ovl, va + n * STRIDE):
            n += 1
        if n >= MIN_SET:
            runs.append((va, n))
    taken: list[tuple[int, int]] = []
    for start, n in sorted(runs, key=lambda r: (-r[1], r[0])):
        end = start + n * STRIDE
        if not any(start < e and s < end for s, e in taken):
            taken.append((start, end))
    sets: list[VolumeSet] = []
    for start, end in sorted(taken):
        base, cur = start, list[HitVolume]()
        for va in range(start, end, STRIDE):
            if sentinel(ovl, va):
                _emit(sets, base, cur)
                base, cur = va + STRIDE, []
            else:
                cur.append(HitVolume(ovl, va))
        _emit(sets, base, cur)
    return sets


def _emit(sets: list[VolumeSet], va: int, volumes: list[HitVolume]) -> None:
    if len(volumes) >= MIN_SET and any(v.radius > 0.0 for v in volumes):
        sets.append(VolumeSet(va, volumes, _classify(volumes)))


def _classify(volumes: list[HitVolume]) -> str:
    if len({v.bone for v in volumes}) < 2:
        return UNKNOWN
    if len({v.part & PART_MASK for v in volumes}) > 1 or len({v.row for v in volumes}) > 1:
        return HURTBOX
    if all(v.part == 0 and v.row == 0 and v.offset == (0.0, 0.0, 0.0) for v in volumes):
        return VOLUME
    return UNKNOWN


def walk_set(mem: Memory, va: int, limit: int = MAX_WALK) -> VolumeSet | None:
    """The set at `va`, read as the engine does: records up to the sentinel, no checks.
    None if it leaves `mem` or finds no sentinel within `limit` records."""
    volumes: list[HitVolume] = []
    try:
        for k in range(limit):
            at = va + k * STRIDE
            mem.read(at, STRIDE)
            if sentinel(mem, at):
                return VolumeSet(va, volumes)
            volumes.append(HitVolume(mem, at))
    except Unmapped:
        pass
    return None


# --- through the species row ---


def space(game: Extracted, em: int) -> Space:
    """game_task.ovl and the overlay of species `em`, as the game has them in one quest."""
    return Space([game.overlay(files.GAME_TASK), game.em(em)])


def _data(ovl: Overlay) -> range:
    return range(ovl.initialised.start, min(ovl.initialised.stop, ovl.end))


def own_set(mem: Memory, ovl: Overlay, n: int) -> VolumeSet | None:
    """The set species `n` walks, from its row's pointer; None unless it points into `ovl`'s
    data. Its record count is the capacity for an in-place replacement."""
    try:
        va = Species.at(mem, n).hurtbox_set
    except Unmapped:
        return None
    return walk_set(mem, va) if va in _data(ovl) else None


def species_sets(mem: Memory, ovl: Overlay) -> dict[int, int]:
    """`{set_va: species}` for every species whose row points into `ovl`; one overlay can serve
    several species ids (em75: 75, 76, 81 and 88)."""
    out: dict[int, int] = {}
    for n in SPECIES_IDS:
        try:
            va = Species.at(mem, n).hurtbox_set
        except Unmapped:
            continue
        if va in _data(ovl):
            out.setdefault(va, n)
    return out


class HitzoneGrid(View):
    struct = a.HITZONE_GRID
    percent = u8s(a.HITZONE_GRID.PERCENT)
    pad = u8s(a.HITZONE_GRID.PAD)

    @property
    def rows(self) -> list[tuple[int, ...]]:
        p = self.percent
        return [p[r * GRID_COLS : (r + 1) * GRID_COLS] for r in range(GRID_ROWS)]


@dataclass
class Hitzones:
    """A species' grids, one per hitzone state."""

    species: int
    row: int
    states_va: int
    states: list[HitzoneGrid]


def species_hitzones(mem: Memory, n: int) -> Hitzones | None:
    """Species `n`'s grids, or None without them. The state count is not stored: the pointer
    table follows the last grid, so the grids are the contiguous run before it."""
    row = Species.at(mem, n)
    grid = a.HITZONE_GRID.step
    try:
        mem.read(row.base, a.SPECIES.stride)
        table = row.hitzone_states
        mem.read(table, 4)
    except Unmapped:
        return None
    grids = []
    for i in range(MAX_STATES):
        try:
            va = mem.u32(table + 4 * i)
            mem.read(va, grid)
        except Unmapped:
            break
        if va % 4 or va >= table:
            break
        grids.append(va)
    if not grids or grids != [grids[0] + i * grid for i in range(len(grids))]:
        return None
    return Hitzones(n, row.base, table, [HitzoneGrid(mem, va) for va in grids])


def all_hitzones(mem: Memory) -> dict[int, Hitzones]:
    out = {}
    for n in SPECIES_IDS:
        hz = species_hitzones(mem, n)
        if hz is not None:
            out[n] = hz
    return out


def owner(game: Extracted, n: int) -> int:
    """The em overlay species whose data holds species `n`'s set (Tigrex 76 -> 75)."""
    task = game.overlay(files.GAME_TASK)
    for em in files.EM_SPECIES:
        ovl = game.em(em)
        if own_set(Space([task, ovl]), ovl, n) is not None:
            return em
    raise ValueError(f"no big-monster overlay holds species {n}'s hit volumes")
