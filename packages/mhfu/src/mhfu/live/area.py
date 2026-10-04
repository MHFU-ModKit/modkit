# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Which section the player is in, and waiting out the loads between sections.

AREA_INDEX (`s.game.area_index`) names the section. MAP_SUBSECTION collides across sections
(snowy mountains s2, s4 and s6 all read 1), churns during a load and is scratch in the hall, so
it serves only to spot the village. A gate load takes SCREEN_STATE from IN_AREA through MENU
and back, and AREA_INDEX is valid once it is IN_AREA again.
"""

from __future__ import annotations

from collections.abc import Callable

from ..structs import Screen
from .session import Session

VILLAGE = 35  # MAP_SUBSECTION in Pokke village, where SCREEN_STATE reads BOOT


def in_area(s: Session) -> bool:
    """True while the player stands in a quest area (not in the village, not loading)."""
    return s.game.screen_state == Screen.IN_AREA


def settled(
    s: Session, read: Callable[[], int | None], what: str, samples: int, gap: float, timeout: float
) -> int:
    """`read()` once it has returned one value `samples` times in a row; None breaks the run."""
    run: list[int | None] = []

    def steady() -> bool:
        run.append(read())
        del run[:-samples]
        return len(run) == samples and run[0] is not None and len(set(run)) == 1

    s.wait(steady, timeout, what, poll=gap)
    value = run[-1]
    assert value is not None
    return value


def settled_index(s: Session, samples: int = 3, gap: float = 0.25, timeout: float = 20.0) -> int:
    """AREA_INDEX once it holds still in an area."""
    return settled(
        s,
        lambda: s.game.area_index if in_area(s) else None,
        "settled area index",
        samples,
        gap,
        timeout,
    )


def settled_subsection(
    s: Session, samples: int = 4, gap: float = 0.25, timeout: float = 15.0
) -> int:
    """MAP_SUBSECTION once it holds still; it is written about 0.4 s after a load ends."""
    return settled(s, lambda: s.game.map_subsection, "settled subsection", samples, gap, timeout)


def wait_for_in_area(s: Session, timeout: float = 90.0) -> int:
    """Wait until the player stands in a quest area, as after departing; returns AREA_INDEX."""
    s.wait(lambda: in_area(s), timeout, "quest area", poll=0.2)
    return settled_index(s)


def wait_for_transition(s: Session, start: int | None = None, timeout: float = 60.0) -> int:
    """Wait out a zone load that starts in an area, and return the new AREA_INDEX.

    `start` is the section left (the current one by default); a load that has already ended in
    another section returns at once. Departing the village is not a transition: use
    `wait_for_in_area`.
    """
    start = s.game.area_index if start is None else start
    deadline = s.now() + timeout
    s.wait(lambda: not in_area(s) or s.game.area_index != start, timeout, "zone load", poll=0.15)
    s.wait(lambda: in_area(s), max(deadline - s.now(), 0.0), "end of the zone load", poll=0.15)
    return settled_index(s)


def wait_for_index_change(s: Session, other_than: int, timeout: float = 25.0) -> int:
    """Wait until AREA_INDEX is not `other_than`, and return it once settled."""
    s.wait(lambda: s.game.area_index != other_than, timeout, "section change", poll=0.25)
    return settled_index(s)
