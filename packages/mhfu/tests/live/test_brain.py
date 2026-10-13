# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Finding em_vhook's config block and reading its rules from game memory."""

import struct
from types import SimpleNamespace
from typing import Any

import pytest
from mhfu import addresses as a
from mhfu.live import brain
from mhfu.memory import Image, Space

MONSTER = a.USER_RAM + 0x4000
BLOCK = a.FRAMEWORK_PRX + 0x87590
"""Where the block sits above the PRX image, as the logs have it."""
SIZE = a.EM_CFG.size or 0
BOARD_AT = brain._board() or SIZE + 0x40
"""The ENTITY word's place in the block: the build's, else a made-up one."""
RULES = int(a.EM_CFG.RULES)
EVENT = a.MONSTER_EVENT_KIND.number("flinch")
needs_board = pytest.mark.skipif(brain._board() is None, reason="this build has no board")
needs_signal = pytest.mark.skipif(
    not hasattr(a.EM_RULE, "SIGNAL"), reason="this build has no signals"
)


class Counting(Space):
    """A memory that counts the requests it is asked."""

    def __init__(self, images: list[Image]) -> None:
        super().__init__(images)
        self.reads: list[tuple[int, int]] = []

    def read(self, address: int, size: int) -> bytes:
        self.reads.append((address, size))
        return super().read(address, size)


def block(rules: dict[str, dict[str, int]] | None = None, *, entity: int = MONSTER) -> bytearray:
    """A config block: its canary, the ticks, the board's owner and `rules` by slot (EM_RULE
    fields by name)."""
    raw = bytearray(BOARD_AT + 8)
    struct.pack_into("<I", raw, a.EM_CFG.CANARY, brain.CANARY)
    struct.pack_into("<I", raw, a.EM_CFG.AI_TICKS, 1234)
    struct.pack_into("<I", raw, BOARD_AT, entity)
    for slot, fields in (rules or {}).items():
        at = RULES + int(slot.removeprefix("r")) * a.EM_RULE.step
        for name, value in fields.items():
            field = getattr(a.EM_RULE, name)
            fmt = {"u8": "B", "u32": "I"}[field.type]
            struct.pack_into("<" + fmt, raw, at + int(field), value)
    return raw


def registry(*monsters: int) -> Image:
    ptrs = struct.pack("<21I", 0, *monsters, *[0] * (20 - len(monsters)))
    return Image(ptrs, int(a.ENTITY_REGISTRY))


def memory(cfg: bytearray, at: int = BLOCK, monsters: tuple[int, ...] = (MONSTER,)) -> Counting:
    """The PRX window with `cfg` in it at `at`, and the entity registry."""
    window = bytearray(0x100000)
    k = at - int(a.FRAMEWORK_PRX)
    window[k : k + len(cfg)] = cfg
    return Counting([Image(bytes(window), int(a.FRAMEWORK_PRX)), registry(*monsters)])


@pytest.fixture(autouse=True)
def board(monkeypatch: pytest.MonkeyPatch) -> None:
    """A build that has no board is given one for the tests of its owner."""
    monkeypatch.setattr(brain, "_board", lambda: BOARD_AT)


def session(mem: Space) -> Any:
    return SimpleNamespace(mem=mem)


def test_the_block_is_found_by_its_canary_and_its_monster() -> None:
    mem = memory(block())
    assert brain.locate(session(mem)) == BLOCK
    assert all(size <= brain.CHUNK for _, size in mem.reads)


def test_a_canary_whose_board_names_no_live_monster_is_refused() -> None:
    mem = memory(block(entity=MONSTER + 0x100))
    assert brain.locate(session(mem)) is None
    assert {size for _, size in mem.reads if size > 0x1000} == {brain.CHUNK}
    assert max(at for at, _ in mem.reads) < int(a.FRAMEWORK_PRX) + brain.WINDOW, "no scan of RAM"
    assert brain.locate(session(memory(block(entity=0)))) is None


def test_no_canary_no_block() -> None:
    assert brain.locate(session(memory(bytearray(SIZE)))) is None


def test_a_block_outside_the_window_is_found_in_user_ram(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(brain, "WINDOW", 0x1000)
    at = int(a.USER_RAM) + 0x1000
    ram = bytes(0x1000) + bytes(block()).ljust(0x3F000, b"\0")
    mem = Counting(
        [Image(bytes(0x1000), int(a.FRAMEWORK_PRX)), Image(ram, int(a.USER_RAM)), registry(MONSTER)]
    )
    assert brain.locate(session(mem)) == at


def test_the_rules_are_decoded() -> None:
    rules: dict[str, dict[str, int]] = {
        "r0": {
            "ON": EVENT,
            "LEFT": brain.UNLIMITED,
            "FIRED": 3,
            "LAST_FIRE": 120,
            "MIN_FRAMES": 15,
        },
        "r2": {"FROM_MOVE": 1, "PLAY_MOVE": 2, "LEFT": 1, "COOLDOWN": 30, "FIRED": 7},
        "r4": {"FROM_MASK": 2, "LEFT": 0, "FIRED": 2},
        "r5": {"ON": EVENT, "FLAGS": brain.NO_PLAY, "LEFT": brain.UNLIMITED},
    }
    mem = memory(block(rules))
    got = brain.read(session(mem), BLOCK)
    assert (got.cfg, got.ticks) == (BLOCK, 1234)
    assert [s.index for s in got.slots] == [0, 2, 4, 5], (
        "slots nothing is installed in are left out"
    )
    first, second, spent, quiet = got.slots
    assert first == brain.Slot(0, EVENT, -1, True, 15, 0, 3, brain.UNLIMITED, 120)
    assert (second.plays, second.min_frames, second.cooldown, second.fired) == (True, 0, 30, 7)
    assert (spent.left, spent.fired) == (0, 2) and not quiet.plays


@needs_signal
def test_a_signal_rule_says_which_signal() -> None:
    mem = memory(block({"r0": {"SIGNAL": 3, "LEFT": brain.UNLIMITED}}))
    [slot] = brain.read(session(mem), BLOCK).slots
    assert (slot.signal, slot.on) == (2, 0)


def test_a_read_is_one_request() -> None:
    mem = memory(block({"r0": {"ON": EVENT, "LEFT": brain.UNLIMITED}}))
    brain.read(session(mem), BLOCK)
    assert mem.reads == [(BLOCK, RULES + (a.EM_CFG.RULES.count or 0) * a.EM_RULE.step)]


@needs_board
def test_a_signal_is_one_byte_of_the_board() -> None:
    mem = memory(block())
    before = mem.read(BLOCK, len(block()))
    brain.fire_signal(session(mem), BLOCK, 3)
    after = mem.read(BLOCK, len(block()))
    at = int(a.EM_BLOCK.BOARD) + int(a.EM_BOARD.SIGNALS) + 3
    assert [k for k in range(len(before)) if before[k] != after[k]] == [at] and after[at] == 1


@needs_board
def test_a_signal_the_board_lacks_is_refused() -> None:
    mem = memory(block())
    for bad in (-1, a.EM_BOARD.SIGNALS.count or 0):
        with pytest.raises(ValueError, match="the board holds"):
            brain.fire_signal(session(mem), BLOCK, bad)


@needs_board
def test_rage_is_one_byte_of_the_board() -> None:
    mem = memory(block())
    at = BLOCK + int(a.EM_BLOCK.BOARD) + int(a.EM_BOARD.RAGE)
    brain.set_rage(session(mem), BLOCK, True)
    assert mem.read(at, 1) == bytes([brain.RAGE_ON])
    brain.set_rage(session(mem), BLOCK, False)
    assert mem.read(at, 1) == bytes([brain.RAGE_OFF])
