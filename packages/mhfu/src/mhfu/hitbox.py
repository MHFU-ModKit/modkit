# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Where a big monster hits you: the attack tables, the mirror image of `hitzone`.

An action handler spawns an attack by id; the species overlay then calls
ATTACK_TABLE_SETTER(node, entity, id, handle), which copies ATTACK_RECORD[id] from the table
`handle` points at into the ATTACK_NODE, and right after the call stores
volume_table[record.VOLUME_SET] at ATTACK_NODE.VOLUME_SET. Each frame the engine walks that
HIT_VOLUME set against the hunter. Both tables are found from that call, per overlay.

The volume records are hitzone's HIT_VOLUME; the bone doubles as a coordinate space: 0x7D is a
joiner, 0x7E a capsule between the node's own two points, 0x7F a sphere at the node's own
position, which is how a projectile carries a hitbox.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rabbitizer import InstrId

from . import addresses as a
from .hitzone import SENTINEL_BONE, STRIDE, HitVolume, VolumeSet
from .memory import Memory, Unmapped
from .mips import Code, Gpr
from .overlay import Overlay
from .views import View, u8, u16, u32

RECORD: int = a.ATTACK_RECORD.step
MAX_SET = 64
"""A longer walk ran off its set."""
MAX_RADIUS = 4000.0
MAX_OFFSET = 4000.0
MAX_BONE, MAX_SHAPE, MAX_ROW, MAX_PART = 0x7F, 1, 6, 7
MIN_TABLE = 4
"""The structural volume-table search wants at least this many set pointers."""
STORE_AFTER = 16
"""Instructions after the setter call within which the volume set is stored."""

ID_OFFSETS: dict[int, dict[int, int]] = {75: {75: 0, 76: 33, 88: 70}}
"""{overlay species: {entity species: offset}}: em75's initialiser adds the offset to the
handler's attack id, one table serving three species. Other overlays are unread."""


def id_offset(overlay_species: int, entity_species: int) -> int | None:
    """Record index = handler id + this; 0 for the overlay's own species, None if unknown."""
    if entity_species == overlay_species:
        return 0
    return ID_OFFSETS.get(overlay_species, {}).get(entity_species)


class Attack(View):
    """One ATTACK_RECORD, the `index`th of its table."""

    struct = a.ATTACK_RECORD
    lead = u8(a.ATTACK_RECORD.LEAD)
    unknown_01 = u8(a.ATTACK_RECORD.UNKNOWN_01)
    power = u8(a.ATTACK_RECORD.POWER)
    kind = u8(a.ATTACK_RECORD.KIND)
    family = u8(a.ATTACK_RECORD.FAMILY)
    angle = u8(a.ATTACK_RECORD.ANGLE)
    tag = u8(a.ATTACK_RECORD.TAG)
    element = u8(a.ATTACK_RECORD.ELEMENT)
    volume_set = u8(a.ATTACK_RECORD.VOLUME_SET)
    half_0c = u16(a.ATTACK_RECORD.HALF_0C)
    value_14 = u32(a.ATTACK_RECORD.VALUE_14)

    def __init__(self, mem: Memory, base: int, index: int) -> None:
        super().__init__(mem, base)
        self.index = index

    @property
    def raw(self) -> bytes:
        return self.mem.read(self.base, RECORD)


@dataclass
class AttackTable:
    """The attack records one setter call passes, and the volume sets they index."""

    handle: int
    records: int
    volume_table: int | None
    attacks: list[Attack] = field(default_factory=list)
    volumes: list[VolumeSet] = field(default_factory=list)

    def volume_for(self, attack_id: int) -> VolumeSet | None:
        """The set that attack's node points at."""
        if not 0 <= attack_id < len(self.attacks):
            return None
        i = self.attacks[attack_id].volume_set
        return self.volumes[i] if i < len(self.volumes) else None

    @property
    def rigged(self) -> bool:
        return any(v.rigged for v in self.volumes)


def primary_table(tables: list[AttackTable]) -> AttackTable | None:
    """The overlay's moveset: the table with the most records (em75's first holds 107 against
    1, 5, 1 and 4; em54's extras are movesets too, so "the first" would be wrong)."""
    return max(tables, key=lambda t: (len(t.attacks), -t.records), default=None)


# --- the tables ---


def _mapped(v: HitVolume) -> bool:
    try:
        return len(v.raw) == STRIDE
    except Unmapped:
        return False


def _attack_volume(v: HitVolume) -> bool:
    """A record an attack volume could be made of. Marker records carry shape 1 and row 1, so
    row and part are not required to be 0."""
    if not any(v.raw):
        return False
    if v.bone > MAX_BONE or v.shape > MAX_SHAPE or v.row > MAX_ROW or v.part > MAX_PART:
        return False
    return 0.0 <= v.radius < MAX_RADIUS and all(abs(c) <= MAX_OFFSET for c in (*v.offset, *v.far))


def walk_set(ovl: Overlay, va: int) -> VolumeSet | None:
    """The set at `va`, read as the engine walks it; None where that is not a set, which makes
    this a test of a candidate pointer."""
    if va not in ovl.initialised:
        return None
    volumes: list[HitVolume] = []
    for k in range(MAX_SET + 1):
        v = HitVolume(ovl, va + k * STRIDE)
        if not _mapped(v):
            return None
        if v.bone == SENTINEL_BONE:
            return VolumeSet(va, volumes) if volumes else None
        if not _attack_volume(v):
            return None
        volumes.append(v)
    return None


def volume_table(ovl: Overlay, va: int) -> list[VolumeSet]:
    """The set pointers from `va` on, up to the first that is not one."""
    out = []
    data = ovl.initialised
    while va in data and va + 3 in data:
        s = walk_set(ovl, ovl.u32(va))
        if s is None:
            break
        out.append(s)
        va += 4
    return out


def find_volume_table(ovl: Overlay) -> int | None:
    """The longest run of set pointers in the data section: the fallback where the code
    does not give the table."""
    data = ovl.initialised
    best: tuple[int, int] | None = None
    start, n = None, 0
    for va in range(data.start, data.stop - 4, 4):
        if va + 3 in data and walk_set(ovl, ovl.u32(va)) is not None:
            if start is None:
                start, n = va, 0
            n += 1
            continue
        if start is not None and (best is None or n > best[1]):
            best = (start, n)
        start, n = None, 0
    if start is not None and (best is None or n > best[1]):
        best = (start, n)
    return best[0] if best and best[1] >= MIN_TABLE else None


def setter_calls(ovl: Overlay, code: Code | None = None) -> list[int]:
    """The overlay's calls to ATTACK_TABLE_SETTER."""
    code = code or Code(ovl, ovl.text)
    return [c.site for c in code.calls if c.target == a.ATTACK_TABLE_SETTER and c.link]


def _volume_table_va(ovl: Overlay, code: Code, call: int) -> int | None:
    """The table whose entry the overlay stores at ATTACK_NODE.VOLUME_SET after `call`."""
    for ins in code.span(range(call + 4, call + 4 * STORE_AFTER)):
        if (
            ins.uniqueId == InstrId.cpu_sw
            and ins.getProcessedImmediate() == a.ATTACK_NODE.VOLUME_SET
        ):
            load = code.source(ins.vram, ins.rt)
            table = code.table(load.vram) if load is not None and load.doesLoad() else None
            return table if table in ovl.initialised else None
    return None


def _plausible_attack(ovl: Overlay, va: int, n_volumes: int) -> bool:
    try:
        rec = Attack(ovl, va, 0)
        raw = rec.raw
    except Unmapped:
        return False
    return not any(raw) or (rec.volume_set < max(n_volumes, 1) and rec.lead == 0)


def read_attacks(
    ovl: Overlay, records: int, n_volumes: int, stop: int | None = None
) -> list[Attack]:
    """The records from `records`, up to the first that is not one, or `stop`."""
    out = []
    data = ovl.initialised
    for i in range(len(data)):
        va = records + i * RECORD
        if (stop is not None and va >= stop) or va not in data:
            break
        if not _plausible_attack(ovl, va, n_volumes):
            break
        out.append(Attack(ovl, va, i))
    return out


def tables(ovl: Overlay) -> list[AttackTable]:
    """Every attack table the overlay passes to ATTACK_TABLE_SETTER, in call order; em1 and
    em33 never call it."""
    data = ovl.initialised
    code = Code(ovl, ovl.text)
    handles: dict[int, int | None] = {}
    for call in setter_calls(ovl, code):
        h = code.constant(call, Gpr.a3)
        if h is not None and h in data and h not in handles:
            handles[h] = _volume_table_va(ovl, code, call)
    starts = sorted({ovl.u32(h) for h in handles if ovl.u32(h) in data})
    fallback: list[int | None] = []
    out = []
    for h, vt in handles.items():
        records = ovl.u32(h)
        if records not in data:
            continue
        if vt is None:
            if not fallback:
                fallback.append(find_volume_table(ovl))
            vt = fallback[0]
        volumes = volume_table(ovl, vt) if vt else []
        stop = next((r for r in starts if r > records), None)
        attacks = read_attacks(ovl, records, len(volumes), stop)
        out.append(AttackTable(h, records, vt, attacks, volumes))
    return out
