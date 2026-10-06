# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Play in game: an executor entry held on the running game's big monster by the framework's
`cli_bridge.lua`, through `mhfu.live.clips`, which restarts an idle monster's action so the
entry shows at once."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from mhfu import inject
from mhfu.live import clips
from mhfu.live.clips import Played
from mhfu.live.session import Session
from mhfu.live.survival import BIG_MONSTER_VTABLES
from mhfu.memory import Memory
from mhfu.structs import Game

__all__ = ["Held", "attached", "force", "release", "target"]


@dataclass(frozen=True)
class Held:
    slot: int
    """The monster's registry slot."""
    played: Played

    def says(self) -> str:
        """How many body parts play the entry, for the status line."""
        took = sum(p.taken and p.resolved for p in self.played.parts)
        how = f"{took} of {len(self.played.parts)} body parts play it"
        if not self.played.dispatched:
            how = "the monster has not taken it yet"
        return f"anim {self.played.entry} held on monster {self.slot} until Release: {how}"


@contextmanager
def attached(timeout: float = 3.0) -> Iterator[Session]:
    """The running game: `$MHFU_LANE`'s PPSSPP, else the one on this machine."""
    lane = inject.lane()
    s = Session.attach(None if lane is None else lane.port, timeout=timeout, wait_for_game=False)
    try:
        yield s
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


def force(s: Session, entry: int, species: int | None) -> Held:
    """Holds executor entry `entry` until `release`, and what each body part then plays."""
    slot = target(s.mem, species)
    return Held(slot, clips.play(s, entry, slot=slot, link=clips.bridge(s)))


def release(s: Session, species: int | None) -> tuple[int, bool]:
    """Ends the hold: the slot, and whether the script acked."""
    slot = target(s.mem, species)
    return slot, clips.release(s, slot, clips.bridge(s))
