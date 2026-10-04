# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The poller thread: a few batched reads per cycle, parsed through mhfu views into a snapshot.

PPSSPP stops the CPU for every debugger read (about 10 ms), so a poll reads a handful of
regions sized from the fields it uses, and the views read those copies (`Image`, `Space`):
globals, area index and the player always; in a quest also the quest, the carve count and the
registry, one read per monster, and one per species the first time it is seen.
"""

from __future__ import annotations

import math
import queue
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import replace
from typing import Any, TypeVar

from mhfu import addresses as a
from mhfu.addresses import Field
from mhfu.memory import Image, Space
from mhfu.structs import (
    TARGET_GROUPS,
    Entity,
    Game,
    Player,
    Quest,
    QuestTarget,
    Screen,
    Species,
)
from mhfu.views import Value, View, f32s, ptr, ptrs, u8, u16, u16s, u32, u32s, vec3
from ppsspp_debug import Client, DebuggerError

from .calibration import SectionMap
from .monster_db import identify
from .state import (
    BagSlot,
    Cell,
    Context,
    GameSnapshot,
    MonsterHUD,
    PlayerHUD,
    SpeciesRow,
    Vec3,
)

T = TypeVar("T")

DEBUGGER_ERRORS = (DebuggerError, ConnectionError, TimeoutError)
QUEST_MAP = "snowy_mountains"
"""The only map with anchors; the tracker assumes it, as the map panel does."""
SNAP_DIST = 1500.0
"""Entry points land within ~30 units of their anchor, so this is generous."""
SETTLE_S = 0.6
"""After a gate load, wait this long before trusting the player to stand on an entry point."""
STATUS_EVERY = 20
"""Polls between game.status checks; it is slow and rarely changes."""

HP_MAX_FALLBACK = 150
STAMINA_MAX_FALLBACK = 320
HP_MAX_SANE = 1000
"""Above this a player cell holds zone-load garbage, which must not latch into a running max."""
STAMINA_MAX_SANE = 2000
SHARPNESS_SANE = 4000
SHARPNESS_TIERS = 7
SCALE_SANE = (0.05, 10.0)
WORLD_SANE = 1e7


class HudGame(Game):
    """Game plus the player stats Game has no descriptor for."""

    bag = u32s(a.PLAYER_BAG)
    sharpness = u16(a.SHARPNESS)
    sharpness_max = u16(a.SHARPNESS_MAX)
    sharpness_tier = u8(a.SHARPNESS_TIER)


class HudEntity(Entity):
    """Entity plus the AI cells the inspector lists."""

    draw_node = ptr(a.ENTITY.DRAW_NODE)
    frame_counter = u8(a.ENTITY.FRAME_COUNTER)
    clip_flags = u16(a.ENTITY.CLIP_FLAGS)
    slot_count = u16(a.ENTITY.SLOT_COUNT)
    slot_inputs = u16s(a.ENTITY.SLOT_INPUTS)
    phase = u8(a.ENTITY.PHASE)
    target_acquired = u8(a.ENTITY.TARGET_ACQUIRED)
    target = ptr(a.ENTITY.TARGET)
    stimulus_tag = u16(a.ENTITY.STIMULUS_TAG)
    condition_flags = u32(a.ENTITY.CONDITION_FLAGS)
    action_budget = u32(a.ENTITY.ACTION_BUDGET)
    prev_main = u8(a.ENTITY.PREV_MAIN)
    prev_sub = u8(a.ENTITY.PREV_SUB)
    ai_timer_a = u16(a.ENTITY.AI_TIMER_A)
    ai_timer_b = u16(a.ENTITY.AI_TIMER_B)
    stress = u16(a.ENTITY.STRESS)
    flags = u32(a.ENTITY.FLAGS)
    flee = u8(a.ENTITY.FLEE)
    herd_rally = vec3(a.ENTITY.HERD_RALLY)
    herd_members = ptrs(a.ENTITY.HERD_MEMBERS)


class HudSpecies(Species):
    """Species plus every field the species panel lists."""

    cooldowns = ptr(a.SPECIES.COOLDOWNS)
    weights = ptr(a.SPECIES.WEIGHTS)
    action_list = ptr(a.SPECIES.ACTION_LIST)
    range_params = f32s(a.SPECIES.RANGE_PARAMS)
    scalars = f32s(a.SPECIES.SCALARS)
    attack_patterns = ptr(a.SPECIES.ATTACK_PATTERNS)


E, S, G = HudEntity, HudSpecies, HudGame
AI_CELLS: tuple[Value[Any], ...] = (
    E.main_state,
    E.sub_state,
    E.phase,
    E.prev_main,
    E.prev_sub,
    E.anim_input,
    E.anim_mode,
    E.anim_speed,
    E.slot_inputs,
    E.slot_count,
    E.clip,
    E.clip_flags,
    E.frame_counter,
    E.action_table,
    E.action_list,
    E.freeze_gate,
    E.flags,
    E.condition_flags,
    E.action_budget,
    E.stimulus_tag,
    E.ai_timer_a,
    E.ai_timer_b,
    E.stress,
    E.engage,
    E.target,
    E.target_acquired,
    E.section,
    E.yaw,
    E.hitzone_state,
    E.flee,
    E.herd_rally,
)
"""The entity cells AI_MOD lists, in its order."""
SMALL_ONLY = "small monsters:"
"""How addresses.toml marks an ENTITY field only small monsters fill."""
BIG_CELLS = tuple(v for v in AI_CELLS if not v.where.doc.startswith(SMALL_ONLY))
SPECIES_CELLS: tuple[Value[Any], ...] = (
    S.cooldowns,
    S.weights,
    S.action_list,
    S.attack_patterns,
    S.sight_radius,
    S.hurtbox_set,
    S.hitzone_states,
    S.range_params,
    S.scalars,
)


def get(view: View, value: Value[T]) -> T:
    """`value` read from `view`, for a field picked at run time."""
    return value.__get__(view, type(view))


def cells(view: View, values: Iterable[Value[Any]]) -> tuple[Cell, ...]:
    out = []
    for v in values:
        assert isinstance(v.where, Field)
        out.append(Cell(v.where, get(view, v)))
    return tuple(out)


def _fields(view: type[View]) -> list[Value[Any]]:
    return [v for c in view.__mro__ for v in vars(c).values() if isinstance(v, Value)]


def region(fields: Iterable[tuple[int, Value[Any]]]) -> tuple[int, int]:
    """(start, size) of the bytes holding every (base, field)."""
    spans = [(base + v.where, base + v.where + v.format.size) for base, v in fields]
    lo = min(s for s, _ in spans)
    return lo, max(e for _, e in spans) - lo


GLOBALS = region((0, v) for v in (G.scene, G.screen_state, G.stamina, G.map_subsection))
AREA = region([(0, G.area_index)])
PLAYER = region(
    [(a.PLAYER_ENTITY, v) for v in (Player.vtable, Player.rotation, Player.position, Player.hp)]
    + [(a.PLAYER_ENTITY, v) for v in (Player.hp_cap, Player.max_hp)]
    + [(0, v) for v in (G.bag, G.sharpness, G.sharpness_max, G.sharpness_tier, G.weapon_drawn)]
)
"""PLAYER_ENTITY through SHARPNESS: the entity, the bag and the weapon stats in one read."""
QUEST = region(
    [(a.QUEST_SINGLETON, Quest.timer)]
    + [
        (a.QUEST_SINGLETON + a.QUEST.TARGETS + k * a.QUEST_TARGET.step, v)
        for k in range(TARGET_GROUPS)
        for v in (QuestTarget.em_id, QuestTarget.count)
    ]
)
CARVE = region([(0, G.carve_count)])
REGISTRY = region([(0, G.registry)])
ENTITY = region((0, v) for v in _fields(HudEntity))
"""Every HudEntity field, through the herd table at the far end."""
SPECIES_ROW = region((0, v) for v in _fields(HudSpecies))


def classify(screen: int, scene: int, player_vtable: int) -> Context:
    """The layout for a SCREEN_STATE, scene and player vtable.

    The player's vtable says village or quest (the village reads BOOT like the logos); MENU with
    the player in memory is a zone load.
    """
    if player_vtable in (a.PLAYER_ENTITY_VTABLE, a.PLAYER_QUEST_VTABLE):
        if screen == Screen.MENU:
            return Context.LOADING
        return Context.QUEST if player_vtable == a.PLAYER_QUEST_VTABLE else Context.VILLAGE
    if screen == Screen.BOOT and scene != a.SCENE_LANGUAGE_MENU:
        return Context.BOOT
    return Context.MENU


def _vec(v: Sequence[float]) -> Vec3:
    x, y, z = v
    return Vec3(x, y, z)


def _finite(v: Vec3) -> bool:
    return all(math.isfinite(c) and abs(c) < WORLD_SANE for c in (v.x, v.y, v.z))


class SectionTracker:
    """The labelled section the player is in: AREA_INDEX looked up in the known pairs, else
    the nearest entry-point anchor once a gate load has settled, which learns the pair."""

    def __init__(self, sections: SectionMap) -> None:
        self.sections = sections
        self.learnt: dict[int, int | None] = {}
        self.section: int | None = None
        self.source = "init"
        self._last_screen = -1
        self._settle_at = 0.0
        self._initial = True

    def override(self, section: int | None) -> None:
        self.section, self.source = section, "override"
        self._initial, self._settle_at = False, 0.0

    def reset(self) -> None:
        self.section, self.source = None, "init"
        self._initial, self._settle_at = True, 0.0

    def _lookup(self, area_index: int) -> tuple[int | None, bool]:
        if area_index in self.learnt:
            return self.learnt[area_index], True
        return self.sections.lookup(area_index)

    def update(
        self, context: Context, screen: int, area_index: int, pos: Vec3, loaded: bool, now: float
    ) -> None:
        last, self._last_screen = self._last_screen, screen
        if context != Context.QUEST or self.source == "override":
            return
        in_area = screen == Screen.IN_AREA
        if in_area:
            section, found = self._lookup(area_index)
            if found:
                self.section, self.source = section, "area_index"
        if in_area and last not in (-1, Screen.IN_AREA):
            self._settle_at = now + SETTLE_S
        elif in_area and loaded and self._initial:
            self._settle_at, self._initial = now + SETTLE_S, False
        if not self._settle_at or now < self._settle_at:
            return
        self._settle_at = 0.0
        section, _ = self.sections.snap(pos.x, pos.z, SNAP_DIST)
        if section is None:
            return
        if not self._lookup(area_index)[1]:
            self.learnt[area_index] = section
        if self.source != "area_index" or self.section is None:
            self.section, self.source = section, "transition"


class MemoryReader:
    """Owns the debugger connection and the polling thread; `snapshot` is the latest poll."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        poll_hz: float = 3.0,
        sections: SectionMap | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.poll_interval = 1.0 / max(0.5, poll_hz)
        self._client: Client | None = None
        self._client_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._snapshot = GameSnapshot()
        self._poll_count = 0
        self._hp_max = 0
        self._stamina_max = 0
        self._game_title = ""
        self._game_loaded = False
        self._tracker = SectionTracker(sections or SectionMap((), {}))
        self._section_requests: queue.SimpleQueue[tuple[str, int | None]] = queue.SimpleQueue()
        self._species: dict[int, SpeciesRow] = {}
        self._subscribers: list[queue.SimpleQueue[GameSnapshot]] = []

    # --- lifecycle ---

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="mhfu-reader", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._drop()  # a poll waiting on a reply then fails at once instead of timing out
        if self._thread:
            self._thread.join(timeout=2.0)

    def _drop(self) -> None:
        """Close the client once, whichever thread gets here first: two closes deadlock."""
        with self._client_lock:
            client, self._client = self._client, None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    @property
    def snapshot(self) -> GameSnapshot:
        with self._lock:
            return self._snapshot

    @property
    def client(self) -> Client | None:
        """The connection, for the writer; None while disconnected."""
        return self._client

    def subscribe(self) -> queue.SimpleQueue[GameSnapshot]:
        """A queue that gets every snapshot published from now on."""
        q: queue.SimpleQueue[GameSnapshot] = queue.SimpleQueue()
        with self._lock:
            self._subscribers.append(q)
        return q

    def _publish(self, snap: GameSnapshot) -> None:
        with self._lock:
            self._snapshot = snap
            for q in self._subscribers:
                q.put(snap)

    def set_section_override(self, section: int | None) -> None:
        """Pin the tracked section until reset; safe from any thread."""
        self._section_requests.put(("override", section))

    def reset_section_tracking(self) -> None:
        """Drop the override and re-snap on the next settled poll; safe from any thread."""
        self._section_requests.put(("reset", None))

    # --- thread ---

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._client is None or self._client.closed:
                self._connect()
                if self._stop.is_set():
                    break
            start = time.monotonic()
            try:
                self._publish(self.poll())
            except DEBUGGER_ERRORS as e:
                self._drop()
                self._publish(GameSnapshot(status_text=f"connection lost: {e}"))
            except Exception as e:  # keep polling: a dead thread would freeze the HUD unseen
                self._publish(replace(self.snapshot, status_text=f"poll failed: {e!r}"))
            self._stop.wait(max(0.0, self.poll_interval - (time.monotonic() - start)))

    def _connect(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            attempt += 1
            text = f"waiting for PPSSPP debugger (attempt {attempt})..."
            self._publish(GameSnapshot(status_text=text))
            try:
                client = Client.connect(self.host or "127.0.0.1", self.port, timeout=2.0)
            except DEBUGGER_ERRORS:
                self._stop.wait(2.0)
                continue
            self.host, self.port = client.host, client.port
            with self._client_lock:
                if not self._stop.is_set():
                    self._client = client
                    return
            client.close()  # stopped while connecting
            return

    # --- one poll ---

    def _read(self, start: int, size: int) -> Image:
        c = self._client
        if c is None:
            raise ConnectionError("closed")
        return Image(c.read(start, size), start)

    def poll(self) -> GameSnapshot:
        """One poll cycle on the connected client."""
        c = self._client
        if c is None:
            raise ConnectionError("closed")
        t0 = time.monotonic()
        self._poll_count += 1
        if self._poll_count == 1 or self._poll_count % STATUS_EVERY == 0:
            try:
                game = c.game()
            except DEBUGGER_ERRORS:
                game = None
            self._game_title, self._game_loaded = (game.title, True) if game else ("", False)
        if not self._game_loaded:
            idle = GameSnapshot(connected=True, context=Context.BOOT, status_text="no game running")
            return self._meta(idle, t0)

        images = [self._read(*GLOBALS), self._read(*AREA), self._read(*PLAYER)]
        g = HudGame(Space(images))
        screen, scene, area_index = g.screen_state, g.scene, g.area_index
        p = g.player
        loaded = p.loaded
        context = classify(screen, scene, p.vtable)

        quest_timer = carve = 0
        targets: frozenset[int] = frozenset()
        monsters: tuple[MonsterHUD, ...] = ()
        if context == Context.QUEST:
            images += [self._read(*QUEST), self._read(*CARVE), self._read(*REGISTRY)]
            g = HudGame(Space(images))
            quest_timer, carve = g.quest.timer, g.carve_count
            targets = g.quest.target_species
            monsters = self._monsters(g, targets)
        else:
            self._species.clear()

        player = self._player(g, loaded, context)
        self._update_sections(context, screen, area_index, player)
        return self._meta(
            GameSnapshot(
                connected=True,
                context=context,
                game_title=self._game_title,
                status_text="ok",
                screen_state=screen,
                map_subsection=g.map_subsection,
                area_index=area_index,
                scene=scene,
                tracked_section=self._tracker.section,
                tracked_section_source=self._tracker.source,
                learnt_sections=dict(self._tracker.learnt),
                quest_timer_frames=quest_timer,
                carve_count=carve,
                player=player,
                monsters=monsters,
                species_rows=dict(self._species),
            ),
            t0,
        )

    def _meta(self, snap: GameSnapshot, t0: float) -> GameSnapshot:
        return replace(
            snap,
            game_title=snap.game_title or self._game_title,
            poll_latency_ms=(time.monotonic() - t0) * 1000.0,
            poll_count=self._poll_count,
            timestamp=time.time(),
        )

    def _player(self, g: HudGame, loaded: bool, context: Context) -> PlayerHUD:
        p = g.player
        hp, hp_cap, max_hp, stamina = p.hp, p.hp_cap, p.max_hp, g.stamina
        # latch the running maxima only on a settled screen: a zone load leaves garbage here
        if context in (Context.QUEST, Context.VILLAGE) and loaded:
            if 0 < max_hp <= HP_MAX_SANE:
                self._hp_max = max(self._hp_max, max_hp)
            if 0 < stamina <= STAMINA_MAX_SANE:
                self._stamina_max = max(self._stamina_max, stamina)
        if not loaded:
            return PlayerHUD(
                hp_max=self._hp_max or HP_MAX_FALLBACK,
                stamina=stamina if stamina <= STAMINA_MAX_SANE else None,
                stamina_max=self._stamina_max or STAMINA_MAX_FALLBACK,
            )
        sharp, sharp_max, tier = g.sharpness, g.sharpness_max, g.sharpness_tier
        bag = tuple(
            BagSlot(k, raw & 0xFFFF, (raw >> 16) & 0xFF, raw >> 24) for k, raw in enumerate(g.bag)
        )
        return PlayerHUD(
            loaded=True,
            pos=_vec(p.position),
            facing_rad=p.facing,
            hp=hp if hp <= HP_MAX_SANE else None,
            hp_recov=hp_cap if hp_cap <= HP_MAX_SANE else None,
            hp_max=(max_hp if 0 < max_hp <= HP_MAX_SANE else self._hp_max) or HP_MAX_FALLBACK,
            stamina=stamina if stamina <= STAMINA_MAX_SANE else None,
            stamina_max=self._stamina_max or STAMINA_MAX_FALLBACK,
            weapon_drawn=bool(g.weapon_drawn),
            sharpness=sharp if 0 < sharp <= SHARPNESS_SANE else None,
            sharpness_max=sharp_max if 0 < sharp_max <= SHARPNESS_SANE else None,
            sharpness_tier=tier if tier < SHARPNESS_TIERS else None,
            bag=bag,
        )

    def _monsters(self, g: HudGame, targets: frozenset[int]) -> tuple[MonsterHUD, ...]:
        out = []
        for slot, entity in g.monsters().items():
            try:
                img = self._read(entity.base + ENTITY[0], ENTITY[1])
            except DEBUGGER_ERRORS:
                continue
            m = self._monster(slot, HudEntity(img, entity.base), targets)
            if m is not None:
                out.append(m)
        return tuple(out)

    def _monster(self, slot: int, e: HudEntity, targets: frozenset[int]) -> MonsterHUD | None:
        vtable = e.vtable
        if not a.VTABLE_BAND <= vtable < a.VTABLE_BAND_END:
            return None
        pos = _vec(e.position)
        if not _finite(pos):
            return None
        species, hp = e.species, e.hp
        big = species in targets
        name, slug = identify(vtable, species)
        scale = e.render_scale[0]
        lo, hi = SCALE_SANE
        self._species_row(species)
        return MonsterHUD(
            slot=slot,
            ptr=e.base,
            vtable=vtable,
            species=species,
            entity_id=e.id,
            name=name,
            icon_slug=slug,
            big=big,
            pos=pos,
            hp=hp,
            hp_max=max(e.max_hp, hp),
            render_scale=scale if lo < scale < hi else None,
            drawn=e.draw_node != 0,
            anim_input=e.anim_input,
            actions=e.slot_actions,
            state=(e.main_state, e.sub_state),
            cells=cells(e, BIG_CELLS if big else AI_CELLS),
            herd=() if big else tuple(m for m in e.herd_members if m),
        )

    def _species_row(self, species: int) -> None:
        """Read a species row the first time it is seen this quest."""
        if species in self._species:
            return
        base = a.SPECIES_TABLE + species * a.SPECIES.step
        try:
            img = self._read(base + SPECIES_ROW[0], SPECIES_ROW[1])
        except DEBUGGER_ERRORS:
            return
        row = HudSpecies(img, base)
        self._species[species] = SpeciesRow(species, base, cells(row, SPECIES_CELLS))

    def _update_sections(
        self, context: Context, screen: int, area_index: int, player: PlayerHUD
    ) -> None:
        while not self._section_requests.empty():
            kind, section = self._section_requests.get()
            if kind == "override":
                self._tracker.override(section)
            else:
                self._tracker.reset()
        self._tracker.update(
            context, screen, area_index, player.pos, player.loaded, time.monotonic()
        )
