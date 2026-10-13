# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Watch the game: how often the running game's brain fired each rule of the open port.

`mhfu.live.brain` reads the brain's rule slots from the game; `match` says whether they are this
manifest's rules, `Watch` is the state of the Behaviour tab's toggle: it asks for a read at most
once a second (every memory request stalls the emulator about 10 ms) and keeps what the marks
show. No toolkit.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import Future
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, NamedTuple

from mhfu.live import brain as live
from mhfu.live.brain import Brain, Reader
from mhfu_port import behaviour
from mhfu_port.behaviour import Trigger
from mhfu_port.manifest import Manifest, ManifestError
from ppsspp_debug import DebuggerError

if TYPE_CHECKING:
    from mhfu.live.session import Session

PERIOD, FLASH, RETRY = 1.0, 1.5, 10.0
"""Seconds: between two reads, a rule's frame lit after its count rose, and between two tries to
find the brain (the scan of memory stalls the game for a second or two)."""
NO_GAME = "no game running"
NOT_FOUND = "the framework's rules were not found"
OTHER_BUILD = "the game runs another build of these rules: Play in game sends them"
READING = "reading the game…"
DOT = "·"
NO_SIGNAL = -1
"""`Slot.signal` of a rule no signal fires."""


class Read(NamedTuple):
    """What one read of the game brought: where the brain is (None: not found) and what it
    holds."""

    cfg: int | None
    brain: Brain | None


def fetch(attach: Callable[[], AbstractContextManager[Session]], cfg: int | None) -> Read:
    """Attaches to the game, finds the brain unless `cfg` says where it is, and reads it. The
    seam the tests fake; it runs on a worker."""
    with attach() as s:
        found = live.locate(s) if cfg is None else cfg
        if found is None:
            return Read(None, None)
        return Read(found, live.read(s, found))


def kind(event: behaviour.Event | None) -> int:
    """The number the game's rule slot holds for `event` (MONSTER_EVENT_KIND counted from 1); 0
    for none."""
    return 0 if event is None else behaviour.EVENTS.index(event) + 1


def match(brain: Brain, m: Manifest) -> dict[int, int] | str:
    """The fire count of each rule of `m`, by its place in `paths(m)` counted from 1; or why the
    brain is not running them: every slot must say what the compiled rule says. Slots past the
    last rule are not looked at."""
    try:
        rules = behaviour.compile(m)
    except ManifestError as e:
        return f"these rules do not compile: {e}"
    slots = {s.index: s for s in brain.slots}
    out: dict[int, int] = {}
    for k, rule in enumerate(rules):
        s = slots.get(k)
        same = s is not None and (
            s.on == kind(rule.on)
            and s.signal == (NO_SIGNAL if rule.signal is None else rule.signal)
            and s.plays == bool(rule.play)
            and s.min_frames == rule.min_frames
            and s.cooldown == rule.cooldown
        )
        if s is None or not same:
            return OTHER_BUILD
        out[k + 1] = s.fired
    return out


Writer = Callable[[Reader, int, Manifest, Trigger], str]
"""Raises a trigger in the game at the brain `cfg`; says what it did."""


def _signal(s: Reader, cfg: int, m: Manifest, t: Trigger) -> str:
    index = behaviour.signals(m)[t.name]
    live.fire_signal(s, cfg, index)
    return f"raised signal {t.name} in the game (board signal {index})"


def _enrage(s: Reader, cfg: int, m: Manifest, t: Trigger) -> str:
    live.set_rage(s, cfg, True)
    return "asked the game to enrage the monster (no effect while it is enraged)"


def _calm(s: Reader, cfg: int, m: Manifest, t: Trigger) -> str:
    live.set_rage(s, cfg, False)
    return "asked the game to calm the monster (no effect while it is calm)"


FIRE: dict[str, Writer] = {"on_signal": _signal, "on_enraged": _enrage, "on_calmed": _calm}
"""What Fire can raise in the running game, by the trigger's kind, and how: the one table. A
trigger with no entry has no Fire button."""


class Watch:
    """The state of Watch the game: `fires` (rank to count; None when not watching or there is
    nothing to show) and `status` say what the marks show, `lit` which frames flash."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.on = False
        self.fires: dict[int, int] | None = None
        self.status = ""
        self._flash: dict[int, float] = {}
        self._cfg: int | None = None
        self._job: Future[Read] | None = None
        self._asked = self._lost = float("-inf")
        self._since: int | None = None

    @property
    def cfg(self) -> int | None:
        """Where the brain was found in this attach; None until it is."""
        return self._cfg

    def blocker(self) -> str | None:
        """Why the game cannot be written to now: it is not watched, not running, its brain not
        found or not these rules. None when the brain is found and runs this port's rules."""
        if not self.on:
            return "turn on Watch the game first: it finds the framework in the game"
        if self.fires is None or self._cfg is None:
            return (
                self.status
                if self.status != READING
                else "Watch the game has not read the game yet"
            )
        return None

    def set(self, on: bool) -> None:
        self.on = on
        self.reset()

    def reset(self) -> None:
        """Forgets what was read: a new document, or the toggle changed."""
        self.fires, self._cfg, self._job, self._since = None, None, None, None
        self.status = READING if self.on else ""
        self._flash = {}
        self._asked = self._lost = float("-inf")

    def lit(self) -> frozenset[int]:
        """The ranks whose count rose in the last `FLASH` seconds."""
        now = self.clock()
        self._flash = {r: t for r, t in self._flash.items() if t > now}
        return frozenset(self._flash)

    def poll(
        self,
        m: Manifest | None,
        running: bool,
        ask: Callable[[int | None], Future[Read]],
    ) -> bool:
        """Takes a finished read, and asks for the next when `PERIOD` has passed (`ask` runs it
        on a worker, told where the brain was last found). True when what the marks show
        changed."""
        if not self.on or m is None:
            return False
        before = (self.fires, self.status, self.lit())
        if self._job is not None and self._job.done():
            job, self._job = self._job, None
            self._took(m, job)
        elif self._job is not None:
            return False
        now = self.clock()
        if not running:
            self.reset()
            self.status = NO_GAME
        elif now - self._asked >= PERIOD and (self._cfg is not None or now - self._lost >= RETRY):
            self._asked = now
            self._job = ask(self._cfg)
        return before != (self.fires, self.status, self.lit())

    def _took(self, m: Manifest, job: Future[Read]) -> None:
        now = self.clock()
        try:
            got = job.result()
        except (LookupError, OSError, ValueError, DebuggerError) as e:
            self._cfg = None  # a new attach finds it again
            self._fail(f"the game did not answer: {e}")
            return
        self._cfg = got.cfg
        if got.cfg is None or got.brain is None:
            self._lost = now
            self._fail(NOT_FOUND)
            return
        found = match(got.brain, m)
        if isinstance(found, str):
            self._fail(found)
            return
        old = self.fires or {}
        self._flash |= {r: now + FLASH for r, n in found.items() if r in old and n > old[r]}
        if any(n < old.get(r, 0) for r, n in found.items()):  # a new quest, or the module re-ran
            self._since = got.brain.ticks
        self.fires = found
        tick = got.brain.ticks if self._since is None else self._since
        self.status = (
            f"watching {DOT} {'AI tick' if self._since is None else 'since AI tick'} {tick}"
        )

    def _fail(self, why: str) -> None:
        self.fires, self._since, self._flash = None, None, {}
        self.status = why
