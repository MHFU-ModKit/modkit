# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Pokke village: finding NPCs, talking to them, and leaving on a quest.

Every NPC is an ENTITY with NPC_VTABLE and no registry lists them, so `scan_npcs` scans
NPC_HEAP. Slots are per area (the elder's is the hall receptionist's in the hall), and the two
travelling traders come and go, so check `npc_present` before trusting an address.

NPCs are solid: a walk aimed at one stops about 100 units short, which is arrival. The talk
flag (ENTITY.TALKABLE) also encodes facing, and cross does nothing without it.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Callable

from .. import addresses as a
from ..memory import Image
from ..structs import Entity
from . import area, dialog
from . import navigation as nav
from .navigation import XZ
from .session import Session

ELDER = a.NPC_ELDER
NPC_SPAN = 0x20_0000  # bytes of NPC_HEAP that hold NPCs
SCAN_CHUNK = 0x8_0000

TALK_RANGE = 140.0  # NPC collision keeps the player 90-110 units out
ORBIT = (0.0, 40.0, -40.0, 90.0, -90.0, 140.0, -140.0, 180.0)  # degrees round an NPC to try
ORBIT_RADIUS = 150.0
TALK_WINDOW = 0.9  # seconds for a speech box to open after cross
TALK_PRESSES = 4  # a cross right after a walk stops can be lost

EXIT_XZ = (9500.0, 8400.0)  # the square prompt "go on the quest" at the bottom of the village
EXIT_ROUTE = ((10140.0, 8404.0), (9800.0, 8450.0), EXIT_XZ)
EXIT_PROMPTS = frozenset({a.SCENE_PROMPT, a.SCENE_PROMPT_ALT})
DEPART_PRESSES = 6
TOOK_WINDOW = 2.0  # for a square to take SCREEN_STATE off CONTRACT; the prompt shows a beat
# before it takes input, so the first press can be lost (always, under fast-forward)


def scan_npcs(s: Session) -> list[Entity]:
    """Every loaded NPC, by address; they wander, so re-read positions rather than keep them."""
    start = a.NPC_HEAP
    data = b"".join(s.mem.read(p, SCAN_CHUNK) for p in range(start, start + NPC_SPAN, SCAN_CHUNK))
    heap = Image(data, start)
    key = struct.pack("<I", a.NPC_VTABLE)
    found = []
    i = data.find(key)
    while i >= 0:
        base = start + i - a.ENTITY.VTABLE
        if i % 4 == 0 and base + a.ENTITY.TRANSLATION_W + 4 <= heap.end:
            npc = Entity(heap, base)
            if npc.translation_w == 1.0 and all(map(math.isfinite, npc.translation)):
                found.append(Entity(s.mem, base))
        i = data.find(key, i + 1)
    return found


def npc_present(s: Session, npc: int) -> bool:
    return bool(Entity(s.mem, npc).vtable == a.NPC_VTABLE)


def can_talk(s: Session, npc: int) -> bool:
    return Entity(s.mem, npc).talkable == 1


def move_npc(s: Session, npc: int, x: float, y: float, z: float) -> None:
    """Teleport an NPC: POSITION is what the engine draws, TRANSLATION what `scan_npcs` reads."""
    entity = Entity(s.mem, npc)
    entity.position = (x, y, z)
    entity.translation = (x, y, z)


def nearest_npc(s: Session, x: float, z: float, within: float = math.inf) -> Entity | None:
    near = [(nav.distance((x, z), n.xz), n) for n in scan_npcs(s)]
    near = [(d, n) for d, n in near if d < within]
    return min(near, key=lambda dn: dn[0])[1] if near else None


def wait_talkable(s: Session, npc: int, timeout: float = 12.0, tick: float = 0.15) -> bool:
    """Face `npc` until its talk flag lights; the end of a walk faces wherever it last went."""
    deadline = s.now() + timeout
    while s.now() < deadline:
        if can_talk(s, npc):
            return True
        nav.face(s, *Entity(s.mem, npc).xz, tolerance_deg=12, timeout=2.0)
        s.sleep(tick)
    return can_talk(s, npc)


def approach(
    s: Session,
    x: float,
    z: float,
    tolerance: float = TALK_RANGE,
    until: Callable[[], object] | None = None,
) -> nav.Walk:
    """Walk to a point, counting a stop against the NPC standing there as arrival."""
    walk = nav.walk_to(s, x, z, tolerance=tolerance, timeout=60.0, until=until)
    if not walk.reached and walk.remaining <= tolerance + 30:
        return nav.Walk(True, walk.at, walk.remaining, walk.elapsed, "contact", walk.detours)
    return walk


def talk_to(s: Session, npc: int | XZ) -> str | None:
    """Walk to `npc` (an NPC address, or a spot to face), talk, and return its first line.

    None if it never answered: scenery, an NPC that wandered off or is not spawned. Walks stop as
    soon as the NPC's talk flag (the red triangle) lights, so the player never shoves into it;
    the elder sits behind a fire, so the talk circles him only while the flag stays dark.
    """
    who: int | None = None
    if isinstance(npc, int):
        if not npc_present(s, npc):
            return None
        who, target = npc, Entity(s.mem, npc).xz
    else:
        target = npc

    def lit() -> bool:
        return who is not None and can_talk(s, who)

    walk = approach(s, *target, until=lit)
    if not walk.reached and walk.remaining > TALK_RANGE + 120:
        return None
    base = nav.world_angle(target, nav.where(s))
    for k, offset in enumerate(ORBIT):
        if k:
            angle = base + math.radians(offset)
            x, z = (
                target[0] + ORBIT_RADIUS * math.sin(angle),
                target[1] + ORBIT_RADIUS * math.cos(angle),
            )
            nav.walk_to(s, x, z, tolerance=70, timeout=15.0, until=lit)
        if who is None:
            nav.face(s, *target, tolerance_deg=12)
        else:
            if not npc_present(s, who):
                return None
            target = Entity(s.mem, who).xz
            if not wait_talkable(s, who, timeout=5.0):
                continue
        if speak(s, lit if who is not None else None):
            return s.game.text()
    return None


def speak(s: Session, lit: Callable[[], bool] | None = None) -> bool:
    """Cross until a speech box opens, while `lit` (the NPC's talk flag) stays true."""
    for _ in range(TALK_PRESSES):
        if lit is not None and not lit():
            return False
        s.press("cross", 5)
        try:
            s.wait(lambda: not dialog.has_control(s), TALK_WINDOW, "a speech box")
            return True
        except TimeoutError:
            continue
    return False


def open_npc_menu(s: Session, npc: int, attempts: int = 5) -> str | None:
    """Talk to `npc` and advance to its option list; returns the greeting, None if no list.

    The list is the NPC's once NPC_MENU.PARTNER names it; each NPC's list has its own scene, and
    the struct keeps the previous NPC's values after a menu closes. One cross too many drills
    into a submenu. Steer the list with `menus.NPC_MENU.goto` by index.
    """
    line = talk_to(s, npc)
    if line is None:
        return None
    menu = s.game.npc_menu

    def ready() -> bool:
        return menu.partner == npc and s.game.scene != a.SCENE_DIALOG and not dialog.has_control(s)

    for _ in range(attempts):
        if ready():
            return line
        if dialog.settled_control(s):
            return None
        s.press("cross", 5)
        try:
            s.wait(ready, 1.0, "the option list")
            return line
        except TimeoutError:
            continue
    return None


def at_departure_zone(s: Session, window: float = 0.6) -> bool:
    """True while the village exit's prompt shows; a zone flickers, so it samples a while."""
    try:
        return s.wait(lambda: s.game.scene in EXIT_PROMPTS, window, "the departure prompt")
    except TimeoutError:
        return False


def _screen_leaves(s: Session, value: int) -> Callable[[], bool]:
    return lambda: s.game.screen_state != value


def depart(s: Session, timeout: float = 90.0, attempts: int = 6) -> int:
    """Walk to the village exit and start the quest under contract; returns AREA_INDEX.

    A square counts once SCREEN_STATE leaves its value in the zone (CONTRACT), so a lost one
    costs TOOK_WINDOW, not the load's timeout. Without a contract the prompt never shows, and
    the TimeoutError this raises says so.
    """
    dialog.dismiss(s)
    nav.walk_path(s, EXIT_ROUTE, tolerance=95)
    for i in range(attempts):
        if at_departure_zone(s):
            took = _screen_leaves(s, s.game.screen_state)
            # re-checking the zone between presses would read its flicker as having left it
            for _ in range(DEPART_PRESSES):
                s.press("square", 8)
                try:
                    s.wait(took, TOOK_WINDOW, "the departure")
                except TimeoutError:
                    continue
                return area.wait_for_in_area(s, timeout)
        nudge = 60 if i % 2 else -60
        nav.walk_to(s, EXIT_XZ[0] + nudge, EXIT_XZ[1] + nudge, tolerance=45, timeout=12.0)
    raise TimeoutError("no departure prompt at the village exit; is a quest under contract?")
