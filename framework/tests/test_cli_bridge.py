"""cli_bridge.lua in lupa, against a fake `mhfu` table backed by a dict of memory."""

from pathlib import Path

import pytest
from lupa.lua54 import LuaRuntime
from mhfu import addresses as a

SCRIPT = Path(__file__).parents[1] / "lua" / "tools" / "cli_bridge.lua"
MAGIC = 0x4D484252
FORCE, FREEZE, CLEAR = 1, 2, 3
FREEZE_BITS = 0x10100
OTHER_BITS = 0x7
GATE = a.ENTITY.FREEZE_GATE
BR, CB = a.CLI_BRIDGE_BLOCK, a.CLI_BRIDGE


class Game:
    """The script loaded, with monsters at registry slots 1 and 2."""

    def __init__(self) -> None:
        self.mem: dict[int, int] = {}
        self.slots = {1: a.RAM.start + 0x100000, 2: a.RAM.start + 0x110000}
        self.seq = 0
        self.lua = LuaRuntime()
        g = self.lua.globals()
        self.handlers: list[object] = []
        g.mhfu = self.lua.table_from(
            {
                "addr": self.lua.execute(a.render_lua(a.table())),
                "read_u32": lambda at: self.mem.get(at, 0),
                "write_u32": self.mem.__setitem__,
                "entity_at": lambda slot: self.slots.get(slot, 0),
                "on_bigmonster_action": lambda fn, _pri: self.handlers.append(fn),
                "log": lambda _msg: None,
            }
        )
        self.lua.execute(SCRIPT.read_text())

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

    def action(self, engine_choice: int) -> int:
        ctx = self.lua.table_from({"action_id": engine_choice})
        return int(self.handlers[0](ctx))


@pytest.fixture
def game() -> Game:
    return Game()


def test_clear_releases_the_action(game):
    game.send(FORCE, arg=0x2B)
    assert (game.mem[BR + CB.STATUS], game.action(5)) == (0x2B, 0x2B)
    game.send(CLEAR)
    game.tick()
    assert (game.mem[BR + CB.STATUS], game.action(5)) == (0, 5)


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
