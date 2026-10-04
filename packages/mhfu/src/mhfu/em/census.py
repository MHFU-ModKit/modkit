# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Which behaviour pairs the engine enters on its own, how long it stays, and whether the monster
moves there: measured from the framework log's `[state]` and `[brute] t=` lines, which the
observe-only probe (brute_dmg.lua) writes at 2 Hz.

A pair the engine never enters bounces straight back out when forced, however good its handler
looks offline. Nothing here estimates: a pair without samples has none. Transitions a script
forced (`FORCED`) are not the monster's choice, so neither the forced visit nor the visit it cut
short is counted.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

Pair = tuple[int, int]

STATE = re.compile(r"\[state\] main=(\d+) sub=(\d+) \(a1=(\d+)\) t=(\d+)(.*)")
TICK = re.compile(r"\[brute\] t=(\d+) sec=(\d+)/(\d+)( SAME)? out=(\d+) in=(\d+) .* d=(\d+)")

MAX_DWELL = 60
"""Ticks; a longer gap between transitions means the probe stopped."""
MAX_DIST = 8000
"""A longer monster-to-player distance is across section frames, not a distance."""
SHORT = 8
"""A mean dwell below this many ticks bounces out."""
STILL, DRIFT = 25, 60
"""Movement per tick below which a pair stands still, or only drifts."""


@dataclass
class Census:
    dwell: dict[Pair, list[int]] = field(default_factory=lambda: defaultdict(list))
    """Ticks spent in the pair, per visit that ended in another transition."""
    anims: dict[Pair, set[int]] = field(default_factory=lambda: defaultdict(set))
    """The executor action ids (a1) seen on entry."""
    moved: dict[Pair, list[int]] = field(default_factory=lambda: defaultdict(list))
    """Change in distance to the player between consecutive co-located ticks in the pair."""
    transitions: int = 0
    """`[state]` lines read, forced ones not counted."""
    forced: int = 0
    """`[state]` lines a script forced, skipped."""


def parse(lines: Iterable[str]) -> Census:
    c = Census()
    states: list[tuple[int, int, int, int, bool]] = []
    ticks: list[tuple[int, bool, int, int, int]] = []
    main = sub = -1
    forced = False
    for line in lines:
        if m := STATE.search(line):
            main, sub, forced = int(m[1]), int(m[2]), "FORCED" in m[5]
            states.append((int(m[4]), main, sub, int(m[3]), forced))
        elif (m := TICK.search(line)) and not forced:
            ticks.append((int(m[1]), bool(m[4]), int(m[7]), main, sub))
    c.forced = sum(s[4] for s in states)
    c.transitions = len(states) - c.forced
    for (t, ma, su, a1, force), (t2, *_, cut) in zip(states, states[1:], strict=False):
        if not (force or cut) and 0 <= t2 - t <= MAX_DWELL:
            c.dwell[ma, su].append(t2 - t)
            c.anims[ma, su].add(a1)
    for prev, row in zip(ticks, ticks[1:], strict=False):
        t, same, dist, ma, su = row
        if (
            same
            and prev[1]
            and prev[3:] == (ma, su)
            and t - prev[0] == 1
            and dist < MAX_DIST
            and prev[2] < MAX_DIST
        ):
            c.moved[ma, su].append(abs(prev[2] - dist))
    return c


def load(path: Path, since: int = 0) -> tuple[Census | None, str]:
    """The census in a log from byte `since` on, or None and why there is none."""
    if not path.exists():
        return None, f"no log at {path}"
    c = parse(path.read_bytes()[since:].decode("utf-8", "replace").splitlines())
    if not c.dwell:
        return None, (
            f"{path} has {c.transitions} [state] line(s) and no usable transitions — "
            "the observe-only probe was not deployed for this run"
        )
    return c, ""


def _mean(values: list[int]) -> float | None:
    return sum(values) / len(values) if values else None


def verdict(c: Census, pair: Pair) -> str:
    dwell, move = _mean(c.dwell.get(pair, [])), _mean(c.moved.get(pair, []))
    if dwell is None:
        return "never entered"
    if dwell < SHORT:
        return "short: bounces out"
    if move is None:
        return "holds, movement unmeasured"
    return "holds, " + ("still" if move < STILL else "drifts" if move < DRIFT else "moves")


def measured(c: Census, pair: Pair) -> dict[str, Any]:
    """One pair's measured block of the species intel; `entered` 0 is a finding, not a gap."""
    d, mv = c.dwell.get(pair, []), c.moved.get(pair, [])
    dwell, move = _mean(d), _mean(mv)
    out: dict[str, Any] = {
        "entered": len(d),
        "dwell_ticks": 0.0 if dwell is None else round(dwell, 2),
        "a1": sorted(c.anims.get(pair, ())),
        "move_per_tick": None if move is None else round(move, 1),
        "move_samples": len(mv),
    }
    if not d:
        out["note"] = (
            f"0 of {c.transitions} observed transitions entered this pair — "
            "forced, it bounces out in one tick"
        )
    elif move is None:
        out["note"] = (
            "never seen on two consecutive ticks with the monster co-located, "
            "so movement is unmeasured, not zero"
        )
    return out
