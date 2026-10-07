# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`moves.play_own`: a port's own move asked for by name through the bridge."""

import pytest
from mhfu import addresses as a
from mhfu.live import moves
from mhfu.live.shell_anim import MAGIC, Op

MON = a.RAM.start + 0x90_0000  # inside the fake's memory
BR, CB = a.CLI_BRIDGE_BLOCK, a.CLI_BRIDGE


class Bridge:
    """cli_bridge.lua's side: CMD 4 with ARG 1 answers RESULT 1 for the names in `own`."""

    def __init__(self, fake, own: set[str], acks: bool = True) -> None:
        self.fake, self.own, self.acks = fake, own, acks
        self.asked: list[tuple[int, str]] = []
        fake.memory.extend(bytes(BR + 0x100 - fake.base - len(fake.memory)))
        fake.poke("I", a.ENTITY_REGISTRY + 4, MON)
        fake.poke("I", MON + a.ENTITY.VTABLE, a.TIGREX_VTABLE)
        fake.on_read.append(self.tick)

    def tick(self, address: int) -> None:
        f = self.fake
        if address != BR + CB.ACK or not self.acks:
            return
        magic, seq, cmd, slot, arg = f.peek("5I", BR)
        if magic == MAGIC and f.peek("I", BR + CB.ACK) != (seq,):
            name = bytes(f.peek(f"{CB.NAME.count}s", BR + CB.NAME)[0]).split(b"\0")[0].decode()
            if cmd == Op.MOVE and arg == moves.OWN:
                self.asked.append((slot, name))
                f.poke("I", BR + CB.RESULT, int(name in self.own))
            f.poke("I", BR + CB.ACK, seq)


def test_play_own(s, fake):
    game = Bridge(fake, {"stamp"})
    assert moves.play_own(s, "stamp")
    assert not moves.play_own(s, "howl")
    assert game.asked == [(1, "stamp"), (1, "howl")]


def test_no_monster_or_bad_name(s, fake):
    game = Bridge(fake, {"stamp"})
    assert not moves.play_own(s, "stamp", slot=2)
    assert not moves.play_own(s, "x" * (CB.NAME.count or 0))
    assert game.asked == []


def test_no_ack(s, fake):
    Bridge(fake, {"stamp"}, acks=False)
    with pytest.raises(TimeoutError):
        moves.play_own(s, "stamp")
