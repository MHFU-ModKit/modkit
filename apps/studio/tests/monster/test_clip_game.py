# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import shutil
from contextlib import nullcontext

import pytest
from mhfu import addresses as a
from mhfu.live.shell_anim import MAGIC, Op
from mhfu.memory import Image, Space
from mhfu_studio.monster import clip_game
from mhfu_studio.monster.workspace import MonsterWorkspace

MON = a.RAM.start + 0x90_0000  # entities inside the fake's memory
B = a.CLI_BRIDGE


class Fake(Space):
    """The registry, two entities and the bridge block; with `ack`, cli_bridge.lua's side."""

    def __init__(self, bridge: bool = True, ack: bool = True) -> None:
        images = [Image(bytes(4 * 21), a.ENTITY_REGISTRY), Image(bytes(0x1000 * 5), MON)]
        if bridge:
            images.append(Image(bytes(0x40), a.CLI_BRIDGE_BLOCK))
        super().__init__(images)
        self.ack = ack

    def monster(self, slot: int, species: int, vtable: int = 0) -> None:
        base = MON + 0x1000 * (slot - 1)
        self.write_u32(a.ENTITY_REGISTRY + 4 * slot, base)
        self.write_u32(base + a.ENTITY.VTABLE, vtable)
        self.write_u8(base + a.ENTITY.SPECIES, species)

    def write(self, address: int, data: bytes) -> None:
        super().write(address, data)
        if self.ack and address == a.CLI_BRIDGE_BLOCK and len(data) > B.SEQ:
            self.write_u32(address + B.ACK, self.u32(address + B.SEQ))

    def command(self) -> tuple[int, int, int, int]:
        """MAGIC, CMD, SLOT, ARG."""
        at = a.CLI_BRIDGE_BLOCK
        return tuple(self.u32(at + f) for f in (B.MAGIC, B.CMD, B.SLOT, B.ARG))  # type: ignore[return-value]


@pytest.fixture
def game() -> Fake:
    g = Fake()
    g.monster(1, 77)
    g.monster(3, 75, a.TIGREX_VTABLE)
    return g


def test_force(game):
    sent = clip_game.force(game, 100, 75)
    assert sent == clip_game.Sent(3, 1, True)
    assert game.command() == (MAGIC, Op.FORCE_ACTION, 3, 100)
    assert clip_game.force(game, 7, 75).seq == 2


def test_release(game):
    game.write_u32(a.CLI_BRIDGE_BLOCK + B.STATUS, 100)
    assert clip_game.release(game, 75).acked
    assert game.command()[1:3] == (Op.CLEAR, 3) and game.u32(a.CLI_BRIDGE_BLOCK + B.STATUS) == 0


def test_target():
    g = Fake()
    g.monster(2, 40, a.TIGREX_VTABLE)
    g.monster(4, 75)
    assert clip_game.target(g, 75) == 4, "the host species first"
    assert clip_game.target(g, 99) == 2, "else a known big-monster class"
    with pytest.raises(LookupError, match="no big monster"):
        clip_game.target(Fake(), 75)


def test_refusals():
    with pytest.raises(LookupError, match="memory=64"):
        clip_game.force(Fake(bridge=False), 1, 75)
    stalled = Fake(ack=False)
    stalled.monster(1, 75)
    assert not clip_game.force(stalled, 1, 75, wait=0.1).acked


def test_play_button(games, ports, tmp_path, game):
    """The picked clip's anim reaches the bridge; a clip moved since the save does not."""
    path = tmp_path / "zinogre.toml"
    shutil.copy(ports / "zinogre.toml", path)
    ws = MonsterWorkspace(games)
    ws.intel_cache[75] = None
    ws.open(path)
    ws.game_memory = lambda: nullcontext(game)
    ws.play_source(248)
    ws.play_in_game()
    assert game.command() == (MAGIC, Op.FORCE_ACTION, 3, 100)
    assert ws.message == "anim 100 held on monster 3 until Release"
    ws.play_source(2)
    ws.play_in_game()
    assert game.command()[3] == 2
    ws.play_source(248)
    ws.place_clip(7)
    ws.play_in_game()
    assert "save, then build and inject" in ws.message and game.command()[3] == 2
    ws.release_in_game()
    assert game.command()[1] == Op.CLEAR and ws.message == "monster 3 released"
    ws.game_memory = lambda: nullcontext(Fake(bridge=False))
    ws.save()
    ws.play_in_game()
    assert ws.message.startswith("not played in the game: the bridge is not up")


def test_attached_reads_the_lane(monkeypatch):
    seen = {}

    class Session:
        mem = "memory"

        @classmethod
        def attach(cls, port, timeout, wait_for_game):
            seen["port"] = port
            return cls()

        def close(self):
            seen["closed"] = True

    monkeypatch.setattr(clip_game, "Session", Session)
    monkeypatch.setenv("MHFU_LANE", "3")
    with clip_game.attached() as mem:
        assert mem == "memory"
    assert seen == {"port": 45103, "closed": True}
