# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The turn gizmo: a ring on the floor around where the clip starts, an arrow for the facing at
frame 0, one following the facing as the clip plays, and a handle with a number for the facing
at its end. Dragging the handle sets the clip's `turn`, or an own move's fixed steer angle,
in whole degrees (Shift: 15), positive the way YAW grows; one commit on release.

YAW turns the monster about its own position, the world origin of the preview (`playback.yaw_at`),
so the ring sits there whatever the clip's travel.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import numpy as np

from mhfu_studio.monster.render.playback import end_turn, yaw_at
from mhfu_studio.shell.input import Button, Mod, Pointer
from mhfu_studio.shell.manipulator import GRAB_PX, ray_plane
from mhfu_studio.shell.overlay import Color, Ink, Overlay, Point
from mhfu_studio.shell.workspace import Gesture, Shortcut

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace

TURN_KEY = Shortcut(("T",), "Shows or hides the turn gizmo on the floor")
#: the ring's radius, a share of the monster's bind radius
RING = 0.8
SEGMENTS = 64
SNAP = 15.0
#: the loader's bound on a fixed steer: under one turn either way
MOST = 359.0
UP = np.array([0.0, 1.0, 0.0])


@dataclass(frozen=True)
class Target:
    """What the gizmo shows: the end facing in degrees (None: decided in the game), what a drag
    writes (None: nothing), and a line about it."""

    end: float | None
    writes: Literal["clip", "steer"] | None
    text: str


def heading(deg: float) -> np.ndarray:
    """The floor direction a monster faces `deg` degrees turned from +z."""
    r = math.radians(deg)
    return np.array([math.sin(r), 0.0, math.cos(r)])


class TurnGizmo:
    def __init__(self, ws: MonsterWorkspace) -> None:
        self.ws = ws
        self.shown = True
        self.hot = False
        #: the angle under a drag, unwrapped from the press
        self.dragged: float | None = None

    def cancel(self) -> None:
        self.dragged, self.hot = None, False

    def target(self) -> Target | None:
        """None while no clip of a port is on screen."""
        ws, vp = self.ws, self.ws.vp
        if not self.shown or vp is None or vp.clip is None or vp.scene is None:
            return None
        m = ws.manifest
        if m is None:
            return None
        sc, clip = vp.scene, vp.clip
        mv = ws.own_move_on_screen()
        s = None if mv is None else mv.steer
        if s is None or s.turn == "clip":
            found = ws.clip_name_on_screen()
            who = f"clip {found}" if found else f"anim {clip.slot}"
            given = found is not None and m.clips[found].turn is not None
            how = "set here" if given else "its own body's"
            return Target(end_turn(sc, clip), "clip", f"{who}: turns {how}")
        if s.turn == "fixed":
            return Target(s.angle or 0.0, "steer", f"fixed: over {s.frames} AI frames")
        if s.turn == "still":
            return Target(0.0, None, "still: no turn")
        rate = "the charge's rate" if s.rate is None else f"{s.rate} a frame"
        way = "toward" if s.turn == "hunter" else "away from"
        return Target(None, None, f"turns {way} the hunter at {rate}")

    # drawing

    def _radius(self) -> float:
        vp = self.ws.vp
        return 0.0 if vp is None else max(vp.bind_bounds.radius * RING, 1.0)

    def _screen(self, pts: np.ndarray, size: tuple[int, int]) -> list[Point]:
        vp = self.ws.vp
        assert vp is not None
        got = vp.camera.project(pts, size)
        return [(float(x), float(y)) for x, y, _ in got]

    def handle(self, size: tuple[int, int]) -> Point | None:
        t = self.target()
        if t is None or t.end is None:
            return None
        deg = t.end if self.dragged is None else self.dragged
        return self._screen(heading(deg)[None] * self._radius(), size)[0]

    def paint(self, o: Overlay) -> None:
        t, vp = self.target(), self.ws.vp
        if t is None or vp is None or vp.clip is None or vp.scene is None:
            return
        r, size = self._radius(), o.size
        ring = self._screen(
            np.array([heading(a) * r for a in np.linspace(0.0, 360.0, SEGMENTS + 1)]), size
        )
        o.polyline(ring, Ink.AXIS_VIEW, 1.0, closed=True)
        c = self._screen(np.zeros((1, 3)), size)[0]
        o.text((min(x for x, _ in ring), max(y for _, y in ring) + 6), t.text, Ink.TEXT, 11.0)
        self._arrow(o, c, heading(0.0) * r, Ink.AXIS_Z, 1.5, size)
        steer = None if vp.actor is None else vp.actor.steer
        now = math.degrees(yaw_at(vp.scene, vp.clip, vp.frame, steer, vp.playback.speed))
        self._arrow(o, c, heading(now) * r * 0.8, Ink.TEXT, 2.5, size)
        end = t.end if self.dragged is None else self.dragged
        if end is None:
            return
        arc = np.array([heading(a) * r for a in np.linspace(0.0, end, max(2, int(abs(end)) // 4))])
        ink: Color = Ink.HOT if self.hot or self.dragged is not None else Ink.SELECTION
        o.polyline(self._screen(arc, size), ink, 3.0)
        h = self._screen(heading(end)[None] * r, size)[0]
        o.line(c, h, ink, 1.0)
        o.circle(h, 6.0, Ink.SHADOW, ink if t.writes else None, 1.5)
        o.text((h[0] + 9, h[1] - 7), f"{end:+.0f}°", Ink.TEXT, 13.0)

    def _arrow(
        self, o: Overlay, c: Point, tip: np.ndarray, ink: Color, width: float, size: tuple[int, int]
    ) -> None:
        b, s1, s2 = self._screen(
            np.array([tip, tip * 0.88 + _side(tip, 0.06), tip * 0.88 - _side(tip, 0.06)]), size
        )
        o.line(c, b, ink, width)
        o.polygon([b, s1, s2], ink)

    # the pointer

    def pointer(self, ev: Pointer) -> Gesture | None:
        """The handle's events; None for one it leaves to the rest of the view."""
        if self.dragged is not None:
            if ev.kind == "move":
                self._drag(ev)
            elif ev.kind == "release" and ev.button == Button.LEFT:
                self._commit(ev)
            elif ev.kind == "leave":
                return None
            return Gesture.ALL
        h = self.handle(ev.size)
        over = h is not None and math.dist(h, ev.pos) <= GRAB_PX + 2.0
        t = self.target()
        if ev.kind == "move":
            self.hot = over and t is not None and t.writes is not None
            return None
        if ev.kind == "leave":
            self.hot = False
            return None
        if ev.kind == "press" and ev.button == Button.LEFT and over and t is not None:
            if t.writes is None:
                self.ws.message = (
                    t.text + ": nothing to drag. Set the move's steer to clip or fixed."
                )
                return Gesture.ALL
            self.dragged = t.end
            if self.ws.vp is not None:
                self.ws.vp.playback.pause()
            return Gesture.ALL
        return None

    def _drag(self, ev: Pointer) -> None:
        vp = self.ws.vp
        if vp is None or self.dragged is None:
            return
        o, d = vp.camera.ray(ev.x, ev.y, ev.size)
        p = ray_plane(o, d, np.zeros(3), UP)
        if p is None or math.hypot(p[0], p[2]) < 1e-6:
            return
        a = math.degrees(math.atan2(p[0], p[2]))
        step = (a - self.dragged + 180.0) % 360.0 - 180.0
        self.dragged = max(-MOST, min(MOST, self.dragged + step))

    def _commit(self, ev: Pointer) -> None:
        got, self.dragged = self.dragged, None
        if got is None:
            return
        snap = SNAP if Mod.SHIFT in ev.mods else 1.0
        deg = float(round(got / snap) * snap) + 0.0  # + 0.0: no -0.0
        t = self.target()
        if t is not None and t.end is not None and deg == round(t.end, 6):
            return
        self.ws.set_turn(deg)


def _side(v: np.ndarray, k: float) -> np.ndarray:
    """`v` turned a quarter about y, scaled by `k`: an arrowhead's half width."""
    return np.array([v[2], 0.0, -v[0]]) * k
