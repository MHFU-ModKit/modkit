# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Walking the player closed-loop. The stick is camera-relative, so every heading is re-solved
from live memory each tick:

    world_move_angle = view_yaw - stick_angle       stick_angle = atan2(sx, sy), 0 = up
    view_yaw = atan2(look.x - eye.x, look.z - eye.z)

Angles are the game's atan2(dx, dz): 0 = +z, 90 degrees = +x. The camera is fixed in the village
and indoors and turns in a quest (the D-pad, or the game when scenery blocks the shot).

Each read and stick send stops the emulated CPU briefly, and a sustained high rate wedges
PPSSPP's debugger while the game runs on, so the loops re-read the camera every few ticks and
re-send the stick only when it moves.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .. import addresses as a
from ..views import View, vec3
from . import dialog
from .session import Session

XZ = tuple[float, float]

PROGRESS = 4.0  # units of closing distance that count as progress
DETOUR = math.radians(70.0)  # off the direct line, to clear a corner after a stall
DETOUR_DISTANCE = 100.0  # how far a detour goes, so a fast-forwarded game goes no further
DETOUR_SECONDS = 0.7  # the most a detour lasts, where the hunter is wedged and cannot go far
CAMERA_EVERY = 4  # walk ticks between camera reads
STICK_EPS = 0.05  # stick change worth re-sending


class Camera(View):
    """The view the stick is relative to."""

    eye = vec3(a.CAM_VIEW_EYE)
    look = vec3(a.CAM_LOOK_AT)

    @property
    def yaw(self) -> float:
        (ex, _, ez), (lx, _, lz) = self.eye, self.look
        return math.atan2(lx - ex, lz - ez)


@dataclass(frozen=True)
class Pose:
    x: float
    y: float
    z: float
    yaw: float  # radians, the way the player faces

    @property
    def xz(self) -> XZ:
        return self.x, self.z


@dataclass(frozen=True)
class Walk:
    """How a walk ended: `reason` is "arrived", "blocked" or "timeout"."""

    reached: bool
    at: XZ
    remaining: float
    elapsed: float
    reason: str
    detours: int = 0


def where(s: Session) -> XZ:
    x, _, z = s.game.player.translation
    return x, z


def pose(s: Session) -> Pose:
    player = s.game.player
    x, y, z = player.translation
    return Pose(x, y, z, player.facing)


def world_angle(start: XZ, end: XZ) -> float:
    return math.atan2(end[0] - start[0], end[1] - start[1])


def stick_for(angle: float, view_yaw: float, magnitude: float = 1.0) -> tuple[float, float]:
    """The stick that walks along world `angle` under a camera at `view_yaw`."""
    d = view_yaw - angle
    return magnitude * math.sin(d), magnitude * math.cos(d)


def distance(p: XZ, q: XZ) -> float:
    return math.hypot(q[0] - p[0], q[1] - p[1])


class Stick:
    """The left stick; `set` sends only a vector that moved, `release` centres it."""

    def __init__(self, s: Session) -> None:
        self.s = s
        self.sent: tuple[float, float] | None = None

    def set(self, x: float, y: float) -> None:
        if self.sent is None or max(abs(x - self.sent[0]), abs(y - self.sent[1])) > STICK_EPS:
            self.s.client.analog(x, y)
            self.sent = x, y

    def toward(self, angle: float, view_yaw: float, magnitude: float = 1.0) -> None:
        self.set(*stick_for(angle, view_yaw, magnitude))

    def release(self) -> None:
        self.s.client.analog(0.0, 0.0)
        self.sent = 0.0, 0.0


def walk_to(
    s: Session,
    x: float,
    z: float,
    *,
    tolerance: float = 60.0,
    timeout: float = 40.0,
    tick: float = 0.12,
    slow_radius: float = 220.0,
    patience: int = 14,
    max_detours: int = 4,
    release: bool = True,
    until: Callable[[], object] | None = None,
) -> Walk:
    """Walk to world (x, z), re-solving the heading every `tick`; `until` ends it early.

    Progress is closing distance, since the player slides along fences and still gets closer.
    After `patience` ticks without it the walk strafes off the line, alternating sides, and
    gives up as "blocked" after `max_detours`. It is no pathfinder: give `walk_path` waypoints
    round real corners. The player is about 100 units wide, so a `tolerance` under 40
    oscillates; inside `slow_radius` the stick eases off so the walk does not overshoot.
    """
    target = (x, z)
    start = s.now()
    here = where(s)
    best = distance(here, target)
    stalled = detours = 0
    side = 1
    camera = Camera(s.mem)
    yaw, age = camera.yaw, 0
    stick = Stick(s)

    def end(reached: bool, reason: str) -> Walk:
        return Walk(reached, here, distance(here, target), s.now() - start, reason, detours)

    try:
        while s.now() < start + timeout:
            here = where(s)
            age += 1
            if age >= CAMERA_EVERY:
                yaw, age = camera.yaw, 0
            remaining = distance(here, target)
            if remaining <= tolerance:
                return end(True, "arrived")
            if until is not None and until():
                return end(True, "until")
            if remaining < best - PROGRESS:
                best, stalled = remaining, 0
            else:
                stalled += 1
            if stalled >= patience:
                if detours >= max_detours:
                    return end(False, "blocked")
                detours += 1
                side = -side
                stick.toward(world_angle(here, target) + side * DETOUR, yaw)
                detour(s, here, tick)
                best, stalled = distance(where(s), target), 0
                yaw, age = camera.yaw, 0
                continue
            magnitude = 1.0 if remaining >= slow_radius else max(0.35, remaining / slow_radius)
            stick.toward(world_angle(here, target), yaw, magnitude)
            s.sleep(tick)
        here = where(s)
        return end(False, "timeout")
    finally:
        if release:
            stick.release()


def detour(s: Session, origin: XZ, tick: float) -> None:
    """Hold the stick as set until the hunter is DETOUR_DISTANCE from `origin`, at most
    DETOUR_SECONDS: a distance, not a time, so fast-forward does not stretch it."""
    end = s.now() + DETOUR_SECONDS
    while s.now() < end:
        s.sleep(tick)
        if distance(where(s), origin) >= DETOUR_DISTANCE:
            return


def walk_path(
    s: Session,
    points: Iterable[XZ],
    *,
    tolerance: float = 60.0,
    timeout: float = 40.0,
    until: Callable[[], object] | None = None,
    patience: int = 14,
    max_detours: int = 4,
) -> Walk:
    """Walk waypoints in order, stopping at the first that is not reached or once `until`."""
    points = list(points)
    if not points:
        raise ValueError("no waypoints")
    for i, (x, z) in enumerate(points):
        last = i == len(points) - 1
        walk = walk_to(
            s,
            x,
            z,
            tolerance=tolerance,
            timeout=timeout,
            release=last,
            until=until,
            patience=patience,
            max_detours=max_detours,
        )
        if not walk.reached or walk.reason == "until":
            if not last:
                Stick(s).release()
            break
    return walk


def face(
    s: Session,
    x: float,
    z: float,
    tolerance_deg: float = 25.0,
    timeout: float = 6.0,
    tick: float = 0.1,
) -> bool:
    """Turn the player toward world (x, z). There is no turn in place, so this walks slowly."""
    stick = Stick(s)
    camera = Camera(s.mem)
    deadline = s.now() + timeout
    try:
        while s.now() < deadline:
            p = pose(s)
            want = world_angle(p.xz, (x, z))
            error = (want - p.yaw + math.pi) % (2 * math.pi) - math.pi
            if abs(math.degrees(error)) <= tolerance_deg:
                return True
            stick.toward(want, camera.yaw, 0.45)
            s.sleep(tick)
        return False
    finally:
        stick.release()


def creep_to(
    s: Session,
    x: float,
    z: float,
    until: Callable[[Session], bool] | None = None,
    seconds: float = 10.0,
    magnitude: float = 0.55,
    tick: float = 0.2,
    wedge_after: float = 2.0,
    wedge_eps: float = 6.0,
) -> bool:
    """Push toward (x, z) at walking pace until `until(s)`; True if it came true.

    For trigger zones (doors, item boxes, counters): they fire from a box the player can stand
    in 100 units from the point aimed at, wedged against furniture where `walk_to` reports
    "blocked". The default `until` is any prompt showing. A creep that has not moved
    `wedge_eps` in `wedge_after` seconds is pressed against something solid and stops.
    """
    done = until or dialog.prompt_showing
    stick = Stick(s)
    camera = Camera(s.mem)
    yaw, age = camera.yaw, 0
    end = s.now() + seconds
    last, moved = where(s), s.now()
    try:
        while s.now() < end:
            if done(s):
                return True
            here = where(s)
            if distance(here, last) > wedge_eps:
                last, moved = here, s.now()
            elif s.now() - moved > wedge_after:
                break
            age += 1
            if age >= CAMERA_EVERY:
                yaw, age = camera.yaw, 0
            stick.toward(world_angle(here, (x, z)), yaw, magnitude)
            s.sleep(tick)
    finally:
        stick.release()
    return done(s)


def push(
    s: Session,
    heading_deg: float,
    seconds: float,
    magnitude: float = 1.0,
    tick: float = 0.2,
    until: Callable[[Session], bool] | None = None,
) -> Pose:
    """Hold the stick along a world heading, as toward a gate whose position is unknown.

    Stops early once `until(s)` holds (say, the section changed).
    """
    angle = math.radians(heading_deg)
    stick = Stick(s)
    camera = Camera(s.mem)
    end = s.now() + seconds
    try:
        while s.now() < end:
            stick.toward(angle, camera.yaw, magnitude)
            s.sleep(tick)
            if until is not None and until(s):
                break
    finally:
        stick.release()
    return pose(s)


def climb(
    s: Session, heading_deg: float, hold: float = 5.0, square_up: float = 1.2, tick: float = 0.3
) -> tuple[Pose, Pose]:
    """Climb the ledge ahead; returns the poses before and after.

    A ledge and a wall both stop a walk. Shoving into the face squares the player up to it,
    circle grabs, and holding the direction climbs. The heading must be perpendicular to the
    face. A climb shows in Y, a step up with the footprint barely moving.
    """
    angle = math.radians(heading_deg)
    stick = Stick(s)
    camera = Camera(s.mem)
    try:
        stick.toward(angle, camera.yaw)
        s.sleep(square_up)
        stick.release()
        before = pose(s)
        s.press("circle", 4)
        s.sleep(0.6)
        end = s.now() + hold
        while s.now() < end:
            stick.toward(angle, camera.yaw)
            s.sleep(tick)
    finally:
        stick.release()
    s.sleep(0.6)
    return before, pose(s)
