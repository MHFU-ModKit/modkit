# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The framework's brain rules, read from game memory: how often each rule has fired.

em_vhook's config block (`EM_CFG`) lives in partition memory at an address that moves with the PRX
build, and nothing publishes it. Its word at `EM_CFG.CANARY` is a fixed value, so `locate` finds the
block by scanning for it; `read` then takes the rules in one request.

    with Rig.attach() as rig:
        cfg = brain.locate(rig.s)             # cache it for the attach
        for slot in brain.read(rig.s, cfg).slots:
            print(slot.index, slot.fired)

A slot's index is the rule's place in `mhfu_port.behaviour.compile(manifest)`. Counters restart with
each quest and whenever the port's moves module is re-run. A memory request stops the emulator for
a few ms, so read about once a second, never faster.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

from .. import addresses as a
from ..memory import Memory, Unmapped

CANARY = 0x5645484B
"""`EM_CFG.CANARY`'s value: 'VEHK' (em_vhook.cpp CFG_CANARY_VAL)."""
UNLIMITED = 0xFFFFFFFF
"""`Slot.left` of a standing rule."""
NO_PLAY = 0x04
"""`EM_RULE.FLAGS` bit: the rule plays nothing and only applies its effects."""
CHUNK = 0x40000
"""Bytes per request while scanning."""
WINDOW = 0x200000
"""The scan's first window above `FRAMEWORK_PRX`, where the block sits."""


class Reader(Protocol):
    """What `locate` and `read` need of a session: its memory."""

    @property
    def mem(self) -> Memory: ...


@dataclass(frozen=True)
class Slot:
    """One installed rule, as the block holds it."""

    index: int
    on: int
    """The monster event that fires it (`MONSTER_EVENT_KIND` number); 0 for none."""
    signal: int
    """The board signal that fires it; -1 for none."""
    plays: bool
    """False for a rule that only applies its effects."""
    min_frames: int
    cooldown: int
    fired: int
    left: int
    """Fires left: `UNLIMITED` for a standing rule, 0 once spent."""
    last_fire: int
    """The AI tick of the last fire."""


@dataclass(frozen=True)
class Brain:
    cfg: int
    ticks: int
    """AI ticks since the quest began."""
    slots: tuple[Slot, ...]
    """The installed rules, by slot."""


def _scan(mem: Memory, lo: int, hi: int) -> Iterator[int]:
    """The blocks whose canary word lies in [lo, hi): the word is aligned, so no chunk splits it."""
    word = struct.pack("<I", CANARY)
    for at in range(lo, hi, CHUNK):
        try:
            raw = mem.read(at, min(CHUNK, hi - at))
        except Unmapped:
            continue
        k = raw.find(word)
        while k >= 0:
            if (at + k) % 4 == 0:
                yield at + k - a.EM_CFG.CANARY
            k = raw.find(word, k + 1)


def _live(mem: Memory, entity: int) -> bool:
    """Whether `entity` is a monster in the entity registry."""
    registry = a.ENTITY_REGISTRY
    ptrs = struct.unpack(f"<{registry.count}I", mem.read(registry, 4 * (registry.count or 0)))
    return entity != 0 and entity in ptrs[1:]


def _board() -> int | None:
    """Where the board's ENTITY word is in the block; None for a build with no board."""
    board = a.table().structs.get("EM_BOARD")
    at = getattr(a.EM_CFG, "BOARD", None)
    return None if board is None or at is None else int(at) + int(board.ENTITY)


def _owned(mem: Memory, cfg: int) -> bool:
    """Whether the board belongs to a live monster; a build with no board has none to ask."""
    at = _board()
    return at is None or _live(mem, mem.u32(cfg + at))


def locate(s: Reader) -> int | None:
    """The config block: the canary found in the window above the PRX, else in user RAM, and the
    board it holds names a live monster. None while there is no such block, which is also the case
    from a quest's start until a monster of the wrapped species takes its first AI step. The caller
    keeps the answer for the attach."""
    mem = s.mem
    first = int(a.FRAMEWORK_PRX)
    seen = False
    for lo, hi in ((first, first + WINDOW), (int(a.USER_RAM), int(a.USER_RAM_END))):
        for cfg in _scan(mem, lo, hi):
            seen = True
            if _owned(mem, cfg):
                return cfg
        if seen:  # the block is there: a scan of the rest would find no other
            return None
    return None


def read(s: Reader, cfg: int) -> Brain:
    """The block's tick count and rules, in one request."""
    rules = a.EM_CFG.RULES
    n, size = rules.count or 0, a.EM_RULE.step
    raw = s.mem.read(cfg, int(rules) + n * size)
    (ticks,) = struct.unpack_from("<I", raw, a.EM_CFG.AI_TICKS)
    slots = []
    for i in range(n):
        slot = _decode(i, raw[int(rules) + i * size : int(rules) + (i + 1) * size])
        if slot is not None:
            slots.append(slot)
    return Brain(cfg, ticks, tuple(slots))


def _decode(index: int, r: bytes) -> Slot | None:
    """Rule `index` of the block, or None for a slot nothing is installed in."""
    rule = a.EM_RULE
    left, fired, last = struct.unpack_from("<3I", r, rule.LEFT)
    on = r[rule.ON]
    signal_at = getattr(rule, "SIGNAL", None)
    signal = r[signal_at] - 1 if signal_at is not None and r[signal_at] else -1
    triggered = bool(r[rule.FROM_MASK] or r[rule.FROM_MOVE] or on or signal >= 0)
    if not (triggered or fired):
        return None
    (min_frames,) = struct.unpack_from("<I", r, rule.MIN_FRAMES)
    (cooldown,) = struct.unpack_from("<I", r, rule.COOLDOWN)
    return Slot(
        index, on, signal, not r[rule.FLAGS] & NO_PLAY, min_frames, cooldown, fired, left, last
    )
