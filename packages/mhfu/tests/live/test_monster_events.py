# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Reading the framework's monster-event ring from game memory."""

import struct

import pytest
from mhfu import addresses as a
from mhfu.live import monster_events
from mhfu.memory import Image, Space

MOVES = 0x09DE5900  # where the move player's block sits; noaddr
EVENTS = 0x09DE6100  # noaddr
ENT = 0x090C1B00  # noaddr


def memory(head: int, records: dict[int, bytes], *extra: Image, watch: bytes = b"") -> Space:
    bridge = bytearray(0x100)
    struct.pack_into("<I", bridge, a.CLI_BRIDGE.MOVE_STATE, MOVES)
    moves = bytearray(a.MOVE_STATE.size or 0)
    struct.pack_into("<I", moves, a.MOVE_STATE.EVENTS, EVENTS)
    block = bytearray(a.MONSTER_EVENTS.size or 0)
    struct.pack_into("<I", block, a.MONSTER_EVENTS.HEAD, head)
    for k, raw in records.items():
        at = a.MONSTER_EVENTS.RING + (k % 32) * 0x14
        block[at : at + 0x14] = raw
    row = a.MONSTER_EVENTS.WATCH + (a.MONSTER_WATCH.size or 0)  # the second watch
    block[row : row + len(watch)] = watch
    return Space(
        [
            Image(bytes(bridge), a.CLI_BRIDGE_BLOCK),
            Image(bytes(moves), MOVES),
            Image(bytes(block), EVENTS),
            *extra,
        ]
    )


def record(kind: int, frame: int, part: int = 0xFF) -> bytes:
    return struct.pack("<IIIBBBBH2x", ENT, frame, 1000 + frame, kind, part, 0, 2, 1)


def test_reads_the_ring_from_its_tail() -> None:
    mem = memory(35, {k: record(4, k, 0) for k in range(3, 35)})
    head, found = monster_events.read(mem)
    assert head == 35 and len(found) == 32 and found[0].frame == 3
    assert found[-1] == monster_events.Event("flinch", ENT, 34, 1034, (0, 2), 0, 1)


def test_since_and_no_part() -> None:
    mem = memory(2, {0: record(1, 10), 1: record(6, 11)})
    _, found = monster_events.read(mem, since=1)
    assert [(e.kind, e.part) for e in found] == [("tail_cut", None)]


def test_no_block() -> None:
    with pytest.raises(ConnectionError):
        monster_events.read(Space([Image(bytes(0x100), a.CLI_BRIDGE_BLOCK)]))


def test_state() -> None:
    ent = bytearray(0x800)
    ent[a.ENTITY.AWARE], ent[a.ENTITY.MAIN_STATE], ent[a.ENTITY.FLINCH_MASK] = 1, 1, 4
    struct.pack_into("<H", ent, a.ENTITY.BROKEN, 2)
    player = Image(bytes(0x800), a.PLAYER_ENTITY)
    watch = bytearray(a.MONSTER_WATCH.size or 0)
    struct.pack_into("<I", watch, a.MONSTER_WATCH.ENTITY, ENT)
    watch[a.MONSTER_WATCH.COMBAT] = 1
    st = monster_events.state(memory(0, {}, Image(bytes(ent), ENT), player, watch=watch), ENT)
    assert st == monster_events.State(True, True, True, False, False, (1, 0), 4, 2)
    ent[a.ENTITY.COMBAT_MODE] = 1
    st = monster_events.state(memory(0, {}, Image(bytes(ent), ENT), player), ENT)
    assert (st.noticing, st.combat) == (False, None)
