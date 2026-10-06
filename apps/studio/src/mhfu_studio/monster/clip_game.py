# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Play in game: an executor entry forced on the running game's big monster and held by the
framework's `cli_bridge.lua`, as `mhfu shell`'s `anim play` does (`mhfu.live.shell_anim`)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from mhfu import inject
from mhfu.live.session import Session
from mhfu.live.shell_anim import ACK_TIMEOUT, STALLED, Bridge, Op
from mhfu.live.survival import BIG_MONSTER_VTABLES
from mhfu.memory import Memory, Unmapped
from mhfu.structs import Game

UNREACHABLE = (
    "the bridge is not up: it needs the framework's memory=64 and cli_bridge.lua in the stick's"
    " mods, from a cold boot"
)
POLL = 0.05
__all__ = ["STALLED", "Sent", "attached", "force", "release", "send", "target"]


@dataclass(frozen=True)
class Sent:
    slot: int
    """The monster's registry slot."""
    seq: int
    acked: bool


@contextmanager
def attached(timeout: float = 3.0) -> Iterator[Memory]:
    """The running game's memory: `$MHFU_LANE`'s PPSSPP, else the one on this machine."""
    lane = inject.lane()
    s = Session.attach(None if lane is None else lane.port, timeout=timeout, wait_for_game=False)
    try:
        yield s.mem
    finally:
        s.close()


def target(mem: Memory, species: int | None) -> int:
    """The registry slot of the big monster to hold: the first of `species`, else the first of
    a known big-monster class."""
    found = sorted(Game(mem).monsters().items())
    for want in (lambda e: e.species == species, lambda e: e.vtable in BIG_MONSTER_VTABLES):
        slot = next((k for k, e in found if want(e)), None)
        if slot is not None:
            return slot
    raise LookupError("no big monster in the entity registry: is a quest with it running?")


def _bridge(mem: Memory) -> Bridge:
    bridge = Bridge(mem)
    try:
        up = bridge.reachable()
    except Unmapped:
        up = False
    if not up:
        raise LookupError(UNREACHABLE)
    return bridge


def send(mem: Memory, op: Op, species: int | None, arg: int = 0, wait: float = ACK_TIMEOUT) -> Sent:
    """`op` for the big monster, and whether the script acked it within `wait` seconds."""
    bridge = _bridge(mem)
    slot = target(mem, species)
    seq = bridge.send(op, slot, arg)
    for _ in range(round(wait / POLL)):
        if bridge.block.ack == seq:
            break
        time.sleep(POLL)
    return Sent(slot, seq, bridge.block.ack == seq)


def force(mem: Memory, entry: int, species: int | None, wait: float = ACK_TIMEOUT) -> Sent:
    """Holds executor entry `entry` until `release`."""
    return send(mem, Op.FORCE_ACTION, species, entry, wait)


def release(mem: Memory, species: int | None, wait: float = ACK_TIMEOUT) -> Sent:
    return send(mem, Op.CLEAR, species, 0, wait)
