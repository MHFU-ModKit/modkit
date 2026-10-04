# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Pokke gathering hall: getting in and out, its quest counters, and its fixtures.

The hall is its own area with its own world frame, entered through a door in the village.
Inside, SCREEN_STATE and MAP_SUBSECTION are scratch and the NPC slots hold other people than in
the village, so `in_hall` tests the room itself: three counters in a row at fixed spots. The
camera never moves indoors.

Every square prompt in the room shares one pair of scene values, and those move between
visits, so a prompt is located by where the player stands and a press is judged by what it
did. The counters are talked to across a desk, about 200 units out.
"""

from __future__ import annotations

from ppsspp_debug import Button

from .. import addresses as a
from ..structs import Entity
from . import area, dialog, travel
from . import navigation as nav
from .navigation import XZ
from .session import Session
from .village import can_talk, npc_present, scan_npcs

# the village side: the door is just west of the travelling merchant, past the exit
DOOR_XZ = (9233.0, 8990.0)
DOOR_ROUTE = ((9800.0, 8450.0), (9560.0, 8700.0), (9380.0, 8950.0), DOOR_XZ)
PROMPT_LAG = 0.8  # a zone's prompt shows a beat before it takes input

# the hall side: the room spans about x 4200-5700, z 4600-5700
ARRIVAL_XZ = (4322.0, 5698.0)  # open floor; the front door is approached from here
ZONE_EXIT = (4416.0, 5676.0)  # square: leave; triangle: travel
ZONE_TABLE = (5045.0, 5300.0)  # square: sit
ZONE_ITEM_BOX = (5165.0, 4645.0)
ZONE_DEPART = (5335.0, 4611.0)  # square, square: go on the quest under contract
ITEM_BOX_APPROACH = (5300.0, 4750.0)  # from the west the table stops the player short
ON_ZONE = 150.0  # this close to a zone's spot counts as standing on it

COUNTER_X = 4202.0  # the desk front; the player cannot pass it
COUNTER_START = ((4330.0, 5620.0), (4150.0, 5620.0))  # the near end of the desk
DESK_END_Z = 4900.0

NPCS = {
    a.NPC_ELDER: ("info", (4122.0, 5555.0)),
    a.NPC_HALL_COUNTER_LOW: ("counter_low", (4010.0, 5400.0)),
    a.NPC_HALL_COUNTER_HIGH: ("counter_high", (4010.0, 5250.0)),
    a.NPC_HALL_COUNTER_G: ("counter_g", (4010.0, 5100.0)),
    a.NPC_HALL_SHOP: ("shop", (4902.0, 4529.0)),
    a.NPC_HALL_TRESHI: ("treshi", (5816.0, 5151.0)),
    a.NPC_HALL_HUNTER: ("hunter", (4398.0, 4766.0)),
    a.NPC_HALL_FELYNE: ("felyne", (5378.0, 5232.0)),
}
"""Who stands where in the hall, by NPC slot; the counter staff never move."""

COUNTERS = {role: npc for npc, (role, _) in NPCS.items() if role.startswith("counter")}


class NotInHall(RuntimeError):
    """A hall step was asked for somewhere that is not the hall."""


def in_hall(s: Session, tolerance: float = 80.0) -> bool:
    """True while the player stands in the hall: the three counter staff are at their spots."""
    for npc in COUNTERS.values():
        _, (x, z) = NPCS[npc]
        if not npc_present(s, npc):
            return False
        nx, nz = Entity(s.mem, npc).xz
        if abs(nx - x) > tolerance or abs(nz - z) > tolerance:
            return False
    return True


def _require(s: Session) -> None:
    if not in_hall(s):
        raise NotInHall("not in the gathering hall")


def _wait_for_room(s: Session, inside: bool, timeout: float) -> bool:
    """Wait for a load to put the player in (or out of) the hall; it can read right mid-load."""

    def there() -> bool:
        return in_hall(s) == inside

    try:
        s.wait(lambda: there() and s.holds(there, 0.6, poll=0.3), timeout, "the room", poll=0.4)
    except TimeoutError:
        return False
    return True


def enter(s: Session, online: bool = False, timeout: float = 90.0, attempts: int = 8) -> bool:
    """Walk to the hall door in the village and go in; True once inside.

    At the door square enters the offline hall and circle the online one.
    """
    button: Button = "circle" if online else "square"
    dialog.dismiss(s)
    nav.walk_path(s, DOOR_ROUTE, tolerance=90, timeout=60.0)
    for i in range(attempts):
        nav.walk_to(s, *DOOR_XZ, tolerance=45, timeout=15.0)
        s.sleep(PROMPT_LAG)
        s.press(button, 8)
        if _wait_for_room(s, True, timeout / attempts + 8):
            return True
        nudge = 45 if i % 2 else -45
        nav.walk_to(s, DOOR_XZ[0] + nudge, DOOR_XZ[1] - nudge, tolerance=35, timeout=12.0)
    return in_hall(s)


def stand_at_exit(s: Session, tries: int = 3) -> bool:
    """Put the player on the front door's zone, its prompt showing."""
    for _ in range(tries):
        nav.walk_to(s, *ARRIVAL_XZ, tolerance=70, timeout=25.0)
        if nav.creep_to(s, *ZONE_EXIT) and nav.distance(nav.where(s), ZONE_EXIT) <= ON_ZONE:
            return True
    return False


def leave(s: Session, timeout: float = 90.0, attempts: int = 8) -> bool:
    """Go out through the front door to the village; True once out."""
    _require(s)
    dialog.dismiss(s)
    for _ in range(attempts):
        if not stand_at_exit(s, tries=1):
            continue
        s.press("square", 8)
        if _wait_for_room(s, False, timeout / attempts + 8):
            return True
    return not in_hall(s)


def stand_at_counter(s: Session, npc: int, timeout: float = 60.0) -> bool:
    """Stop the player where `npc`'s talk flag lights, sweeping down the desk front.

    The counters' talk boxes sit about 145 down the room from the staff and only 100 apart, so
    aiming at a spot picks the neighbour half the time; stopping on the flag cannot.
    """
    _require(s)
    nav.walk_to(s, *COUNTER_START[0], tolerance=70, timeout=25.0)
    nav.walk_to(s, *COUNTER_START[1], tolerance=40, timeout=18.0)
    stick = nav.Stick(s)
    yaw = nav.Camera(s.mem).yaw
    deadline = s.now() + timeout
    try:
        while s.now() < deadline:
            if can_talk(s, npc):
                return True
            x, z = nav.where(s)
            if z < DESK_END_Z:
                return False
            stick.toward(nav.world_angle((x, z), (COUNTER_X, z - 200)), yaw, 0.7)
            s.sleep(0.18)
        return False
    finally:
        stick.release()


def open_talk(s: Session, tries: int = 5) -> bool:
    """Press cross until a conversation opens; at a counter one lit press is not enough."""
    try:
        s.press_until(
            lambda: not dialog.has_control(s),
            "a conversation",
            frames=6,
            attempts=tries,
            window=1.1,
        )
    except TimeoutError:
        return False
    return True


def talk_to_counter(s: Session, role: str = "counter_low") -> bool:
    """Stand at a quest counter and open its conversation; `quests.open_board` goes on."""
    try:
        npc = COUNTERS[role]
    except KeyError:
        raise KeyError(f"no hall counter {role!r}; one of {sorted(COUNTERS)}") from None
    return stand_at_counter(s, npc) and open_talk(s)


def read_conversation(s: Session, steps: int = 30) -> str:
    """Press through a conversation and return its lines; stops when a menu opens."""
    seen: list[str] = []
    for _ in range(steps):
        if s.game.scene in dialog.FREE:
            if dialog.settled_control(s):
                break
            continue
        line = s.game.text()
        if line and (not seen or line != seen[-1]):
            seen.append(line)
        if s.game.scene != a.SCENE_DIALOG:
            break
        s.press("cross", 6)
        try:
            s.wait(lambda: s.game.text() != seen[-1] or dialog.has_control(s), 0.9, "a new line")
        except TimeoutError:
            pass
    return "\n".join(seen)


def depart(s: Session, timeout: float = 90.0, attempts: int = 6) -> int:
    """Leave by the back door for the quest under contract; returns AREA_INDEX.

    Two square presses: the first asks, the second commits. Without a contract there is no
    prompt, and the TimeoutError this raises is that answer.
    """
    _require(s)
    dialog.dismiss(s)
    for i in range(attempts):
        nav.walk_to(s, *ZONE_DEPART, tolerance=40, timeout=20.0)
        s.sleep(PROMPT_LAG)
        for _ in range(3):
            s.press("square", 8)
            try:
                s.wait(lambda: not in_hall(s), 1.5, "the hall to unload")
            except TimeoutError:
                continue
            return area.wait_for_in_area(s, timeout)
        nudge = 40 if i % 2 else -40
        nav.walk_to(s, ZONE_DEPART[0] + nudge, ZONE_DEPART[1] + nudge, tolerance=30, timeout=12.0)
    raise TimeoutError("the hall's departure prompt never took; is a quest under contract?")


def use_zone(s: Session, spot: XZ, timeout: float = 20.0, attempts: int = 4) -> bool:
    """Stand on the zone at `spot` and press square; True once a menu is up."""
    _require(s)
    for _ in range(attempts):
        nav.walk_to(s, *spot, tolerance=45, timeout=timeout)
        nav.creep_to(s, *spot, seconds=6)
        # every zone looks alike, and one of them is the front door
        if nav.distance(nav.where(s), spot) > ON_ZONE:
            continue
        s.press("square", 8)
        try:
            s.wait(lambda: dialog.modal_is_up(s), 1.2, "a menu")
            return True
        except TimeoutError:
            pass
        if not in_hall(s):
            raise NotInHall("that press left the hall; it was the wrong zone")
    return False


def sit(s: Session) -> bool:
    """Sit at the table in the middle of the room."""
    return use_zone(s, ZONE_TABLE)


def open_item_box(s: Session) -> bool:
    """Open the small item box by the back door; the same widget as the house's, minus row 3."""
    _require(s)
    nav.walk_to(s, *ITEM_BOX_APPROACH, tolerance=70, timeout=20.0)
    return use_zone(s, ZONE_ITEM_BOX)


def travel_to(s: Session, dest: str | int) -> bool:
    """Fast-travel out through the front door's triangle prompt (`travel.DESTINATIONS`)."""
    _require(s)
    last: Exception = NotInHall("could not reach the hall's exit zone")
    for _ in range(3):
        if not stand_at_exit(s):
            continue
        try:
            return travel.travel_to(s, dest)
        except travel.DestinationUnavailable:
            raise
        except (RuntimeError, TimeoutError) as e:  # stopped short of the zone; come at it again
            last = e
    raise last


def scan(s: Session) -> list[tuple[Entity, str]]:
    """Every NPC in the room, with its role where known."""
    return [(n, NPCS[n.base][0] if n.base in NPCS else "?") for n in scan_npcs(s)]
