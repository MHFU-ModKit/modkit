"""The base-camp supply box (the blue chest), and the map in its first slot.

With the map taken the minimap shows the whole map with every area numbered, which makes
"which section is this?" a glance. The item never shows in PLAYER_BAG, so only the minimap
confirms it was taken; the open grid is SCENE_SUPPLY_BOX.

The box's trigger is a small spot off to one side of its face, not the whole face, so
`open_box` sweeps along the face between tries.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import addresses as a
from . import navigation as nav
from .navigation import XZ
from .session import Session

SETTLE = 0.7  # seconds for the player to stop after the shove
GRID_WINDOW = 1.5
TAKE_WINDOW = 1.6  # nothing in memory shows the take, so give it this long


@dataclass(frozen=True)
class SupplyBox:
    """Where to stand, and the world heading (degrees) to shove from there. Per map."""

    stand: XZ
    heading: float
    seconds: float = 1.8


SNOWY_MOUNTAINS = SupplyBox(stand=(14520.0, 15880.0), heading=0.0)
"""Past the red delivery box, toward the camp exit; the player wedges at the box's west end."""


def is_open(s: Session) -> bool:
    return bool(s.game.scene == a.SCENE_SUPPLY_BOX)


def open_box(
    s: Session, box: SupplyBox = SNOWY_MOUNTAINS, attempts: int = 6, sweep: float = 40.0
) -> bool:
    """Wedge against the box and press circle until the grid is up, shifting along its face."""
    if is_open(s):
        return True
    for attempt in range(attempts):
        offset = (attempt + 1) // 2 * sweep * (1 if attempt % 2 else -1)
        nav.walk_to(s, box.stand[0] + offset, box.stand[1], tolerance=60, timeout=35.0)
        nav.push(s, box.heading, box.seconds)
        s.sleep(SETTLE)
        s.press("circle", 6)
        try:
            s.wait(lambda: is_open(s), GRID_WINDOW, "the supply box")
            return True
        except TimeoutError:
            pass
    return False


def take_selected(s: Session) -> None:
    """Cross on the highlighted slot; slot 0 of a fresh quest's box is the map."""
    s.press("cross", 6)
    s.sleep(TAKE_WINDOW)


def close_box(s: Session, tries: int = 4) -> bool:
    for _ in range(tries):
        if not is_open(s):
            return True
        s.press("circle", 6)
        try:
            s.wait(lambda: not is_open(s), 1.4, "the supply box to close")
        except TimeoutError:
            pass
    return not is_open(s)


def take_map(s: Session, box: SupplyBox = SNOWY_MOUNTAINS) -> bool:
    """Open the box, take slot 0 (the map) and close it; False if the grid never opened."""
    if not open_box(s, box):
        return False
    take_selected(s)
    return close_box(s)
