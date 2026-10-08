# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import math

import pytest
from mhfu import addresses as a
from mhfu.cli import main
from mhfu.live import Launcher, Session, rig
from mhfu.structs import DRAW_GATE, SKIP_DRAW
from PIL import Image
from ppsspp_debug import DebuggerError, Lane

TIGREX, MAP_MANAGER = a.RAM.start + 0x90_0000, a.RAM.start + 0x91_0000  # heap in `fake`
JOINTS = a.RAM.start + 0x92_0000
AREA = 109
TAIL = 44


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
    assert guard.hp == 150 and not guard.calm_monsters and not guard.cull_small
    assert rig.pin_hp(s, cull=True).cull_small


def test_hold(s, game):
    assert rig.hold(s).base == TIGREX
    assert game.peek("h", TIGREX + a.ENTITY.SCRIPT_WAIT) == (rig.HOLD,)


@pytest.fixture
def tail(game):
    """The Tigrex at (600, 5, 0), facing +z, its tail tip posed 300 behind it."""
    game.poke("3f", TIGREX + a.ENTITY.POSITION, 600.0, 5.0, 0.0)
    game.poke("I", TIGREX + a.ENTITY.JOINTS, JOINTS)
    game.poke("3f", JOINTS + TAIL * a.JOINT.size + a.JOINT.POSITION, 600.0, 5.0, -300.0)
    return game


def test_aim(s, tail):
    assert rig.aim(s, TAIL) == pytest.approx((430, 5.0, -300))  # 170 short of it along +x
    assert tail.peek("H", TIGREX + a.ENTITY.YAW) == (0x4000,), "turned a quarter, tail to us"


def test_aim_takes_the_floor_at_the_monster(s, tail, monkeypatch):
    class Ledge:
        """The ground at 5 and a ledge at 600 everywhere: the highest without `near`."""

        def height(self, x: float, z: float, near: float | None = None) -> float:
            return 600.0 if near is None or abs(near - 600.0) < abs(near - 5.0) else 5.0

    monkeypatch.setattr(rig, "floor", lambda s: Ledge())
    assert rig.aim(s, TAIL)[1] == 5.0


def test_aim_without_turn(s, tail):
    assert rig.aim(s, TAIL, distance=100, turn=False) == pytest.approx((500, 5.0, -300))
    assert tail.peek("H", TIGREX + a.ENTITY.YAW) == (0,)


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


@pytest.mark.parametrize("fake", [True], indirect=True)
def test_open_launches_a_state_and_reads_it_stopped(fake, game, clock, monkeypatch):
    monkeypatch.setattr(rig, "running", lambda launcher: False)
    launched = []

    def launch(*args, state=None, **kw):
        launched.append(state)
        s = Session.attach(fake.port, timeout=5)
        pause, refusals = s.client.pause, [DebuggerError("cpu.stepping", "CPU not started")]

        def refused_once():
            if refusals:
                raise refusals.pop()
            return pause()

        monkeypatch.setattr(s.client, "pause", refused_once)
        return s

    monkeypatch.setattr(Session, "launch", launch)
    stopped = []
    fake.on_read.append(lambda address: stopped.append(fake.stepping))
    with rig.Rig.open(Launcher(lane=1), state=6):
        assert launched == [rig.state_file(6, 1)]
        assert stopped and all(stopped) and not fake.stepping


@pytest.fixture
def cli(fake, game, clock, monkeypatch):
    """`mhfu rig ...` against the fake game, attached rather than launched."""
    monkeypatch.setattr(rig, "running", lambda launcher: True)
    monkeypatch.setattr(Session, "launch", lambda *a, **kw: Session.attach(fake.port, timeout=5))
    fake.poke("I", a.MAP_MANAGER_PTR, MAP_MANAGER)
    fake.poke("H", MAP_MANAGER + a.MAP_MANAGER.STAGE, AREA)
    return lambda *argv: main(["rig", *argv, "--lane", "1"])


def test_cli(cli, capsys):
    assert cli("teleport", "300", "-40") == 0
    assert cli("summon", "--distance", "500") == 0
    assert cli("where") == 0
    out = capsys.readouterr().out.splitlines()
    assert out[2] == f"stage st{AREA}, area {AREA}, screen 0"
    assert out[:2] == ["player -> (300, 5.0, -40)", "Tigrex -> (800, 5, -40), 500 away, drawn"]
    assert out[-1] == f"Tigrex 0x{TIGREX:08X} (800, 5, -40), 500 away, section {AREA}, drawn"


def test_cli_hold_and_aim(cli, tail, capsys):
    assert cli("hold") == 0
    assert cli("aim", str(TAIL), "--no-turn") == 0
    assert cli("hold", "--slot", "5") == 1
    out = capsys.readouterr()
    assert out.out.splitlines() == [
        f"Tigrex held: its AI script waits {rig.HOLD} AI frames",
        f"player -> (430, 5.0, -300), bone {TAIL} 170 ahead",
    ]
    assert "slot 5 holds no big monster" in out.err


def test_cli_not_drawn(cli, game, capsys):
    game.on_read.clear()
    assert cli("summon") == 1
    assert "NOT drawn" in capsys.readouterr().out


patched = pytest.mark.parametrize("fake", [True], indirect=True)


@patched
def test_shot(s, tmp_path):
    assert rig.shot(s, tmp_path / "a" / "b.png").read_bytes().startswith(b"\x89PNG")


@patched
def test_film(s, tmp_path):
    got = rig.film(s, tmp_path, 1, 5, columns=2)
    assert [f.name for f in got.frames] == [f"{k:04d}.png" for k in range(5)]
    assert got.times == pytest.approx([0, 0.2, 0.4, 0.6, 0.8]) and got.fps == pytest.approx(5)
    assert got.speed == pytest.approx(1)
    with Image.open(got.sheet) as sheet:
        assert sheet.size == (2 * 1 + 2, 3 * 1 + 2 * 2)  # 1x1 frames, 2 px apart


@patched
def test_film_skips_slots_a_late_shot_passed(s, clock, tmp_path, monkeypatch):
    save = s.client.save_screenshot

    def slow(path, scale=1):
        save(path, scale)
        clock.sleep(0.25)

    monkeypatch.setattr(s.client, "save_screenshot", slow)
    got = rig.film(s, tmp_path, 1, 10)
    assert got.times == pytest.approx([0, 0.25, 0.5, 0.8])


def test_shot_on_stock_ppsspp(cli, tmp_path, capsys):
    assert cli("shot", str(tmp_path / "a.png")) == 1
    assert "modkit's build" in capsys.readouterr().err


@patched
def test_cli_film(cli, tmp_path, capsys):
    assert cli("film", str(tmp_path), "--seconds", "1", "--fps", "2") == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("2 frames at 2.0 fps") and out[0].endswith("the game at 1.00x")
    assert out[1] == str(tmp_path / "sheet.png")
