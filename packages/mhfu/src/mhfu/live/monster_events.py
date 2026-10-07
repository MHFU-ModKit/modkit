# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The framework's monster events (`framework/include/mhfu/monster_events.h`) read from its block:
the ring the move player's step writes each AI frame, before the registry poll raises the records
to mods. Reading does not consume them.

    with Rig.attach() as rig:
        head, found = read(rig.s)                 # the ring's last records
        for ev in follow(rig.s, 30.0): print(ev)  # what happens in the next 30 s

The block is found through the move player's (MOVE_STATE.EVENTS), which cli_bridge.lua publishes:
a cold boot with the framework and the bridge (`mhfu move ride`).
"""

from __future__ import annotations

import struct
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from .. import addresses as a
from ..memory import Memory
from .session import Session

KINDS = ("noticed", "combat_entered", "combat_left", "flinch", "part_broken", "tail_cut")
"""MONSTER_EVENT.KIND 1.. in order."""
NO_PART = 0xFF
_RECORD = struct.Struct("<IIIBBBBH2x")


@dataclass(frozen=True)
class Event:
    kind: str
    entity: int
    frame: int
    """The monster's AI frame the change was seen in."""
    usec: int
    """The emulated clock then, cut to 32 bits."""
    pair: tuple[int, int]
    part: int | None
    data: int

    def __str__(self) -> str:
        part = "" if self.part is None else f" part {self.part}"
        main, sub = self.pair
        return (
            f"{self.kind:<15} 0x{self.entity:08X} frame {self.frame} ({main},{sub})"
            f"{part} data 0x{self.data:X}"
        )


def unpack(raw: bytes) -> Event:
    """One MONSTER_EVENT record."""
    entity, frame, usec, kind, part, main, sub, data = _RECORD.unpack_from(raw)
    name = KINDS[kind - 1] if 1 <= kind <= len(KINDS) else f"kind {kind}"
    return Event(name, entity, frame, usec, (main, sub), None if part == NO_PART else part, data)


def block(mem: Memory) -> int:
    """The MONSTER_EVENTS block, through the move player's block cli_bridge.lua published."""
    moves = mem.u32(a.CLI_BRIDGE_BLOCK + a.CLI_BRIDGE.MOVE_STATE)
    at = mem.u32(moves + a.MOVE_STATE.EVENTS) if moves else 0
    if not at:
        raise ConnectionError("no monster-event block: cold boot the framework and the bridge")
    return at


def read(mem: Memory, since: int | None = None, at: int | None = None) -> tuple[int, list[Event]]:
    """HEAD and the records from `since` (default: as far back as the ring holds) to it."""
    at = block(mem) if at is None else at
    head = mem.u32(at + a.MONSTER_EVENTS.HEAD)
    ring = a.MONSTER_EVENTS.RING
    n, size = ring.count or 0, a.MONSTER_EVENT.size or 0
    first = max(head - n, 0 if since is None else since)
    out = [unpack(mem.read(at + ring + (k % n) * size, size)) for k in range(first, head)]
    return head, out


def follow(
    s: Session, seconds: float, poll: float = 0.25, log: Callable[[Event], None] | None = None
) -> Iterator[Event]:
    """The records written in the next `seconds`, as they come."""
    at = block(s.mem)
    head, _ = read(s.mem, at=at)
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        time.sleep(poll)
        head, found = read(s.mem, head, at)
        for ev in found:
            if log:
                log(ev)
            yield ev
