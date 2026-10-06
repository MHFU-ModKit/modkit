# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The game's live structs as typed views: `Game` for the fixed addresses, `Entity` for monsters,
the player's combat entity and village NPCs, and the quest, its card and the NPC option menu."""

from __future__ import annotations

import math
from enum import IntEnum

from . import addresses as a
from .addresses import Struct
from .memory import Memory
from .views import View, f32, f32s, ptr, ptrs, s8, u8, u16, u16s, u32, vec3

assert a.QUEST.TARGETS.count  # an array
TARGET_GROUPS = a.QUEST.TARGETS.count  # the engine's cap
EM_ID_SPECIES = 0xFF
"""QUEST_TARGET.EM_ID holds the species in its low byte."""

ACTION_INPUT_BASE = 0x3E8
"""ENTITY.ANIM_INPUT[k] is the executor action id + ACTION_INPUT_BASE + k * ACTION_INPUT_STEP."""
ACTION_INPUT_STEP = 0xC8
EXECUTOR_VTABLES = frozenset({a.TIGREX_VTABLE})
"""Species known to animate through ACTION_EXECUTOR, so their ANIM_INPUT decodes to action ids;
the small-monster-shaped AI keeps its own ids there."""


def _named(suffix: str) -> dict[int, str]:
    table = a.table().addresses
    return {int(v): n.removesuffix(suffix) for n, v in table.items() if n.endswith(suffix)}


SPECIES_NAMES = {v: n.replace("_", " ").title() for v, n in _named("_VTABLE").items()}
"""Entity vtable -> name; the vtable tells a species apart better than ENTITY.SPECIES."""

SPECIES_IDS = {
    0x05: "Bullfango",
    0x13: "Vespoid",
    0x23: "Giaprey",
    0x3D: "Blango",
    0x45: "Anteka",
    0x46: "Popo",
    0x48: "Popo",
    0x4B: "Tigrex",
    0x4D: "Giadrome",
}
"""ENTITY.SPECIES (the em id) -> name, for the ids checked in the game."""


def input_action(value: int, slot: int) -> int:
    """The executor action id in ENTITY.ANIM_INPUT[slot]."""
    return value - ACTION_INPUT_BASE - slot * ACTION_INPUT_STEP


class Screen(IntEnum):
    """SCREEN_STATE values; the village reads BOOT too, so tell them apart by SCENE_OBJECT."""

    BOOT = 0  # logos, language menu, village
    MENU = 1  # main menu, character select, a zone load
    INTRO = 2  # attract movie the title cycles into
    TITLE = 4
    CART = 5  # the player fainted
    CUTSCENE = 6
    IN_AREA = 17
    CONTRACT = 113  # the village with a quest under contract; steady while the scene flickers


class Species(View):
    """A SPECIES_TABLE row, shared by every entity of that species on the map."""

    struct = a.SPECIES
    sight_radius = f32(a.SPECIES.SIGHT_RADIUS)
    hurtbox_set = ptr(a.SPECIES.HURTBOX_SET)
    hitzone_states = ptr(a.SPECIES.HITZONE_STATES)

    @classmethod
    def at(cls, mem: Memory, species: int) -> Species:
        return cls(mem, a.SPECIES_TABLE + species * a.SPECIES.stride)


class Entity(View):
    """A monster, the player's combat entity or a village NPC."""

    struct = a.ENTITY

    vtable = ptr(a.ENTITY.VTABLE)
    rotation = f32s(a.ENTITY.ROTATION)
    size_scale = f32(a.ENTITY.SIZE_SCALE)
    translation = vec3(a.ENTITY.TRANSLATION)
    id = u8(a.ENTITY.ID)
    species = u8(a.ENTITY.SPECIES)
    yaw = u16(a.ENTITY.YAW)
    position = vec3(a.ENTITY.POSITION)
    section = u16(a.ENTITY.SECTION)
    hp = u16(a.ENTITY.HP)
    talkable = u8(a.ENTITY.TALKABLE)
    anim_input = u16s(a.ENTITY.ANIM_INPUT)
    anim_mode = u16s(a.ENTITY.ANIM_MODE)
    hp_cap = u16(a.ENTITY.HP_CAP)
    max_hp = u16(a.ENTITY.MAX_HP)
    engage = f32(a.ENTITY.ENGAGE)
    translation_w = f32(a.ENTITY.TRANSLATION_W)
    clip = ptr(a.ENTITY.CLIP)
    action_table = ptr(a.ENTITY.ACTION_TABLE)
    render_scale = vec3(a.ENTITY.RENDER_SCALE)
    size_radius = f32(a.ENTITY.SIZE_RADIUS)
    main_state = u8(a.ENTITY.MAIN_STATE)
    sub_state = u8(a.ENTITY.SUB_STATE)
    anim_speed = u16s(a.ENTITY.ANIM_SPEED)
    hitzone_state = s8(a.ENTITY.HITZONE_STATE)
    freeze_gate = u32(a.ENTITY.FREEZE_GATE)
    action_list = ptr(a.ENTITY.ACTION_LIST)

    @property
    def xz(self) -> tuple[float, float]:
        """The ground position, from TRANSLATION."""
        x, _, z = self.translation
        return x, z

    @property
    def facing(self) -> float:
        """Radians in the game's atan2(dx, dz) convention, from the forward row of ROTATION."""
        r = self.rotation
        return math.atan2(r[8], r[10])

    @property
    def name(self) -> str:
        return SPECIES_NAMES.get(self.vtable, f"em{self.species}")

    @property
    def action(self) -> int:
        """The executor action id the body is animating."""
        return input_action(self.anim_input[0], 0)

    @property
    def slot_actions(self) -> tuple[int, ...] | None:
        """The action id per body slot; None for a species outside EXECUTOR_VTABLES."""
        if self.vtable not in EXECUTOR_VTABLES:
            return None
        return tuple(input_action(v, k) for k, v in enumerate(self.anim_input))

    def resize(self, scale: float) -> None:
        """Set the drawn size; SIZE_SCALE alone is re-derived every frame."""
        self.size_scale, self.render_scale, self.size_radius = scale, (scale,) * 3, scale


class Player(Entity):
    """The player's combat entity at PLAYER_ENTITY, in the village and in quests."""

    @property
    def loaded(self) -> bool:
        """False during loads and on menus, where the entity is not the player's."""
        return self.vtable in (a.PLAYER_ENTITY_VTABLE, a.PLAYER_QUEST_VTABLE)


class QuestTarget(View):
    struct = a.QUEST_TARGET

    record = ptr(a.QUEST_TARGET.RECORD)
    em_id = u32(a.QUEST_TARGET.EM_ID)
    count = u16(a.QUEST_TARGET.COUNT)


class QuestRecord(View):
    struct = a.QUEST_RECORD

    em_id = u16(a.QUEST_RECORD.EM_ID)
    spawn_x = f32(a.QUEST_RECORD.SPAWN_X)
    spawn_z = f32(a.QUEST_RECORD.SPAWN_Z)


class QuestListNode(View):
    struct = a.QUEST_LIST_NODE

    header = u32(a.QUEST_LIST_NODE.HEADER)
    record = u32(a.QUEST_LIST_NODE.RECORD)


class QuestData(View):
    """The parsed quest data QUEST.RECORDS points at."""

    struct = a.QUEST_DATA

    list_a = u32(a.QUEST_DATA.LIST_A)

    def monsters(self, limit: int = 64) -> list[QuestRecord]:
        """The big-monster records, in list order."""
        first, stride = self.base + self.list_a, a.QUEST_LIST_NODE.step
        out = []
        for i in range(limit):
            node = QuestListNode(self.mem, first + i * stride)
            if node.header == 0:
                break
            out.append(QuestRecord(self.mem, self.base + node.record))
        return out


class Quest(View):
    """The quest singleton; TIMER counts down only during a quest."""

    struct = a.QUEST

    timer = u32(a.QUEST.TIMER)
    records = ptr(a.QUEST.RECORDS)
    group_count = u32(a.QUEST.GROUP_COUNT)

    @property
    def targets(self) -> list[QuestTarget]:
        first, stride = self.base + a.QUEST.TARGETS, a.QUEST_TARGET.step
        return [QuestTarget(self.mem, first + i * stride) for i in range(TARGET_GROUPS)]

    @property
    def target_species(self) -> frozenset[int]:
        """Species of the big-monster target groups that hold a monster."""
        return frozenset(t.em_id & EM_ID_SPECIES for t in self.targets if t.count)

    @property
    def data(self) -> QuestData:
        return QuestData(self.mem, self.records)


def _text_limits(s: Struct, last: int) -> dict[str, int]:
    """Each text field's room: up to the next field, `last` bytes for the final one."""
    fields = sorted(s.fields.values())
    ends = [*fields[1:], None]
    return {f.name: end - f if end else last for f, end in zip(fields, ends, strict=True)}


_CARD = _text_limits(a.QUEST_CARD, 64)


class QuestCard(View):
    """The quest card being browsed, as text; rewritten on every page step."""

    struct = a.QUEST_CARD

    def _text(self, name: str) -> str:
        return self.mem.cstr(self.base + a.QUEST_CARD.fields[name], _CARD[name]).strip()

    @property
    def name(self) -> str:
        return self._text("NAME")

    @property
    def objective(self) -> str:
        return self._text("OBJECTIVE")

    @property
    def fail_condition(self) -> str:
        return self._text("FAIL_CONDITION")

    @property
    def reward(self) -> str:
        return self._text("REWARD")

    @property
    def description(self) -> str:
        return self._text("DESCRIPTION")

    @property
    def monsters(self) -> list[str]:
        return [m for m in self._text("MONSTERS").split("\n") if m]

    @property
    def client(self) -> str:
        return self._text("CLIENT")


class NpcMenu(View):
    """The option menu every NPC shares; it is not cleared when a menu closes."""

    struct = a.NPC_MENU

    player = ptr(a.NPC_MENU.PLAYER)
    partner = ptr(a.NPC_MENU.PARTNER)
    cursor = u8(a.NPC_MENU.CURSOR)
    row_count = u8(a.NPC_MENU.ROW_COUNT)


class Game(View):
    """Fixed addresses: screens, areas, menu cursors, dialogue, player stats, the registry."""

    scene = ptr(a.SCENE_OBJECT)
    screen_state = u8(a.SCREEN_STATE)
    map_subsection = u8(a.MAP_SUBSECTION)
    area_index = u16(a.AREA_INDEX)
    box_menu_highlight = u16(a.BOX_MENU_HIGHLIGHT)
    language_cursor = u8(a.LANGUAGE_CURSOR)
    quest_index = u16(a.QUEST_INDEX)
    quest_rank_cursor = u16(a.QUEST_RANK_CURSOR)
    travel_cursor = u8(a.TRAVEL_CURSOR)
    dialog_text = ptr(a.DIALOG_TEXT)
    stamina = u16(a.PLAYER_STAMINA)
    weapon_drawn = u8(a.WEAPON_DRAWN)
    map_paint = u8(a.MAP_PAINT)
    carve_count = u8(a.CARVE_COUNT)
    quest_timer_mirror = u32(a.QUEST_TIMER_MIRROR)
    registry = ptrs(a.ENTITY_REGISTRY)

    @property
    def player(self) -> Player:
        return Player(self.mem, a.PLAYER_ENTITY)

    @property
    def quest(self) -> Quest:
        return Quest(self.mem, a.QUEST_SINGLETON)

    @property
    def quest_card(self) -> QuestCard:
        return QuestCard(self.mem, a.QUEST_CARD_TEXT)

    @property
    def npc_menu(self) -> NpcMenu:
        return NpcMenu(self.mem, a.NPC_MENU_STATE)

    def species(self, species: int) -> Species:
        return Species.at(self.mem, species)

    def text(self, limit: int = 512) -> str:
        """The dialogue line on screen, whole even while it types out.

        The pointer is not cleared when a box closes, so this is the last line shown until
        another opens.
        """
        line = self.dialog_text
        return self.mem.cstr(line, limit) if line in a.RAM else ""

    def monsters(self) -> dict[int, Entity]:
        """Every entity in registry slots 1..20; the registry is sparse, so nulls are skipped."""
        slots = enumerate(self.registry)
        return {slot: Entity(self.mem, p) for slot, p in slots if slot and p in a.RAM}


# --- rig ---

SKIP_DRAW = 0x4
"""ENTITY.RENDER_FLAGS bit VISIBILITY_GATE sets on a big monster it does not draw."""
DRAW_GATE = 0x8000
"""ENTITY.FLAGS bit a big monster needs, besides ENTITY.SECTION == AREA_INDEX, to be drawn."""


class BigMonster(Entity):
    """A big monster, with the two words VISIBILITY_GATE reads and writes."""

    render_flags = u32(a.ENTITY.RENDER_FLAGS)
    flags = u32(a.ENTITY.FLAGS)

    @property
    def drawn(self) -> bool:
        """VISIBILITY_GATE draws it: it is in the player's section and has DRAW_GATE."""
        return not self.render_flags & SKIP_DRAW


# --- clips ---

STREAM_SLOTS = 100
"""Slots per animation stream: ENTITY.ANIM_INPUT value v plays slot v % STREAM_SLOTS of stream
(v - ACTION_INPUT_BASE) // STREAM_SLOTS of the pack at ENTITY.ACTION_TABLE."""


def entry_clip(entry: int, part: int) -> tuple[int, int]:
    """(stream, slot) body part `part` plays for executor entry `entry`."""
    return divmod(entry + part * ACTION_INPUT_STEP, STREAM_SLOTS)


class ClipBlock(View):
    """One body part's clip player, at ENTITY.CLIP_BLOCKS + part * CLIP_BLOCK.SIZE."""

    struct = a.CLIP_BLOCK
    phase = f32(a.CLIP_BLOCK.PHASE)
    speed = f32(a.CLIP_BLOCK.SPEED)
    loop_start = f32(a.CLIP_BLOCK.LOOP_START)
    end = f32(a.CLIP_BLOCK.END)
    node = ptr(a.CLIP_BLOCK.NODE)
    flags = u16(a.CLIP_BLOCK.FLAGS)

    @classmethod
    def of(cls, mem: Memory, entity: int, part: int) -> ClipBlock:
        assert a.CLIP_BLOCK.size
        return cls(mem, entity + a.ENTITY.CLIP_BLOCKS + part * a.CLIP_BLOCK.size)


# --- areas ---


class AreaChange(View):
    """The area-change half of MONSTER_MANAGER_SINGLETON: a pending exit and where it lands."""

    struct = a.MONSTER_MANAGER

    landing = vec3(a.MONSTER_MANAGER.LANDING)
    landing_yaw = u16(a.MONSTER_MANAGER.LANDING_YAW)
    exit = ptr(a.MONSTER_MANAGER.EXIT)
    requests = u32(a.MONSTER_MANAGER.REQUESTS)


class Hunter(View):
    """PLAYER_ENTITY's own fields."""

    struct = a.HUNTER

    exiting = u8(a.HUNTER.EXITING)
