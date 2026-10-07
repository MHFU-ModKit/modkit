# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""cli_bridge.lua in lupa, against a fake `mhfu` table backed by a dict of memory."""

from pathlib import Path

import pytest
from lupa.lua54 import LuaRuntime
from mhfu import addresses as a

SCRIPT = Path(__file__).parents[1] / "lua" / "tools" / "cli_bridge.lua"
MAGIC = 0x4D484252
FORCE, FREEZE, CLEAR, MOVE = 1, 2, 3, 4
MOVE_BLOCK = 0x09F00000  # noaddr
FREEZE_BITS = 0x10100
OTHER_BITS = 0x7
GATE = a.ENTITY.FREEZE_GATE
BR, CB = a.CLI_BRIDGE_BLOCK, a.CLI_BRIDGE


class Game:
    """The script loaded, with monsters at registry slots 1 and 2."""

    def __init__(self, port_lib: str | None = None) -> None:
        self.mem: dict[int, int] = {}
        self.slots = {1: a.RAM.start + 0x100000, 2: a.RAM.start + 0x110000}
        self.seq = 0
        self.lua = LuaRuntime()
        g = self.lua.globals()
        self.handlers: list[object] = []
        self.moves: list[tuple[int, int]] = []
        self.stops = 0
        self.wrapped = True
        g.mhfu = self.lua.table_from(
            {
                "addr": self.lua.execute(a.render_lua(a.table())),
                "read_u32": lambda at: self.mem.get(at, 0),
                "write_u32": self.mem.__setitem__,
                "entity_at": lambda slot: self.slots.get(slot, 0),
                "on_bigmonster_action": lambda fn, _pri: self.handlers.append(fn),
                "log": lambda _msg: None,
                "move_play": self._move_play,
                "move_stop": self._move_stop,
                "move_block": lambda: MOVE_BLOCK,
            }
        )
        if port_lib is not None:
            self.lua.execute(f"package.loaded.mhfu_port = {port_lib}")
        self.lua.execute(SCRIPT.read_text())

    def _move_play(self, ent: int, spec: int) -> bool:
        self.moves.append((ent, spec))
        return self.wrapped

    def _move_stop(self) -> bool:
        self.stops += 1
        return True

    def gate(self, slot: int) -> int:
        return self.mem.get(self.slots[slot] + GATE, 0)

    def send(self, cmd: int, slot: int = 1, arg: int = 0) -> None:
        """Write one command and run a tick; the script acks it."""
        self.seq += 1
        words = {CB.MAGIC: MAGIC, CB.SEQ: self.seq, CB.CMD: cmd, CB.SLOT: slot, CB.ARG: arg}
        self.mem.update({BR + at: value for at, value in words.items()})
        self.tick()
        assert self.mem[BR + CB.ACK] == self.seq

    def tick(self) -> None:
        self.lua.globals().mhfu_tick()

    def action(self, engine_choice: int, slot: int = 1) -> int:
        """The executor hook's answer for the monster in `slot`; the engine's without one."""
        if not self.handlers:
            return engine_choice
        ctx = self.lua.table_from({"action_id": engine_choice, "entity": self.slots[slot]})
        return int(self.handlers[-1](ctx))


@pytest.fixture
def game() -> Game:
    return Game()


def test_clear_releases_the_action(game):
    game.send(FORCE, arg=0x2B)
    assert (game.mem[BR + CB.STATUS], game.action(5)) == (0x2B, 0x2B)
    assert game.action(5, slot=2) == 5
    game.send(CLEAR)
    game.tick()
    assert (game.mem[BR + CB.STATUS], game.action(5)) == (0, 5)


PORT_LIB = """{ ports = { zin = { name = "zin", ent = %d, log = {},
  latch = function(self, a1, uses) self.log[#self.log + 1] = a1 .. "x" .. uses end,
  release = function(self) self.log[#self.log + 1] = "release" end } } }"""


def test_a_ported_monster_is_forced_through_its_port():
    game = Game(PORT_LIB % Game().slots[1])
    game.send(FORCE, arg=101)
    game.send(FORCE, arg=102)
    game.send(CLEAR)
    log = game.lua.eval("package.loaded.mhfu_port.ports.zin.log")
    assert list(log.values()) == ["101x1073741823", "release", "102x1073741823", "release"]
    assert not game.handlers  # the port's hook stays the only one
    game.send(FORCE, slot=2, arg=7)
    assert game.action(5, slot=2) == 7


def test_freeze_off_unfreezes(game):
    game.mem[game.slots[1] + GATE] = OTHER_BITS
    game.send(FREEZE, arg=1)
    assert game.gate(1) == OTHER_BITS | FREEZE_BITS
    game.send(FREEZE, arg=0)
    game.tick()
    assert game.gate(1) == OTHER_BITS


def test_clear_unfreezes(game):
    game.send(FREEZE, arg=1)
    game.send(CLEAR)
    assert game.gate(1) == 0


def test_clear_after_a_force_drops_the_engines_halt(game):
    game.send(FORCE, arg=0x2B)
    game.mem[game.slots[1] + GATE] = FREEZE_BITS  # the engine's own, while forced
    game.send(CLEAR)
    game.tick()
    assert game.gate(1) == 0


def test_freezing_another_slot_frees_the_first(game):
    game.send(FREEZE, slot=1, arg=1)
    game.send(FREEZE, slot=2, arg=1)
    assert (game.gate(1), game.gate(2)) == (0, FREEZE_BITS)
    game.send(CLEAR, slot=2)
    assert (game.gate(1), game.gate(2)) == (0, 0)


def test_move_plays_the_block_spec_on_the_slot(game):
    assert game.mem[BR + CB.MOVE_STATE] == MOVE_BLOCK
    game.send(MOVE, slot=2)
    assert game.moves == [(game.slots[2], BR + CB.MOVE)]
    game.send(CLEAR)
    assert game.stops == 1
