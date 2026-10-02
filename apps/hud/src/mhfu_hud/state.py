"""One poll of game state: the reader thread builds a GameSnapshot, the window draws it.

Every class here is frozen and a snapshot is never changed once published, so the two threads
share it without a lock.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum

from mhfu.addresses import Field


@dataclass(frozen=True)
class Vec3:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def __repr__(self) -> str:
        return f"({self.x:.1f}, {self.y:.1f}, {self.z:.1f})"


class Context(Enum):
    """Which layout the game state calls for."""

    DISCONNECTED = "disconnected"
    BOOT = "boot"  # connected: no game, or the boot logos
    MENU = "menu"  # title, main menu, language menu
    LOADING = "loading"  # a zone load with the player still in memory
    VILLAGE = "village"  # village, hall, house
    QUEST = "quest"


CellValue = int | float | tuple[int, ...] | tuple[float, ...]


@dataclass(frozen=True)
class Cell:
    """A struct field and its value at the poll; the label comes from `field`."""

    field: Field
    value: CellValue


@dataclass(frozen=True)
class SpeciesRow:
    """A SPECIES_TABLE row, read once per species and quest."""

    species: int
    address: int
    cells: tuple[Cell, ...]


@dataclass(frozen=True)
class BagSlot:
    """A PLAYER_BAG slot: {u16 item, u8 count, u8 flags}."""

    idx: int
    item_id: int
    count: int
    flags: int

    @property
    def empty(self) -> bool:
        return self.item_id == 0 and self.count == 0


@dataclass(frozen=True)
class MonsterHUD:
    """An entity in the registry."""

    slot: int
    ptr: int
    vtable: int
    species: int
    entity_id: int
    name: str
    icon_slug: str | None
    big: bool
    """Its species is a quest target."""
    pos: Vec3
    hp: int
    hp_max: int
    render_scale: float | None
    """RENDER_SCALE x, the size the edits write; None when implausible."""
    drawn: bool
    """DRAW_NODE is set: in the view this frame."""
    anim_input: tuple[int, ...]
    actions: tuple[int, ...] | None
    """Executor action id per body slot; None for a species that does not decode."""
    state: tuple[int, int]
    """(MAIN_STATE, SUB_STATE), the move a big monster is in."""
    cells: tuple[Cell, ...] = ()
    """The AI cells AI_MOD lists."""
    herd: tuple[int, ...] = ()
    """HERD_MEMBERS that are set; small monsters only."""


@dataclass(frozen=True)
class PlayerHUD:
    loaded: bool = False
    pos: Vec3 = field(default_factory=Vec3)
    facing_rad: float | None = None
    hp: int | None = None
    hp_recov: int | None = None
    """HP_CAP, the red bar's ceiling."""
    hp_max: int = 0
    stamina: int | None = None
    stamina_max: int = 0
    weapon_drawn: bool | None = None
    sharpness: int | None = None
    sharpness_max: int | None = None
    sharpness_tier: int | None = None
    bag: tuple[BagSlot, ...] = ()


@dataclass(frozen=True)
class GameSnapshot:
    connected: bool = False
    status_text: str = "starting..."
    game_title: str = ""
    poll_latency_ms: float = 0.0
    poll_count: int = 0
    timestamp: float = 0.0
    screen_state: int = -1
    map_subsection: int = -1
    area_index: int = -1
    scene: int = 0
    context: Context = Context.DISCONNECTED
    tracked_section: int | None = None
    tracked_section_source: str = "init"
    """area_index, transition, override or init."""
    learnt_sections: Mapping[int, int | None] = field(default_factory=dict)
    """area_index -> section pairs the reader learnt this run, for the window to save."""
    quest_timer_frames: int = 0
    carve_count: int = 0
    player: PlayerHUD = field(default_factory=PlayerHUD)
    monsters: tuple[MonsterHUD, ...] = ()
    species_rows: Mapping[int, SpeciesRow] = field(default_factory=dict)
    """Rows of the species seen this quest, by species id."""
