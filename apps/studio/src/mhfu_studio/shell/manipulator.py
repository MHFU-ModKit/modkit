# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The transform gizmo without a toolkit: handles laid out, hit, dragged and painted in numpy.

A pose goes in and the pose after the drag comes out; `new @ inv(start)` is a transform about
the pivot (the pose's translation). Handles keep a constant size on screen, in view points,
except a scale drag's arms, which grow with the factor so the grabbed one stays under the pointer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

from mhfu_studio.shell.camera import Mat, OrbitCamera, Vec
from mhfu_studio.shell.input import Button, Pointer
from mhfu_studio.shell.overlay import Ink, Overlay
from mhfu_studio.shell.workspace import Gesture

Operation = Literal["translate", "rotate", "scale"]
Part = Literal["axis", "plane", "view"]
Ray = tuple[Vec, Vec]

#: an arm's length on screen
ARM_PX = 90.0
#: how far from a handle a press still takes it
GRAB_PX = 8.0
#: an axis shorter than this on screen points at the camera and is left out
MIN_ARM_PX = 12.0
#: the centre handle's radius (translate) or half side (scale)
CENTER_PX = 6.0
#: the view ring's radius, in arms
VIEW_RING = 1.2
#: a plane square's near and far edge, in arms
PLANE = (0.25, 0.5)
#: a plane seen closer to edge-on than this (|cos| of normal and view) is left out
PLANE_MIN = 0.2
#: a ring seen closer to edge-on than this rotates by the screen angle instead
EDGE_ON = 0.15
MIN_SCALE = 1e-3
WIDTH = 2.5
#: the far half of a ring
BACK_WIDTH = 1.0
#: a back half nearer than this to the pointer loses to a front half
BACK_PENALTY = 3.0
HEAD = (12.0, 5.0)
BOX_PX = 4.5
SEGMENTS = 32
INKS = (Ink.AXIS_X, Ink.AXIS_Y, Ink.AXIS_Z)


@dataclass(frozen=True)
class Manipulation:
    """One pointer event's outcome; commit `matrix` when `done`."""

    matrix: Mat
    using: bool
    over: bool
    done: bool = False
    consumed: Gesture = Gesture.NONE


@dataclass(frozen=True)
class Handle:
    """What can be grabbed; a plane is named by its normal, `view` faces the camera."""

    part: Part
    axis: int | None = None

    @property
    def ink(self) -> Ink:
        return Ink.AXIS_VIEW if self.axis is None else INKS[self.axis]

    @property
    def label(self) -> str:
        if self.axis is None:
            return ""
        return "XYZ"[self.axis] if self.part == "axis" else "YZ XZ XY".split()[self.axis]


# ---- math ---------------------------------------------------------------------------------- #


def world_per_px(camera: OrbitCamera, at: Vec, height: int) -> float:
    """World length of one point at `at`'s depth; 0 when `at` is behind the eye."""
    depth = float(np.dot(at - camera.eye, camera.forward))
    if depth <= 0.0:
        return 0.0
    return 2.0 * depth * math.tan(math.radians(camera.fov) * 0.5) / max(height, 1)


def along(pivot: Vec, axis: Vec, origin: Vec, direction: Vec) -> float | None:
    """How far along the unit `axis` from `pivot` the ray passes closest; None when parallel."""
    w0 = pivot - origin
    b = float(axis @ direction)
    denom = 1.0 - b * b
    if denom < 1e-9:
        return None
    return (b * float(direction @ w0) - float(axis @ w0)) / denom


def ray_plane(origin: Vec, direction: Vec, point: Vec, normal: Vec) -> Vec | None:
    """Where the ray meets the plane; None when parallel or behind the origin."""
    den = float(direction @ normal)
    if abs(den) < 1e-9:
        return None
    t = float((point - origin) @ normal) / den
    if t < 0.0:
        return None
    out: Vec = origin + direction * t
    return out


def angle(v0: Vec, v1: Vec, normal: Vec) -> float:
    """Signed degrees from `v0` to `v1` about the unit `normal` (right-handed)."""
    return math.degrees(math.atan2(float(np.cross(v0, v1) @ normal), float(v0 @ v1)))


def snapped(value: float, step: float | None) -> float:
    """`value` at the nearest multiple of `step`; unchanged without one."""
    return round(value / step) * step if step else value


def rotation(axis: Vec, degrees: float) -> Mat:
    """The 3x3 turning `degrees` about the unit `axis`."""
    x, y, z = axis
    k = np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])
    a = math.radians(degrees)
    out: Mat = np.eye(3) + math.sin(a) * k + (1.0 - math.cos(a)) * (k @ k)
    return out


def about(pivot: Vec, linear: Mat) -> Mat:
    """The 4x4 applying the 3x3 `linear` about `pivot`."""
    m = np.eye(4)
    m[:3, :3] = linear
    m[:3, 3] = pivot - linear @ pivot
    return m


def axes(matrix: Mat, local: bool) -> Mat:
    """The gizmo's unit axes as rows: the world's, or the pose's own without its scale."""
    if not local:
        return np.eye(3)
    q, r = np.linalg.qr(np.asarray(matrix, dtype=np.float64)[:3, :3])
    d = np.diag(r)
    if (np.abs(d) < 1e-12).any():
        return np.eye(3)
    out: Mat = (q * np.sign(d)).T
    return out


# ---- screen shapes -------------------------------------------------------------------------- #


def _pt(v: Vec) -> tuple[float, float]:
    return float(v[0]), float(v[1])


def _pts(vs: Mat) -> list[tuple[float, float]]:
    return [_pt(v) for v in vs]


def _poly_dist(p: Vec, pts: Mat, closed: bool) -> float:
    a = pts if closed else pts[:-1]
    d = (np.roll(pts, -1, axis=0) if closed else pts[1:]) - a
    n = (d * d).sum(1)
    t = np.clip(((p - a) * d).sum(1) / np.where(n == 0.0, 1.0, n), 0.0, 1.0)
    return float(np.sqrt(((a + d * t[:, None] - p) ** 2).sum(1)).min())


def _inside(p: Vec, pts: Mat) -> bool:
    d = np.roll(pts, -1, axis=0) - pts
    c = d[:, 0] * (p[1] - pts[:, 1]) - d[:, 1] * (p[0] - pts[:, 0])
    return bool((c >= 0.0).all() or (c <= 0.0).all())


@dataclass(frozen=True, eq=False)
class _Shape:
    handle: Handle

    def dist(self, p: Vec) -> float:
        raise NotImplementedError

    def under(self, o: Overlay, ink: Ink) -> None:
        """Drawn below every shape's `draw`."""

    def draw(self, o: Overlay, ink: Ink) -> None:
        raise NotImplementedError


@dataclass(frozen=True, eq=False)
class _Arm(_Shape):
    """An arrow (translate) or a stick with a box end (scale)."""

    a: Vec
    b: Vec
    box: bool

    def dist(self, p: Vec) -> float:
        return _poly_dist(p, np.array([self.a, self.b]), closed=False)

    def draw(self, o: Overlay, ink: Ink) -> None:
        if self.box:
            o.line(_pt(self.a), _pt(self.b), ink, WIDTH)
            o.rect(_pt(self.b - BOX_PX), _pt(self.b + BOX_PX), fill=ink)
            return
        u = (self.b - self.a) / max(float(np.linalg.norm(self.b - self.a)), 1e-9)
        w = np.array([-u[1], u[0]])
        base = self.b - u * HEAD[0]
        o.line(_pt(self.a), _pt(base), ink, WIDTH)
        o.polygon(_pts(np.array([self.b, base + w * HEAD[1], base - w * HEAD[1]])), ink)


@dataclass(frozen=True, eq=False)
class _Quad(_Shape):
    pts: Mat

    def dist(self, p: Vec) -> float:
        return 0.0 if _inside(p, self.pts) else _poly_dist(p, self.pts, closed=True)

    def draw(self, o: Overlay, ink: Ink) -> None:
        o.polygon(_pts(self.pts), ink)


@dataclass(frozen=True, eq=False)
class _Center(_Shape):
    """A circle (translate) or a box (scale) on the pivot."""

    c: Vec
    box: bool

    def dist(self, p: Vec) -> float:
        d = np.abs(p - self.c)
        return max(0.0, float(d.max() if self.box else np.linalg.norm(d)) - CENTER_PX)

    def draw(self, o: Overlay, ink: Ink) -> None:
        if self.box:
            o.rect(_pt(self.c - CENTER_PX), _pt(self.c + CENTER_PX), fill=ink)
        else:
            o.circle(_pt(self.c), CENTER_PX, color=ink, width=WIDTH)


@dataclass(frozen=True, eq=False)
class _Ring(_Shape):
    """A rotation ring: its near half drawn full, its far half under everything."""

    front: Mat
    back: Mat
    back_width: float

    def dist(self, p: Vec) -> float:
        back = _poly_dist(p, self.back, closed=False) + BACK_PENALTY
        return min(_poly_dist(p, self.front, closed=False), back)

    def under(self, o: Overlay, ink: Ink) -> None:
        o.polyline(_pts(self.back), ink, self.back_width)

    def draw(self, o: Overlay, ink: Ink) -> None:
        o.polyline(_pts(self.front), ink, WIDTH)


@dataclass(frozen=True, eq=False)
class _Circle(_Shape):
    """The view ring, a circle on screen."""

    c: Vec
    r: float

    def dist(self, p: Vec) -> float:
        return abs(float(np.linalg.norm(p - self.c)) - self.r)

    def draw(self, o: Overlay, ink: Ink) -> None:
        o.circle(_pt(self.c), self.r, color=ink, width=WIDTH)


@dataclass(frozen=True, eq=False)
class _Layout:
    pivot: Vec
    center: Vec
    axes: Mat
    #: in pick order: on a tie the earlier wins
    shapes: tuple[_Shape, ...]

    def hit(self, x: float, y: float) -> Handle | None:
        p = np.array([x, y])
        best = min(((s.dist(p), i) for i, s in enumerate(self.shapes)), default=(math.inf, -1))
        return self.shapes[best[1]].handle if best[0] <= GRAB_PX else None


def _layout(
    camera: OrbitCamera,
    size: tuple[int, int],
    matrix: Mat,
    op: Operation,
    local: bool,
    grow: Vec | None = None,
) -> _Layout | None:
    """`grow`: each axis arm's length in arms (a scale drag's factors)."""
    m = np.asarray(matrix, dtype=np.float64)
    pivot = m[:3, 3].copy()
    wpp = world_per_px(camera, pivot, size[1])
    if wpp <= 0.0:
        return None
    arm = ARM_PX * wpp
    ax = axes(m, local or op == "scale")
    eye = camera.eye - pivot
    eye /= max(float(np.linalg.norm(eye)), 1e-12)

    def screen(points: Mat) -> Mat:
        out: Mat = camera.project(points, size)[:, :2]
        return out

    center = screen(pivot[None])[0]
    shapes: list[_Shape] = []
    if op == "rotate":
        for i in range(3):
            u, v = ax[(i + 1) % 3], ax[(i + 2) % 3]
            phi = math.atan2(float(v @ eye), float(u @ eye))
            facing = math.hypot(float(u @ eye), float(v @ eye))
            half = []
            for t0 in (phi - math.pi / 2, phi + math.pi / 2):
                t = np.linspace(t0, t0 + math.pi, SEGMENTS + 1)[:, None]
                half.append(screen(pivot + arm * (np.cos(t) * u + np.sin(t) * v)))
            # the far half thins out as the ring turns from face-on
            back = BACK_WIDTH + (WIDTH - BACK_WIDTH) * max(0.0, 1.0 - facing / 0.25)
            shapes.append(_Ring(Handle("axis", i), half[0], half[1], back))
        shapes.append(_Circle(Handle("view"), center, VIEW_RING * ARM_PX))
        return _Layout(pivot, center, ax, tuple(shapes))
    shapes.append(_Center(Handle("view"), center, box=op == "scale"))
    if op == "translate":
        lo, hi = PLANE[0] * arm, PLANE[1] * arm
        for k in range(3):
            if abs(float(ax[k] @ eye)) < PLANE_MIN:
                continue
            u, v = ax[(k + 1) % 3], ax[(k + 2) % 3]
            corners = pivot + np.array([lo * u + lo * v, hi * u + lo * v, hi * u + hi * v])
            corners = np.vstack([corners, pivot + lo * u + hi * v])
            shapes.append(_Quad(Handle("plane", k), screen(corners)))
    tips = camera.project(pivot + ax * arm, size)
    ends = tips if grow is None else camera.project(pivot + ax * (arm * grow)[:, None], size)
    for i in range(3):
        if float(np.linalg.norm(tips[i, :2] - center)) < MIN_ARM_PX or ends[i, 2] > 1.0:
            continue
        end = ends[i, :2]
        length = max(float(np.linalg.norm(end - center)), 1e-9)
        start = center + (end - center) / length * min(CENTER_PX, 0.5 * length)
        shapes.append(_Arm(Handle("axis", i), start, end, box=op == "scale"))
    return _Layout(pivot, center, ax, tuple(shapes))


def hit(
    camera: OrbitCamera,
    size: tuple[int, int],
    matrix: Mat,
    op: Operation,
    x: float,
    y: float,
    *,
    local: bool = False,
) -> Handle | None:
    """The handle at a point of the view, if any."""
    lay = _layout(camera, size, matrix, op, local)
    return None if lay is None else lay.hit(x, y)


# ---- drags ---------------------------------------------------------------------------------- #


class _Drag:
    """One drag, press to release; `pose` turns the pointer into the pose after it."""

    def __init__(self, handle: Handle, start: Mat, lay: _Layout) -> None:
        self.handle, self.start, self.pivot, self.axes = handle, start, lay.pivot, lay.axes
        self.last = start

    def pose(self, ray: Ray, pos: tuple[float, float], snap: float | None) -> Mat:
        raise NotImplementedError

    def readout(self) -> str:
        raise NotImplementedError


class _Move(_Drag):
    """Along an axis (closest point to the ray), in a plane or in the view plane."""

    def __init__(self, handle: Handle, start: Mat, lay: _Layout, cam: OrbitCamera, ray: Ray):
        super().__init__(handle, start, lay)
        self.delta = np.zeros(3)
        self.steps = np.zeros(3)
        k = handle.axis
        if handle.part == "axis":
            assert k is not None
            self.dirs = self.axes[[k]]
            self.normal: Vec | None = None
            self.t0 = along(self.pivot, self.dirs[0], *ray) or 0.0
            return
        if handle.part == "plane":
            assert k is not None
            self.normal, self.dirs = self.axes[k], np.delete(self.axes, k, axis=0)
        else:
            self.normal, self.dirs = cam.forward.copy(), self.axes
        hit0 = ray_plane(*ray, self.pivot, self.normal)
        self.p0 = self.pivot if hit0 is None else hit0

    def pose(self, ray: Ray, pos: tuple[float, float], snap: float | None) -> Mat:
        if self.normal is None:
            t = along(self.pivot, self.dirs[0], *ray)
            raw = None if t is None else self.dirs[0] * (t - self.t0)
        else:
            p = ray_plane(*ray, self.pivot, self.normal)
            raw = None if p is None else p - self.p0
        if raw is not None:
            self.steps = np.array([snapped(float(d @ raw), snap) for d in self.dirs])
            self.delta = self.steps @ self.dirs
        m = self.start.copy()
        m[:3, 3] += self.delta
        self.last = m
        return m

    def readout(self) -> str:
        if self.normal is None:
            return f"{self.handle.label} {self.steps[0]:+.1f}"
        return f"{self.handle.label} {float(np.linalg.norm(self.delta)):.1f}".strip()


class _Turn(_Drag):
    """About a ring's axis or the view axis, by the hit point's angle around the pivot."""

    def __init__(
        self,
        handle: Handle,
        start: Mat,
        lay: _Layout,
        cam: OrbitCamera,
        ray: Ray,
        pos: tuple[float, float],
    ):
        super().__init__(handle, start, lay)
        k = handle.axis
        self.normal = -cam.forward if k is None else self.axes[k]
        self.center = lay.center
        # a ring seen edge-on gives wild plane hits: turn by the screen angle instead
        self.screen = abs(float(self.normal @ ray[1])) < EDGE_ON
        self.sign = 1.0 if float(self.normal @ (cam.eye - self.pivot)) >= 0.0 else -1.0
        self.prev = self._vec(ray, pos)
        self.total = 0.0
        self.degrees = 0.0

    def _vec(self, ray: Ray, pos: tuple[float, float]) -> Vec | None:
        if self.screen:
            v = np.array([pos[0] - self.center[0], self.center[1] - pos[1], 0.0])
        else:
            hit = ray_plane(*ray, self.pivot, self.normal)
            if hit is None:
                return None
            v = hit - self.pivot
        return v if float(np.linalg.norm(v)) > 1e-9 else None

    def pose(self, ray: Ray, pos: tuple[float, float], snap: float | None) -> Mat:
        v = self._vec(ray, pos)
        if v is not None and self.prev is not None:
            if self.screen:
                self.total += self.sign * angle(self.prev, v, np.array([0.0, 0.0, 1.0]))
            else:
                self.total += angle(self.prev, v, self.normal)
        if v is not None:
            self.prev = v
        self.degrees = snapped(self.total, snap)
        self.last = about(self.pivot, rotation(self.normal, self.degrees)) @ self.start
        return self.last

    def readout(self) -> str:
        return f"{self.handle.label} {self.degrees:+.1f}\N{DEGREE SIGN}".strip()


class _Grow(_Drag):
    """Along a local axis by the ratio of distances from the pivot, or uniformly by a sideways
    drag: one arm rightwards doubles the size, so a rightward arm's end moves with the pointer."""

    def __init__(
        self, handle: Handle, start: Mat, lay: _Layout, ray: Ray, pos: tuple[float, float]
    ):
        super().__init__(handle, start, lay)
        k = handle.axis
        self.dir = None if k is None else self.axes[k]
        self.t0 = 0.0 if self.dir is None else along(self.pivot, self.dir, *ray) or 0.0
        self.x0 = pos[0]
        self.raw = 1.0
        self.factor = 1.0

    def pose(self, ray: Ray, pos: tuple[float, float], snap: float | None) -> Mat:
        d = self.dir
        if d is None:
            self.raw = 1.0 + (pos[0] - self.x0) / ARM_PX
        else:
            t = along(self.pivot, d, *ray)
            if t is not None and abs(self.t0) > 1e-12:
                self.raw = t / self.t0
        f = self.factor = max(1.0 + snapped(self.raw - 1.0, snap), MIN_SCALE)
        lin = f * np.eye(3) if d is None else np.eye(3) + (f - 1.0) * np.outer(d, d)
        self.last = about(self.pivot, lin) @ self.start
        return self.last

    @property
    def grow(self) -> Vec:
        """Each axis arm's length in arms."""
        out = np.full(3, self.factor)
        if self.handle.axis is not None:
            out = np.ones(3)
            out[self.handle.axis] = self.factor
        return out

    def readout(self) -> str:
        return f"{self.handle.label} \N{MULTIPLICATION SIGN}{self.factor:.2f}".strip()


# ---- the gizmo ------------------------------------------------------------------------------ #


class Manipulator:
    """One viewport's gizmo: feed it pointer events, paint it after the picture."""

    def __init__(self) -> None:
        self._drag: _Drag | None = None
        self._hover: Handle | None = None

    def _grow(self) -> Vec | None:
        return self._drag.grow if isinstance(self._drag, _Grow) else None

    @property
    def hot(self) -> bool:
        """Hovered or dragging."""
        return self._drag is not None or self._hover is not None

    def cancel(self) -> Mat | None:
        """Abandons a drag; the pose it started from."""
        drag, self._drag = self._drag, None
        return None if drag is None else drag.start.copy()

    def pointer(
        self,
        ev: Pointer,
        camera: OrbitCamera,
        matrix: Mat,
        op: Operation,
        *,
        local: bool = False,
        snap: float | None = None,
    ) -> Manipulation:
        """Hover, press, drag and release; a drag consumes every camera gesture."""
        drag = self._drag
        m = np.array(matrix if drag is None else drag.last, dtype=np.float64)
        if drag is not None and ev.kind in ("move", "release"):
            m = drag.pose(camera.ray(ev.x, ev.y, ev.size), ev.pos, snap or None).copy()
        lay = None if ev.kind == "leave" else _layout(camera, ev.size, m, op, local, self._grow())
        self._hover = None if lay is None else lay.hit(ev.x, ev.y)
        over = self._hover is not None
        if drag is not None:
            done = ev.kind == "release" and ev.button == Button.LEFT
            if done:
                self._drag = None
            return Manipulation(m, not done, over, done, Gesture.ALL)
        if ev.kind == "press" and ev.button == Button.LEFT and lay is not None and over:
            assert self._hover is not None
            self._drag = _begin(self._hover, op, m.copy(), lay, camera, ev)
            return Manipulation(m, True, True, consumed=Gesture.ALL)
        return Manipulation(m, False, over)

    def paint(
        self,
        o: Overlay,
        camera: OrbitCamera,
        size: tuple[int, int],
        matrix: Mat,
        op: Operation,
        *,
        local: bool = False,
    ) -> None:
        """The handles for `matrix`, the hovered or dragged one hot, and a drag's readout."""
        lay = _layout(camera, size, matrix, op, local, self._grow())
        if lay is None:
            return
        hot = self._drag.handle if self._drag is not None else self._hover
        inks = [Ink.HOT if s.handle == hot else s.handle.ink for s in lay.shapes]
        for s, ink in zip(lay.shapes, inks, strict=True):
            s.under(o, ink)
        # pick order is front first, so draw it last
        for s, ink in reversed(list(zip(lay.shapes, inks, strict=True))):
            s.draw(o, ink)
        if self._drag is not None:
            x, y = lay.center + CENTER_PX + 8.0
            o.text((float(x), float(y)), self._drag.readout())


def _begin(h: Handle, op: Operation, m: Mat, lay: _Layout, cam: OrbitCamera, ev: Pointer) -> _Drag:
    ray = cam.ray(ev.x, ev.y, ev.size)
    if op == "translate":
        return _Move(h, m, lay, cam, ray)
    if op == "rotate":
        return _Turn(h, m, lay, cam, ray, ev.pos)
    return _Grow(h, m, lay, ray, ev.pos)
