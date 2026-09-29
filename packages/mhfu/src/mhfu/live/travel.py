"""Fast travel between Pokke's interiors: triangle at any interior exit zone (house, farm,
kitchen, both halls, training school) opens a destination list; square walks out instead.

The six rows are always there in a fixed order (`menus.TRAVEL_MENU`). Where the player is, or
the door next to it, is greyed, and cross on a greyed row does nothing, like a locked quest rank:
`travel_to` raises DestinationUnavailable for it. The interiors have no section id, so arrival
is the player's position jumping to another room's frame.
"""

from __future__ import annotations

from .. import addresses as a
from . import navigation as nav
from .menus import TRAVEL_MENU
from .session import Session

DESTINATIONS = TRAVEL_MENU.rows
MENU_WINDOW = 1.5  # seconds for the list to open or close after a press
CURSOR_WINDOW = 0.45
JUMP = 600.0  # a position change this large means another room


class DestinationUnavailable(RuntimeError):
    """The row is greyed: the player is there already, or next door."""


def menu_is_open(s: Session) -> bool:
    """Fast check; the scene is a pool value, so a False may be wrong (see cursor_responds)."""
    return bool(s.game.scene == a.SCENE_TRAVEL_MENU)


def cursor_responds(s: Session) -> bool:
    """Press down and see whether the destination cursor moves; costs a cursor step.

    The authoritative test: an exit zone's prompt is an unknown scene too, so "no control"
    cannot tell the menu from the doorway.
    """
    row = s.game.travel_cursor
    if row >= len(DESTINATIONS):
        return False
    s.press("down", 3)
    try:
        s.wait(lambda: s.game.travel_cursor != row, CURSOR_WINDOW, "the travel cursor")
    except TimeoutError:
        return False
    return s.game.travel_cursor < len(DESTINATIONS)


def is_open(s: Session) -> bool:
    return menu_is_open(s) or cursor_responds(s)


def open_menu(s: Session, tries: int = 4) -> bool:
    """Press triangle until the list is up; the player must stand in an exit zone."""
    for _ in range(tries):
        if is_open(s):
            return True
        s.press("triangle", 6)
        try:
            s.wait(lambda: menu_is_open(s), MENU_WINDOW, "the travel menu")
        except TimeoutError:
            pass
    return is_open(s)


def close_menu(s: Session, tries: int = 3) -> bool:
    for _ in range(tries):
        if not is_open(s):
            return True
        s.press("circle", 5)
        try:
            s.wait(lambda: not menu_is_open(s), MENU_WINDOW, "the travel menu to close")
        except TimeoutError:
            pass
    return not menu_is_open(s)


def travel_to(s: Session, dest: str | int, timeout: float = 60.0, confirms: int = 3) -> bool:
    """Open the list at the exit zone the player stands in and go to `dest`, a row name or index.

    True once the player has landed in another room; False if no jump came within `timeout`.
    """
    if not open_menu(s):
        raise RuntimeError("triangle opened no travel menu; is the player in an exit zone?")
    before = nav.where(s)
    for _ in range(confirms):
        TRAVEL_MENU.goto(s, dest)  # again after cursor_responds stepped it
        s.press("cross", 5)
        try:
            s.wait(lambda: not menu_is_open(s), MENU_WINDOW, "the travel menu to close")
        except TimeoutError:
            continue
        if not cursor_responds(s):
            break
    else:
        close_menu(s)
        raise DestinationUnavailable(f"{dest!r} is greyed out from here")
    # the player lands on the destination's exit zone, where "has control" reads False
    player = s.game.player
    try:
        s.wait(
            lambda: player.loaded and nav.distance(before, nav.where(s)) > JUMP,
            timeout,
            "arrival",
            poll=0.6,
        )
    except TimeoutError:
        return False
    return s.holds(lambda: player.loaded, 1.2, poll=0.3)
