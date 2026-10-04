# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Headless pygame, set before it is imported; a fake PPSSPP with a quest in its memory; snapshots
and fakes of the reader and writer built by hand. No game data: every byte is written here."""

import os

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

import asyncio
import queue
import struct
import threading
import time
from dataclasses import replace

import pygame
import pytest
from mhfu import addresses as a
from mhfu.structs import Screen
from mhfu_hud.app import HUDApp
from mhfu_hud.calibration import Calibration
from mhfu_hud.state import (
    BagSlot,
    Cell,
    Context,
    GameSnapshot,
    MonsterHUD,
    PlayerHUD,
    SpeciesRow,
    Vec3,
)
from mhfu_hud.writer import GameWriter
from ppsspp_debug.testing import FakePPSSPP

BASE = a.RAM.start + 0x80_0000  # user memory, as the mhfu live tests use
SIZE = 0x180_0000
MON = BASE + 0x90_0000
TIGREX, POPO = 0x4B, 0x46
E = a.ENTITY


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    """Calibration and assets resolve under tmp_path, never the user's folders."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("MHFU_HUD_ASSETS", raising=False)


@pytest.fixture(autouse=True)
def _no_threads_left():
    """Every thread a test starts is gone when it ends."""
    before = set(threading.enumerate())
    yield
    deadline = time.monotonic() + 2.0
    while (left := [t for t in threading.enumerate() if t not in before and t.is_alive()]) and (
        time.monotonic() < deadline
    ):
        time.sleep(0.01)
    assert not left


@pytest.fixture(scope="session", autouse=True)
def _pygame():
    yield
    pygame.quit()


class Quest(FakePPSSPP):
    """A fake PPSSPP in a quest: the player in snowy area 99, a Tigrex in slot 1, a Popo herd of
    one in slot 2."""

    def __init__(self) -> None:
        super().__init__(base=BASE, size=SIZE)
        self.tigrex, self.popo = MON, MON + 0x2000
        poke = self.poke
        poke("B", a.SCREEN_STATE, Screen.IN_AREA)
        poke("H", a.AREA_INDEX, 99)
        poke("H", a.PLAYER_STAMINA, 150)
        p = a.PLAYER_ENTITY
        poke("I", p + E.VTABLE, a.PLAYER_QUEST_VTABLE)
        poke("3f", p + E.POSITION, 18490.0, 0.0, 12600.0)
        rot = [0.0] * 12
        rot[8] = rot[10] = 1.0
        poke("12f", p + E.ROTATION, *rot)
        poke("HH", p + E.HP_CAP, 120, 150)
        poke("H", p + E.HP, 100)
        poke("I", a.PLAYER_BAG, 0x00010040)
        poke("H", a.SHARPNESS, 80)
        poke("H", a.SHARPNESS_MAX, 150)
        poke("B", a.SHARPNESS_TIER, 1)
        poke("I", a.QUEST_SINGLETON + a.QUEST.TIMER, 30 * 60 * 35)
        target = a.QUEST_SINGLETON + a.QUEST.TARGETS
        poke("I", target + a.QUEST_TARGET.EM_ID, TIGREX)
        poke("H", target + a.QUEST_TARGET.COUNT, 1)
        rows = [
            (self.tigrex, a.TIGREX_VTABLE, TIGREX, 2400, (0x413, 0x4DB, 0x5A3)),
            (self.popo, a.POPO_VTABLE, POPO, 102, (1011, 1111, 0)),
        ]
        for slot, (base, vt, species, hp, inputs) in enumerate(rows, start=1):
            poke("I", a.ENTITY_REGISTRY + 4 * slot, base)
            poke("I", base + E.VTABLE, vt)
            poke("B", base + E.SPECIES, species)
            poke("H", base + E.HP, hp)
            poke("H", base + E.MAX_HP, hp)
            poke("3f", base + E.POSITION, 18000.0 + slot * 300, 0.0, 12000.0)
            poke("3f", base + E.RENDER_SCALE, 1.1, 1.1, 1.1)
            poke("3H", base + E.ANIM_INPUT, *inputs)
            poke("BB", base + E.MAIN_STATE, 8, 3)
            poke("I", base + E.HERD_MEMBERS, MON + 0x4000)
        sight = a.SPECIES_TABLE + TIGREX * a.SPECIES.step + a.SPECIES.SIGHT_RADIUS
        poke("f", sight, 2700.0)

    def poke(self, fmt: str, address: int, *values: object) -> None:
        struct.pack_into("<" + fmt, self.memory, address - self.base, *values)

    def peek(self, fmt: str, address: int) -> tuple:
        return struct.unpack_from("<" + fmt, self.memory, address - self.base)

    @property
    def writes(self) -> list[dict]:
        return [m for m in self.received if m["event"].startswith("memory.write")]


@pytest.fixture
def fake():
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    server = Quest()
    asyncio.run_coroutine_threadsafe(server.__aenter__(), loop).result()
    yield server
    asyncio.run_coroutine_threadsafe(server.__aexit__(None, None, None), loop).result()
    loop.call_soon_threadsafe(loop.stop)
    thread.join()
    loop.close()


def monster(slot, species, vtable, big, **kw) -> MonsterHUD:
    cells = (
        Cell(E.MAIN_STATE, 8),
        Cell(E.ANIM_INPUT, (1051, 1251, 1451)),
        Cell(E.CLIP, MON),
        Cell(E.ENGAGE, 1.0),
        Cell(E.HERD_RALLY, (1.0, 2.0, 3.0)),
    ) * 8
    fields = dict(
        slot=slot,
        ptr=MON + 0x2000 * (slot - 1),
        vtable=vtable,
        species=species,
        entity_id=slot,
        name="Tigrex" if big else "Popo",
        icon_slug="tigrex" if big else "popo",
        big=big,
        pos=Vec3(18000.0 + slot * 300, 0.0, 12000.0),
        hp=2400 if big else 102,
        hp_max=2400 if big else 102,
        render_scale=1.1,
        drawn=True,
        anim_input=(1051, 1251, 1451),
        actions=(51, 51, 51) if big else None,
        state=(8, 3),
        cells=cells,
        herd=() if big else (MON,),
    )
    return MonsterHUD(**(fields | kw))


@pytest.fixture
def snaps() -> dict[str, GameSnapshot]:
    """One snapshot per context, the quest one with a Tigrex and a Popo."""
    player = PlayerHUD(
        loaded=True,
        pos=Vec3(18490.0, 0.0, 12600.0),
        facing_rad=1.0,
        hp=100,
        hp_recov=120,
        hp_max=150,
        stamina=150,
        stamina_max=150,
        weapon_drawn=False,
        sharpness=80,
        sharpness_max=150,
        sharpness_tier=1,
        bag=tuple(BagSlot(k, 0x40 + 0x111 * k, k + 1, 0) for k in range(12)),
    )
    row = SpeciesRow(
        TIGREX,
        a.SPECIES_TABLE + TIGREX * a.SPECIES.step,
        (Cell(a.SPECIES.SIGHT_RADIUS, 2700.0), Cell(a.SPECIES.RANGE_PARAMS, (40.0,) * 12)),
    )
    quest = GameSnapshot(
        connected=True,
        status_text="ok",
        context=Context.QUEST,
        screen_state=Screen.IN_AREA,
        area_index=99,
        tracked_section=1,
        tracked_section_source="area_index",
        quest_timer_frames=30 * 60 * 35,
        player=player,
        monsters=(
            monster(1, TIGREX, a.TIGREX_VTABLE, True),
            monster(2, POPO, a.POPO_VTABLE, False),
        ),
        species_rows={TIGREX: row},
    )
    return {
        "disconnected": GameSnapshot(),
        "boot": GameSnapshot(connected=True, context=Context.BOOT, status_text="no game running"),
        "menu": replace(quest, context=Context.MENU, monsters=()),
        "loading": replace(quest, context=Context.LOADING, screen_state=Screen.MENU, monsters=()),
        "village": replace(quest, context=Context.VILLAGE, screen_state=Screen.BOOT, monsters=()),
        "camp": replace(quest, area_index=98, tracked_section=None),
        "quest": quest,
    }


class Reader:
    """The window's view of the reader, and nothing else: no client to reach."""

    def __init__(self, snapshot: GameSnapshot) -> None:
        self.snapshot = snapshot
        self.sections: list[int | None] = []

    def set_section_override(self, section: int | None) -> None:
        self.sections.append(section)

    def reset_section_tracking(self) -> None:
        self.sections.append(None)


class Recorder:
    """A debugger client that records writes."""

    closed = False

    def __init__(self) -> None:
        self.writes: list[tuple[int, bytes]] = []

    def write(self, address: int, data: bytes) -> None:
        self.writes.append((address, bytes(data)))


class Source:
    """The writer's view of the reader: a snapshot queue and a client."""

    def __init__(self, client: Recorder) -> None:
        self.client = client
        self.queue: queue.SimpleQueue[GameSnapshot] = queue.SimpleQueue()

    def subscribe(self) -> queue.SimpleQueue[GameSnapshot]:
        return self.queue


@pytest.fixture
def reader(snaps) -> Reader:
    return Reader(snaps["quest"])


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def source(recorder) -> Source:
    return Source(recorder)


@pytest.fixture
def writer(source) -> GameWriter:
    """A writer whose thread is not started: tests call `apply`."""
    return GameWriter(source)


def _app(reader, writer, tmp_path) -> HUDApp:
    return HUDApp(reader, Calibration(tmp_path / "cal.json"), writer=writer, assets=tmp_path / "x")


@pytest.fixture
def app(reader, writer, tmp_path) -> HUDApp:
    return _app(reader, writer, tmp_path)


@pytest.fixture
def app_ro(reader, tmp_path) -> HUDApp:
    """`hud --read-only`: no writer."""
    return _app(reader, None, tmp_path)
