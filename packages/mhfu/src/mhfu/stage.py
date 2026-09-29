"""Stages at runtime: the stage overlay's parameter object (exits, spheres, surface table), the
map table in game_sub.ovl, and the live tables a quest builds on the heap.

A stage number is the runtime area_index. The stage FILE (`st<NNN>.pac`, its collision and
meshes) is `mhp_formats.fu.stage`'s; nothing here reads it.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import TypeVar

from . import addresses as a
from . import files
from .files import Extracted
from .memory import Image, Memory, Unmapped
from .mips import Code, Gpr
from .overlay import Overlay
from .views import View, f32, ptr, ptrs, u8, u16, u16s, u32, vec3

R = TypeVar("R", bound=View)

MAP_ROWS = 32
"""Rows in MAP_TABLE; unused rows have no stages."""
SURFACE_IDS = 8
"""A triangle's surface id is 3 bits, so a surface table has at most this many entries."""
NO_SLOT = 0xFFFF
SLOT_LIMIT = 0x40
"""STAGE_PARAMS.SLOTS entries are NO_SLOT or below this; the parameter object's signature."""
RESOURCE_SLOTS = 43
"""Rows of the resident-file table."""
FREE_FILE = 0xFFFF
"""RESOURCE_SLOT.FILE_ID of a free slot."""
CHUNK = 1 << 20
"""Bytes per read when scanning live memory."""


def variant_name(variant: int) -> str:
    """`st046_1a` .. `st046_4d`."""
    files.stage_variant_pac(variant)
    return f"st{files.STAGE_VARIANT:03d}_{variant % 4 + 1}{'abcd'[variant // 4]}"


def _mapped(mem: Memory, address: int, n: int) -> bool:
    try:
        mem.read(address, n)
    except Unmapped:
        return False
    return True


# --- the stage overlay ---


class StageParams(View):
    struct = a.STAGE_PARAMS
    surfaces = ptr(a.STAGE_PARAMS.SURFACES)
    spheres = ptr(a.STAGE_PARAMS.SPHERES)
    slots = u16s(a.STAGE_PARAMS.SLOTS)
    exits = ptr(a.STAGE_PARAMS.EXITS)
    objects_a = ptr(a.STAGE_PARAMS.OBJECTS_A)
    objects_b = ptr(a.STAGE_PARAMS.OBJECTS_B)
    env = u8(a.STAGE_PARAMS.ENV)
    exit_count = u8(a.STAGE_PARAMS.EXIT_COUNT)
    sphere_count = u8(a.STAGE_PARAMS.SPHERE_COUNT)


class Exit(View):
    struct = a.STAGE_EXIT
    target = u16(a.STAGE_EXIT.TARGET)
    flag = u16(a.STAGE_EXIT.FLAG)
    trigger = vec3(a.STAGE_EXIT.TRIGGER)
    radius = f32(a.STAGE_EXIT.RADIUS)
    height = f32(a.STAGE_EXIT.HEIGHT)
    dest = vec3(a.STAGE_EXIT.DEST)
    yaw = u16(a.STAGE_EXIT.YAW)


class Sphere(View):
    struct = a.STAGE_SPHERE
    id = u16(a.STAGE_SPHERE.ID)
    position = vec3(a.STAGE_SPHERE.POSITION)
    radius = f32(a.STAGE_SPHERE.RADIUS)
    tail = u32(a.STAGE_SPHERE.TAIL)


def _params_score(mem: Image, va: int) -> int | None:
    """How many NO_SLOT pads the object at `va` has, or None if it is no parameter object."""
    p = StageParams(mem, va)
    try:
        table, slots = p.surfaces, p.slots
    except Unmapped:
        return None
    if table not in mem or any(s != NO_SLOT and s >= SLOT_LIMIT for s in slots):
        return None
    pads = slots.count(NO_SLOT)
    return pads if pads >= 2 else None


@dataclass
class StageOverlay:
    """`stage<NNN>.ovl` as loaded, and its parameter object if it has one."""

    mem: Image
    params: StageParams | None

    @classmethod
    def parse(cls, ovl: Overlay) -> StageOverlay:
        """The parameter object is what a constant getter returns: of those, the object that
        pads the most slot ids with NO_SLOT."""
        mem, code = ovl, Code(ovl, ovl.text)
        returned = (code.constant(i.vram, Gpr.v0) for i in code if i.isReturn())
        best: tuple[int, int] | None = None
        for va in dict.fromkeys(v for v in returned if v is not None):
            score = _params_score(mem, va)
            if score is not None and (best is None or score > best[0]):
                best = (score, va)
        return cls(mem, StageParams(mem, best[1]) if best else None)

    @classmethod
    def read(cls, game: Extracted, stage: int) -> StageOverlay:
        return cls.parse(game.overlay(files.stage_overlay(stage)))

    @property
    def name(self) -> str:
        return self.mem.name

    def exits(self) -> list[Exit]:
        p = self.params
        return _records(Exit, self.mem, p.exits, p.exit_count) if p else []

    def spheres(self) -> list[Sphere]:
        p = self.params
        return _records(Sphere, self.mem, p.spheres, p.sphere_count) if p else []

    def surface_table(self, count: int | None = None) -> list[int] | None:
        """The raw surface-property masks, or None without a parameter object.

        The length is not stored: by default the table runs to the next thing the parameter
        object points at, at most SURFACE_IDS entries. Mask bits are named in
        `mhp_formats.fu.stage.SURFACE_BITS`.
        """
        p = self.params
        if not p:
            return None
        base = p.surfaces
        if count is None:
            ends = (p.base, p.spheres, p.exits, p.objects_a, p.objects_b, self.mem.end)
            count = min(SURFACE_IDS, (min(e for e in ends if e > base) - base) // 4)
        return [
            self.mem.u32(base + 4 * k) for k in range(count) if base + 4 * k + 4 <= self.mem.end
        ]


def _records(kind: type[R], mem: Image, base: int, count: int) -> list[R]:
    """`count` records from `base`, cut short where the overlay ends."""
    assert kind.struct
    stride = kind.struct.step
    out = []
    for k in range(count if base else 0):
        if not _mapped(mem, base + k * stride, stride):
            break
        out.append(kind(mem, base + k * stride))
    return out


# --- the map table ---


class MapRow(View):
    struct = a.MAP_ROW
    count = u32(a.MAP_ROW.COUNT)
    stages = ptr(a.MAP_ROW.STAGES)


def map_table(mem: Memory) -> list[tuple[int, ...]]:
    """The stages of each map row, entry area first; `mem` covers game_sub.ovl."""
    out = []
    for i in range(MAP_ROWS):
        row = MapRow(mem, a.MAP_TABLE + i * a.MAP_ROW.step)
        n = row.count
        try:
            out.append(mem.unpack(struct.Struct(f"<{n}H"), row.stages) if n else ())
        except Unmapped:
            out.append(())
    return out


def read_map_table(game: Extracted) -> list[tuple[int, ...]]:
    return map_table(game.overlay(files.GAME_SUB))


# --- live tables: resident files, the map manager, gathering spots, small-monster spawns ---


class ResourceSlot(View):
    struct = a.RESOURCE_SLOT
    flags = u16(a.RESOURCE_SLOT.FLAGS)
    file_id = u16(a.RESOURCE_SLOT.FILE_ID)
    """Engine file id; `files` ids are ENGINE_SKEW lower."""
    data = ptr(a.RESOURCE_SLOT.DATA)


def resident_files(mem: Memory) -> dict[int, ResourceSlot]:
    """The loaded files by extracted file id."""
    base = mem.u32(a.RESOURCE_TABLE)
    if not base:
        return {}
    out = {}
    for k in range(RESOURCE_SLOTS):
        slot = ResourceSlot(mem, base + k * a.RESOURCE_SLOT.step)
        if slot.file_id != FREE_FILE and slot.data:
            out[slot.file_id - files.ENGINE_SKEW] = slot
    return out


class MapManager(View):
    struct = a.MAP_MANAGER
    row = u32(a.MAP_MANAGER.ROW)
    stage = u16(a.MAP_MANAGER.STAGE)


def map_manager(mem: Memory) -> MapManager:
    return MapManager(mem, mem.u32(a.MAP_MANAGER_PTR))


class Registry(View):
    slots = ptrs(a.ENTITY_REGISTRY)
    """Entity pointers; a spawn's SMALL_SPAWN.ENTITY is its slot minus one."""


USER_RAM = range(a.USER_RAM, a.USER_RAM_END)
WORLD = (-3000.0, 30000.0)
"""x and z of any map lie inside this."""
SPOT_GROUP = 5
"""Four spots, then a terminator record."""
SPOT_PROBE = 2 * SPOT_GROUP
"""Records that must fit the pattern before a run counts as the spot table."""
SPOT_END_X = -1.0
MAX_SPOT_RADIUS = 3000.0


class GatherSpot(View):
    struct = a.GATHER_SPOT
    position = vec3(a.GATHER_SPOT.POSITION)
    radius = f32(a.GATHER_SPOT.RADIUS)
    id = u16(a.GATHER_SPOT.ID)
    uses = u16(a.GATHER_SPOT.USES)
    tool = u16(a.GATHER_SPOT.TOOL)
    uses_m = u16(a.GATHER_SPOT.USES_M)

    @property
    def kind(self) -> str | None:
        """'end', 'unused', 'spot', or None for no spot record."""
        (x, y, z), r = self.position, self.radius
        if not all(_finite(v) for v in (x, y, z, r)):
            return None
        if x == SPOT_END_X and r == 0.0:
            return "end"
        if (x, y, z, r) == (0.0, 0.0, 0.0, 0.0):
            return "unused"
        if 0.0 < r <= MAX_SPOT_RADIUS and _in_world(x) and _in_world(z):
            return "spot"
        return None


def _finite(v: float) -> bool:
    return math.isfinite(v) and abs(v) <= 1e30


def _in_world(v: float) -> bool:
    return WORLD[0] < v < WORLD[1]


def snapshot(mem: Memory, region: range = USER_RAM) -> Image:
    """`region` of `mem`, read once in CHUNK-sized pieces."""
    data = bytearray()
    for at in range(region.start, region.stop, CHUNK):
        data += mem.read(at, min(CHUNK, region.stop - at))
    return Image(bytes(data), region.start)


def _spot_fits(spot: GatherSpot, k: int) -> bool:
    kind = spot.kind
    if k % SPOT_GROUP == SPOT_GROUP - 1:
        return kind == "end"
    return kind in ("spot", "unused") and spot.id == k // SPOT_GROUP * 4 + k % SPOT_GROUP


def find_spots(image: Image) -> list[GatherSpot]:
    """The gathering-spot table in `image` (a `snapshot`), terminators left out.

    Anchored on the id sequence 0, 1, 2, 3, end, 4, ...: the table has no static pointer.
    """
    stride = a.GATHER_SPOT.step
    end_x = struct.pack("<f", SPOT_END_X)
    at = image.data.find(end_x)
    while at >= 0:
        start = at - (SPOT_GROUP - 1) * stride
        if start >= 0 and start % 4 == 0 and start + SPOT_PROBE * stride < len(image.data):
            base = image.base + start
            if all(_spot_fits(GatherSpot(image, base + k * stride), k) for k in range(SPOT_PROBE)):
                out, k = [], 0
                while _mapped(image, base + k * stride, stride):
                    spot = GatherSpot(image, base + k * stride)
                    if not _spot_fits(spot, k):
                        break
                    if spot.kind != "end":
                        out.append(spot)
                    k += 1
                return out
        at = image.data.find(end_x, at + 1)
    return []


SPAWN_KIND = 2
SPAWN_MARK = 100
MAX_SPAWN_INDEX = 0x200
MAX_SPAWN_HP = 3000
NO_HP = 0xFFFF
NO_ENTITY = 0xFF
SPAWN_HEIGHT = (-3000.0, 10000.0)


class SmallSpawn(View):
    struct = a.SMALL_SPAWN
    seed = u32(a.SMALL_SPAWN.SEED)
    position = vec3(a.SMALL_SPAWN.POSITION)
    index = u16(a.SMALL_SPAWN.INDEX)
    kind = u16(a.SMALL_SPAWN.KIND)
    mark = u16(a.SMALL_SPAWN.MARK)
    hp = u16(a.SMALL_SPAWN.HP)
    entity = u16(a.SMALL_SPAWN.ENTITY)

    @property
    def here(self) -> bool:
        """The record belongs to the area the hunter is in."""
        return not (self.hp == NO_HP and self.entity == NO_ENTITY)

    def valid(self) -> bool:
        x, y, z = self.position
        return (
            self.kind == SPAWN_KIND
            and self.mark == SPAWN_MARK
            and self.index <= MAX_SPAWN_INDEX
            and self.seed != 0
            and all(_finite(v) for v in (x, y, z))
            and abs(x) > 1.0
            and abs(z) > 1.0
            and (self.hp == NO_HP or self.hp < MAX_SPAWN_HP)
            and _in_world(x)
            and _in_world(z)
            and SPAWN_HEIGHT[0] < y < SPAWN_HEIGHT[1]
        )


def find_spawns(image: Image) -> list[SmallSpawn]:
    """Every small-monster spawn record in `image` (a `snapshot`), by address.

    The table is rebuilt per area, and records of areas left behind linger with `here` false.
    """
    stride, at_kind = a.SMALL_SPAWN.step, a.SMALL_SPAWN.KIND
    marker = struct.pack("<HH", SPAWN_KIND, SPAWN_MARK)
    out = []
    at = image.data.find(marker)
    while at >= 0:
        start = at - at_kind
        if start >= 0 and start % 4 == 0 and start + stride < len(image.data):
            spawn = SmallSpawn(image, image.base + start)
            if spawn.valid():
                out.append(spawn)
        at = image.data.find(marker, at + 1)
    return out
