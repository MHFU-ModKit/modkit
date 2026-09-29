"""The player's house in Pokke: its exit, item box, bed and kitchen door.

Like the hall it is its own area with its own world frame and scratch screen bytes, but it has
no NPCs to fingerprint, so `in_home` is a bounding box that is not the hall. Its four fixtures
are square-prompt zones located by where the player stands.

The bed saves the game, and its yes/no defaults to yes, so a cross near the bed overwrites the
save. Nothing here presses cross there; circle backs out.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import dialog, hall, travel
from . import navigation as nav
from .navigation import XZ
from .session import POLL, Session

# the village side: the door is up the village from the spawn, by the travelling merchant
DOOR_XZ = (10270.0, 11420.0)
DOOR_ROUTE = ((10800.0, 11900.0), (10500.0, 11600.0), DOOR_XZ)

BOUNDS = (1780.0, 600.0, 2450.0, 1420.0)  # x_lo, z_lo, x_hi, z_hi, from walking into the walls
ROOM_CENTRE = (2200.0, 1050.0)  # open floor on no zone; every approach starts here
ON_ZONE = 150.0
MENU_WINDOW = 1.6


@dataclass(frozen=True)
class Fixture:
    """A zone: stand-off points to walk to, then points past the zone to creep at, in turn."""

    walk: tuple[XZ, ...]
    creep: tuple[XZ, ...]
    zone: XZ


# Approaches that hit, measured from ROOM_CENTRE: each zone is small and wedged against
# furniture, and the side the player comes from decides whether it fires. The bed only fires
# from its head; past its foot it cannot be reached at all.
FIXTURES = {
    "exit": Fixture(
        ((2300.0, 1060.0),),
        ((2380.0, 1230.0), (2300.0, 1260.0), (2430.0, 1160.0)),
        (2360.0, 1200.0),
    ),
    "item_box": Fixture(((2250.0, 1300.0),), ((2050.0, 1290.0),), (2120.0, 1260.0)),
    "bed": Fixture(
        ((2250.0, 950.0),), ((2380.0, 730.0), (2350.0, 800.0), (2360.0, 760.0)), (2346.0, 740.0)
    ),
    "kitchen": Fixture(((2000.0, 950.0),), ((1850.0, 780.0),), (1900.0, 900.0)),
}


class NotInHome(RuntimeError):
    """A house step was asked for somewhere that is not the house."""


def in_home(s: Session) -> bool:
    """True while the player stands in the house; small coordinates in a quest area fool it."""
    x, z = nav.where(s)
    x_lo, z_lo, x_hi, z_hi = BOUNDS
    return x_lo <= x <= x_hi and z_lo <= z <= z_hi and not hall.in_hall(s)


def _require(s: Session) -> None:
    if not in_home(s):
        raise NotInHome("not in the player's house")


def _wait_for_room(s: Session, inside: bool, timeout: float) -> bool:
    def there() -> bool:
        return in_home(s) == inside

    try:
        s.wait(lambda: there() and s.holds(there, 0.6, poll=0.3), timeout, "the room", poll=0.4)
    except TimeoutError:
        return False
    return True


def enter(s: Session, timeout: float = 60.0, attempts: int = 6) -> bool:
    """Walk to the house door in the village and go in; True once inside."""
    dialog.dismiss(s)
    nav.walk_path(s, DOOR_ROUTE, tolerance=90, timeout=60.0)
    for i in range(attempts):
        nav.walk_to(s, *DOOR_XZ, tolerance=60, timeout=15.0)
        nav.creep_to(s, *DOOR_XZ, seconds=6)
        s.press("square", 8)
        if _wait_for_room(s, True, timeout / attempts + 8):
            return True
        nudge = 60 if i % 2 else -60
        nav.walk_to(s, DOOR_XZ[0] + nudge, DOOR_XZ[1] + nudge, tolerance=45, timeout=12.0)
    return in_home(s)


def leave(s: Session, timeout: float = 60.0, attempts: int = 6) -> bool:
    """Step out of the front door into the village; True once out."""
    _require(s)
    dialog.dismiss(s)
    for _ in range(attempts):
        if not stand_at(s, "exit"):
            continue
        s.press("square", 8)
        if _wait_for_room(s, False, timeout / attempts + 8):
            return True
    return not in_home(s)


def stand_at(s: Session, fixture: str, seconds: float = 10.0, tries: int = 2) -> bool:
    """Put the player on `fixture`'s zone, its prompt showing.

    The walk starts from ROOM_CENTRE, off every zone: `creep_to` stops on any prompt, so from
    another fixture's zone it would succeed at once.
    """
    try:
        f = FIXTURES[fixture]
    except KeyError:
        raise KeyError(f"no fixture {fixture!r}; one of {sorted(FIXTURES)}") from None
    for _ in range(tries):
        if not in_home(s):
            return False
        if dialog.modal_is_up(s):  # a menu left open swallows the stick
            close_menu(s)
        nav.walk_to(s, *ROOM_CENTRE, tolerance=80, timeout=20.0)
        for x, z in f.walk:
            nav.walk_to(s, x, z, tolerance=70, timeout=25.0)
        for x, z in f.creep:
            # a prompt is not enough: every zone looks alike, and square on the exit leaves
            if nav.creep_to(s, x, z, seconds=seconds) and on_zone(s, f.zone):
                return True
    return False


def on_zone(s: Session, zone: XZ) -> bool:
    return nav.distance(nav.where(s), zone) <= ON_ZONE


def open_item_box(s: Session, tries: int = 4) -> bool:
    """Open the item box and leave its menu (`menus.ITEM_BOX_MENU`) up."""
    _require(s)
    if not stand_at(s, "item_box"):
        return False
    for _ in range(tries):
        s.press("square", 6)
        try:
            s.wait(lambda: dialog.modal_is_up(s), MENU_WINDOW, "the item box")
            return True
        except TimeoutError:
            pass
        if not in_home(s):
            raise NotInHome("the item box press left the house; it was the wrong zone")
    return False


def _scenes(s: Session, samples: int = 6) -> set[int]:
    seen = set()
    for i in range(samples):
        if i:
            s.sleep(POLL)
        seen.add(s.game.scene)
    return seen


def close_menu(s: Session, tries: int = 4) -> bool:
    """Back out of a fixture's menu with circle, never cross (one fixture is the bed).

    Closed means the menu's scene values are gone: the player lands back on the fixture's zone,
    an unknown scene, so waiting for control would never end.
    """
    for _ in range(tries):
        menu = _scenes(s)
        if menu <= dialog.WALKING:
            return True
        s.press("circle", 5)
        try:
            s.wait(lambda: not menu & _scenes(s), 1.2, "the menu to close")  # noqa: B023
            return True
        except TimeoutError:
            pass
    return False


def at_save_point(s: Session) -> bool:
    """True while the player stands on the bed's zone with its prompt up (do not press cross)."""
    if not in_home(s) or not on_zone(s, FIXTURES["bed"].zone):
        return False
    # a zone alternates with a walking scene, so one read is a coin flip
    return bool(_scenes(s) - dialog.WALKING)


def travel_to(s: Session, dest: str | int, attempts: int = 3) -> bool:
    """Fast-travel out of the house from its exit zone (`travel.DESTINATIONS`)."""
    _require(s)
    last: Exception = NotInHome("could not reach the house's exit zone")
    for _ in range(attempts):
        if not stand_at(s, "exit"):
            continue
        try:
            return travel.travel_to(s, dest)
        except travel.DestinationUnavailable:
            raise
        except (RuntimeError, TimeoutError) as e:  # stopped short of the zone; come at it again
            last = e
    raise last
