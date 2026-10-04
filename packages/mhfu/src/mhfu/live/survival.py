# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Keeping a scripted run alive: the quest clock, the player's HP, and calm big monsters.

These are debugger writes, not play: a guarded run shows that a route exists, not that a hunter
survives it. Calming beats healing, because a big monster knocks the player down and drags him
and a tumbled walk makes no progress at any HP. A zero species sight radius stops new aggro (it
is read on every evaluation) and a zero ENGAGE drops the aggro already there.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Collection, Iterable
from types import TracebackType

from ppsspp_debug import DebuggerError, Disconnected

from .. import addresses as a
from ..structs import Entity
from .session import Session

FULL_TIMER = 90000  # quest frames at 30 Hz, 50 minutes, what a new quest starts with
BIG_MONSTER_VTABLES = frozenset({a.TIGREX_VTABLE})


def big_monsters(s: Session, vtables: Collection[int] = BIG_MONSTER_VTABLES) -> list[Entity]:
    """Registry entities whose class is one of `vtables`."""
    return [e for e in s.game.monsters().values() if e.vtable in vtables]


def hold_timer(s: Session, frames: int = FULL_TIMER) -> None:
    """Set the quest clock; both cells, or the second one runs on."""
    s.game.quest.timer = frames
    s.game.quest_timer_mirror = frames


def top_up_hp(s: Session, hp: int = 100) -> None:
    player = s.game.player
    player.hp_cap = hp
    player.hp = hp


def calm(s: Session, monsters: Iterable[Entity]) -> None:
    """Zero the sight radius of each monster's species and each monster's ENGAGE."""
    monsters = list(monsters)
    for species in {m.species for m in monsters}:
        s.game.species(species).sight_radius = 0.0
    for m in monsters:
        m.engage = 0.0


class Guard:
    """Holds the clock, the player's HP and calm big monsters from a thread.

        with survival.Guard(s) as guard:
            ...
        print(guard.report())

    `hp=None` leaves HP alone, the honest setting when the question is whether a hunter survives.
    The writes are blind: reading first only delays the one write that must not be late.
    """

    def __init__(
        self,
        s: Session,
        *,
        hp: int | None = 100,
        timer: bool = True,
        calm_monsters: bool = True,
        tick: float = 0.5,
        rescan: float = 4.0,
        timer_every: float = 15.0,
        timer_frames: int = FULL_TIMER,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.s = s
        self.hp = hp
        self.timer = timer
        self.calm_monsters = calm_monsters
        self.tick = tick
        self.rescan = rescan
        self.timer_every = timer_every
        self.timer_frames = timer_frames
        self.log = log or (lambda _: None)
        self.monsters: list[Entity] = []
        self.hp_writes = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> Guard:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mhfu-guard", daemon=True)
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def _run(self) -> None:
        next_timer = next_rescan = self.s.now()
        while not self._stop.is_set():
            now = self.s.now()
            try:
                if self.timer and now >= next_timer:
                    hold_timer(self.s, self.timer_frames)
                    next_timer = now + self.timer_every
                if self.calm_monsters and now >= next_rescan:
                    found = big_monsters(self.s)
                    if [m.base for m in found] != [m.base for m in self.monsters]:
                        self.log(f"guard: big monsters {[hex(m.base) for m in found]}")
                        self.monsters = found
                    next_rescan = now + self.rescan
                if self.calm_monsters:
                    calm(self.s, self.monsters)
                if self.hp is not None:
                    top_up_hp(self.s, self.hp)
                    self.hp_writes += 1
            except Disconnected:
                return
            except (DebuggerError, TimeoutError) as e:
                self.log(f"guard: {e}")
            self._stop.wait(self.tick)

    def report(self) -> str:
        held = []
        if self.hp is not None:
            held.append(f"hp pinned at {self.hp} ({self.hp_writes} writes)")
        if self.timer:
            held.append("clock held")
        if self.calm_monsters:
            held.append(f"{len(self.monsters)} big monster(s) calmed")
        return "guard: " + (", ".join(held) or "nothing held")
