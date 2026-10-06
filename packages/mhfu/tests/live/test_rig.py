# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import math

import pytest
from mhfu import addresses as a
from mhfu.live import Launcher, Session, rig
from mhfu.structs import DRAW_GATE, SKIP_DRAW
from ppsspp_debug import Lane

TIGREX = a.RAM.start + 0x90_0000  # a heap address in `fake`
AREA = 109


class Ground:
    """A floor at height 5 everywhere but x > `edge`."""

    def __init__(self, edge: float = math.inf) -> None:
        self.edge = edge

    def height(self, x: float, z: float, near: float | None = None) -> float | None:
        return 5.0 if x <= self.edge else None


@pytest.fixture
def game(fake, monkeypatch):
    """A player at the origin facing +x in AREA, and a Tigrex that VISIBILITY_GATE culls."""
    monkeypatch.setattr(rig, "floor", lambda s: Ground())
    player = a.PLAYER_ENTITY
    fake.poke("I", player + a.ENTITY.VTABLE, a.PLAYER_QUEST_VTABLE)
    fake.poke("2f", player + a.ENTITY.ROTATION + 4 * 8, 1.0, 0.0)  # forward row: +x
    fake.poke("H", player + a.ENTITY.MAX_HP, 150)
    fake.poke("H", a.AREA_INDEX, AREA)
    registry = [0] * 21
    registry[2] = TIGREX
    fake.poke("21I", a.ENTITY_REGISTRY, *registry)
    fake.poke("I", TIGREX + a.ENTITY.VTABLE, a.TIGREX_VTABLE)
    fake.poke("B", TIGREX + a.ENTITY.SPECIES, 0x4B)
    fake.poke("H", TIGREX + a.ENTITY.SECTION, AREA - 1)
    fake.poke("I", TIGREX + a.ENTITY.RENDER_FLAGS, 0x3 | SKIP_DRAW)

    def gate(address: int) -> None:
        if address == TIGREX + a.ENTITY.RENDER_FLAGS:
            (section,), (flags,) = (
                fake.peek("H", TIGREX + a.ENTITY.SECTION),
                fake.peek("I", TIGREX + a.ENTITY.FLAGS),
            )
            shown = section == AREA and flags & DRAW_GATE
            fake.poke("I", address, 0x3 | (0 if shown else SKIP_DRAW))

    fake.on_read.append(gate)
    return fake


def test_teleport(s, game):
    assert rig.teleport(s, 300, -40) == (300, 5.0, -40)
    assert game.peek("3f", a.PLAYER_ENTITY + a.ENTITY.POSITION) == (300, 5.0, -40)


def test_teleport_off_the_floor(s, game, monkeypatch):
    monkeypatch.setattr(rig, "floor", lambda s: Ground(edge=0))
    with pytest.raises(rig.OffFloor):
        rig.teleport(s, 300, 0)


def test_teleport_needs_a_player(s, fake):
    with pytest.raises(RuntimeError, match="no player"):
        rig.teleport(s, 0, 0)


def test_summon(s, game):
    got = rig.summon(s, distance=600)
    assert got.monster.base == TIGREX and got.drawn
    assert got.at == pytest.approx((600, 5.0, 0)) and got.distance == pytest.approx(600)
    assert game.peek("H", TIGREX + a.ENTITY.SECTION) == (AREA,)
    assert game.peek("I", TIGREX + a.ENTITY.FLAGS)[0] & DRAW_GATE
    assert game.peek("H", TIGREX + a.ENTITY.YAW) == (0xC000,)  # facing -x, back at the player


def test_summon_turns_where_the_floor_ends(s, game, monkeypatch):
    monkeypatch.setattr(rig, "floor", lambda s: Ground(edge=1))
    got = rig.summon(s, distance=600)  # facing +x; every bearing with x > 1 is off the floor
    assert got.at == pytest.approx((0, 5.0, -600), abs=1e-6)  # 90 degrees round


def test_summon_not_drawn(s, game):
    game.on_read.clear()  # no visibility gate running
    assert not rig.summon(s, timeout=1.0).drawn


def test_summon_without_a_monster(s, game):
    game.poke("21I", a.ENTITY_REGISTRY, *[0] * 21)
    with pytest.raises(LookupError):
        rig.summon(s)


def test_pin_hp(s, game):
    guard = rig.pin_hp(s, tick=0.01)
    assert guard.hp == 150 and not guard.calm_monsters


def test_state_path():
    lane = Launcher(lane=1)
    assert rig.Rig.state_path(lane, 6) == Lane(1).stick / "PPSSPP_STATE/ULES01213_1.01_6.ppst"
    assert rig.Rig.state_path(lane, "6") == rig.Rig.state_path(lane, 6)
    assert str(rig.Rig.state_path(Launcher(), "/x/y.ppst")) == "/x/y.ppst"
    with pytest.raises(ValueError, match="lane"):
        rig.Rig.state_path(Launcher(), 6)


@pytest.mark.parametrize("fake", [True], indirect=True)
def test_open_from_a_state(fake, game, clock, monkeypatch):
    monkeypatch.setattr(rig, "running", lambda launcher: True)
    monkeypatch.setattr(Session, "launch", lambda *a, **kw: Session.attach(fake.port, timeout=5))
    path = str(rig.state_file(6, 1))
    fake.states[path] = bytes(fake.memory)
    fake.poke("H", a.PLAYER_ENTITY + a.ENTITY.MAX_HP, 99)
    with rig.Rig.open(Launcher(lane=1), state=6) as r:
        assert r.s.game.player.max_hp == 150
        assert r.speed(fast=True) and fake.fast_forward
    assert not fake.fast_forward
