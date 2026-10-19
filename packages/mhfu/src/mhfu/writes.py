# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Guarded writes into game memory: a `Plan` of `Write`s, each behind bytes the game must
already hold, applied all or nothing and read back.

Every guard of every write is read before anything is written; the first that fails refuses
the whole plan (`Refused`). Callers build the plan; this module is the one place that writes it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from mhfu.memory import Memory, Unmapped

STALE = "the game does not hold what this plan was built against ({why})"
"""The refusal's text; `{why}` is the first failing guard. A caller passes its own."""


class Refused(ValueError):
    """Why nothing was written."""


@dataclass(frozen=True)
class Write:
    """`data` at `at`, once every guard reads back as given; no write changes a guard."""

    what: str
    at: int
    pieces: tuple[tuple[bytes, str], ...]
    """The bytes in order, each with a label for whoever renders the plan."""
    guards: tuple[tuple[int, bytes], ...] = ()

    @property
    def data(self) -> bytes:
        return b"".join(p for p, _ in self.pieces)


@dataclass(frozen=True)
class Plan:
    writes: tuple[Write, ...]
    notes: tuple[str, ...] = ()
    """What the plan left out, and why."""

    @property
    def size(self) -> int:
        return sum(len(w.data) for w in self.writes)


@dataclass
class Pushed:
    plan: Plan
    seconds: float
    """Guards, writes and read-back."""
    mismatched: list[str] = field(default_factory=list)
    """Writes whose read-back is not what was written."""
    where: str = ""

    def describe(self) -> str:
        w = self.plan.writes
        head = (
            f"pushed {len(w)} write(s), {self.plan.size} bytes, into {self.where or 'the game'}"
            f" in {self.seconds:.2f} s"
        )
        if self.mismatched:
            return f"{head}; read back DIFFERS at {', '.join(self.mismatched)}"
        notes = "".join(f"; {n}" for n in self.plan.notes)
        return f"{head}; read back matches{notes}"


def failing(mem: Memory, writes: tuple[Write, ...]) -> str | None:
    """The first guard that does not hold, in words; None when all do."""
    seen: dict[tuple[int, int], bytes] = {}
    for w in writes:
        for at, want in w.guards:
            key = (at, len(want))
            if key not in seen:
                try:
                    seen[key] = mem.read(at, len(want))
                except Unmapped:
                    seen[key] = b""
            if seen[key] != want:
                got = seen[key].hex().upper() or "nothing"
                return f"{w.what}: 0x{at:08X} reads {got}, the plan expects {want.hex().upper()}"
    return None


def apply(mem: Memory, p: Plan, where: str = "", refusal: str = STALE) -> Pushed:
    """Every write of `p`, all or nothing, then read back; `refusal.format(why=...)` on a guard."""
    t0 = time.monotonic()
    why = failing(mem, p.writes)
    if why is not None:
        raise Refused(refusal.format(why=why))
    for w in p.writes:
        mem.write(w.at, w.data)
    bad = [w.what for w in p.writes if mem.read(w.at, len(w.data)) != w.data]
    return Pushed(p, time.monotonic() - t0, bad, where)
