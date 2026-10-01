# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The orbit camera and its 4x4s: numpy only, no GL.

Matrices are `(4, 4)` float64 used as `M @ v` (translation in the last column). World space is
engine space, X across, Y up, +Z forward (a monster's nose), so nothing is converted: yaw 0
looks along -Z at the subject's front. What differs between workspaces (named views, near/far,
zoom limits) is a `Lens`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

Mat = npt.NDArray[np.float64]
Vec = npt.NDArray[np.float64]

UP: Vec = np.array([0.0, 1.0, 0.0])

#: name -> (yaw, pitch) in degrees; yaw turns around Y from +Z, pitch lifts the eye.
VIEWS: Mapping[str, tuple[float, float]] = {
    "front": (0.0, 8.0),
    "back": (180.0, 8.0),
    "side": (90.0, 7.0),
    "other_side": (-90.0, 7.0),
    "three": (40.0, 20.0),
    "top": (0.0, 80.0),
}


@dataclass(frozen=True)
class Limit:
    """A distance bound: `max(scale * framed distance, distance * current distance, floor)`."""

    scale: float = 0.0
    distance: float = 0.0
    floor: float = 0.0

    def of(self, scale: float, distance: float) -> float:
        return max(self.scale * scale, self.distance * distance, self.floor)


@dataclass(frozen=True)
class Lens:
    """A workspace's camera constants; the defaults suit one object at the origin."""

    views: Mapping[str, tuple[float, float]] = field(default_factory=lambda: dict(VIEWS))
    view: str = "three"
    fov: float = 40.0
    distance: float = 10.0
    #: framing: 1.0 touches the bounding sphere
    margin: float = 1.15
    #: short of the pole, where `look_at`'s up vector is parallel to the view
    max_pitch: float = 89.0
    dolly_rate: float = 1.1
    near: Limit = Limit(scale=1e-3, floor=1e-4)
    far: Limit = Limit(scale=40.0, distance=4.0)
    #: how close a dolly or `fly_to` may come (the distance term is unused)
    closest: Limit = Limit(scale=1e-3)


def look_at(eye: Sequence[float] | Vec, target: Sequence[float] | Vec, up: Vec = UP) -> Mat:
    """Right-handed view matrix: `M @ v` takes a world point to eye space."""
    eye_ = np.asarray(eye, dtype=np.float64)
    fwd = np.asarray(target, dtype=np.float64) - eye_
    n = np.linalg.norm(fwd)
    if n < 1e-12:
        raise ValueError("look_at: the eye is at the target")
    fwd /= n
    right = np.cross(fwd, np.asarray(up, dtype=np.float64))
    rn = np.linalg.norm(right)
    if rn < 1e-9:  # looking along `up`: any perpendicular will do
        right = np.cross(fwd, np.array([0.0, 0.0, 1.0]))
        rn = np.linalg.norm(right)
        if rn < 1e-9:
            right, rn = np.array([1.0, 0.0, 0.0]), np.float64(1.0)
    right /= rn
    m = np.eye(4)
    m[0, :3], m[1, :3], m[2, :3] = right, np.cross(right, fwd), -fwd
    m[:3, 3] = -(m[:3, :3] @ eye_)
    return m


def perspective(fov_y_deg: float, aspect: float, near: float, far: float) -> Mat:
    """Right-handed projection onto GL's z in [-1, 1]."""
    if aspect <= 0:
        raise ValueError(f"perspective: aspect must be > 0, got {aspect!r}")
    if not 0 < near < far:
        raise ValueError(f"perspective: need 0 < near < far, got {near!r}/{far!r}")
    f = 1.0 / math.tan(math.radians(fov_y_deg) * 0.5)
    m = np.zeros((4, 4))
    m[0, 0] = f / aspect
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = (2.0 * far * near) / (near - far)
    m[3, 2] = -1.0
    return m


def gl_bytes(m: Mat) -> bytes:
    """A row-major `(4, 4)` as the column-major float32 a GL uniform wants."""
    return np.ascontiguousarray(m.T, dtype="f4").tobytes()


@dataclass(frozen=True)
class Bounds:
    """An axis-aligned box; cameras frame its bounding sphere."""

    lo: Vec
    hi: Vec

    @classmethod
    def of(cls, points: npt.ArrayLike) -> Bounds:
        p = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        if not len(p):
            return cls(np.zeros(3), np.zeros(3))
        return cls(p.min(axis=0), p.max(axis=0))

    @classmethod
    def union(cls, *parts: Bounds | None) -> Bounds:
        real = [b for b in parts if b is not None]
        if not real:
            return cls(np.zeros(3), np.zeros(3))
        return cls(np.min([b.lo for b in real], axis=0), np.max([b.hi for b in real], axis=0))

    @property
    def center(self) -> Vec:
        return (self.lo + self.hi) * 0.5

    @property
    def size(self) -> Vec:
        return self.hi - self.lo

    @property
    def radius(self) -> float:
        """Half the diagonal: the bounding sphere."""
        return float(np.linalg.norm(self.size) * 0.5)

    def __repr__(self) -> str:
        return f"<Bounds {np.round(self.lo, 1)}..{np.round(self.hi, 1)} r={self.radius:.1f}>"


class OrbitCamera:
    """Target, distance, yaw and pitch (degrees); pitch is clamped short of the pole."""

    def __init__(
        self,
        lens: Lens | None = None,
        target: Sequence[float] = (0.0, 0.0, 0.0),
        distance: float | None = None,
        yaw: float | None = None,
        pitch: float | None = None,
    ) -> None:
        self.lens = lens or Lens()
        start = self.lens.views.get(self.lens.view, (0.0, 0.0))
        self.target: Vec = np.asarray(target, dtype=np.float64).copy()
        self.distance = float(self.lens.distance if distance is None else distance)
        self.yaw = float(start[0] if yaw is None else yaw)
        self._pitch = 0.0
        self.pitch = float(start[1] if pitch is None else pitch)
        self.fov = float(self.lens.fov)
        #: the distance `frame` chose: near/far and zoom limits scale with it
        self.scale = self.distance

    @property
    def pitch(self) -> float:
        return self._pitch

    @pitch.setter
    def pitch(self, v: float) -> None:
        m = self.lens.max_pitch
        self._pitch = max(-m, min(m, float(v)))

    @property
    def direction(self) -> Vec:
        """Unit vector from the target to the eye."""
        y, p = math.radians(self.yaw), math.radians(self._pitch)
        cp = math.cos(p)
        return np.array([cp * math.sin(y), math.sin(p), cp * math.cos(y)])

    @property
    def eye(self) -> Vec:
        return self.target + self.direction * self.distance

    @property
    def forward(self) -> Vec:
        return -self.direction

    @property
    def near(self) -> float:
        return self.lens.near.of(self.scale, self.distance)

    @property
    def far(self) -> float:
        return self.lens.far.of(self.scale, self.distance)

    def frame(self, bounds: Bounds, margin: float | None = None) -> OrbitCamera:
        """Aims at `bounds` and backs off until its sphere fits the vertical field of view."""
        self.target = bounds.center.copy()
        r = bounds.radius or 1.0
        m = self.lens.margin if margin is None else margin
        self.distance = r * m / math.sin(math.radians(self.fov) * 0.5)
        self.scale = self.distance
        return self

    def look(self, name: str) -> OrbitCamera:
        """Snaps to a named view, keeping target and distance."""
        if name not in self.lens.views:
            raise KeyError(f"no view {name!r} (have: {', '.join(sorted(self.lens.views))})")
        self.yaw, self.pitch = self.lens.views[name]
        return self

    def fly_to(self, point: Sequence[float] | Vec, distance: float | None = None) -> OrbitCamera:
        """Re-aims at `point`, keeping the angle; optionally dollies to `distance`."""
        self.target = np.asarray(point, dtype=np.float64).copy()
        if distance is not None:
            self.distance = max(float(distance), self.lens.closest.of(self.scale, 0.0))
        return self

    def orbit(self, dx: float, dy: float, speed: float = 0.4) -> None:
        """Drag pixels to degrees; yaw wraps, pitch clamps."""
        self.yaw = (self.yaw - dx * speed) % 360.0
        self.pitch = self._pitch + dy * speed

    def pan(self, dx: float, dy: float, viewport_h: int) -> None:
        """Slides the target in the view plane, a pixel of drag per pixel at the target."""
        if viewport_h <= 0:
            return
        world_per_px = 2.0 * self.distance * math.tan(math.radians(self.fov) * 0.5) / viewport_h
        v = self.view
        self.target += (-dx * v[0, :3] + dy * v[1, :3]) * world_per_px

    def dolly(self, ticks: float) -> None:
        """Wheel ticks to a multiplicative zoom, never closer than the lens allows."""
        closest = self.lens.closest.of(self.scale, 0.0)
        self.distance = max(closest, self.distance * (self.lens.dolly_rate**-ticks))

    @property
    def view(self) -> Mat:
        return look_at(self.eye, self.target, UP)

    def projection(self, aspect: float) -> Mat:
        return perspective(self.fov, aspect, self.near, self.far)

    def mvp(self, aspect: float, model: Mat | None = None) -> Mat:
        m = self.projection(aspect) @ self.view
        return m if model is None else m @ model

    def ray(self, px: float, py: float, size: tuple[int, int]) -> tuple[Vec, Vec]:
        """`(origin, unit direction)` of the world ray through a pixel (row 0 at the top)."""
        w, h = max(int(size[0]), 1), max(int(size[1]), 1)
        nx, ny = 2.0 * px / w - 1.0, 1.0 - 2.0 * py / h
        inv = np.linalg.inv(self.mvp(w / float(h)))
        a = inv @ np.array([nx, ny, -1.0, 1.0])
        b = inv @ np.array([nx, ny, 1.0, 1.0])
        a3, b3 = a[:3] / a[3], b[:3] / b[3]
        d = b3 - a3
        return a3, d / np.linalg.norm(d)

    def project(self, points: npt.ArrayLike, size: tuple[int, int]) -> Mat:
        """World points to `(n, 3)`: pixel x, pixel y (row 0 top), clip depth (> 1 behind)."""
        p = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        w, h = max(int(size[0]), 1), max(int(size[1]), 1)
        hom = np.concatenate([p, np.ones((len(p), 1))], 1) @ self.mvp(w / float(h)).T
        wv = hom[:, 3:4]
        ok = np.abs(wv[:, 0]) > 1e-9
        ndc = np.zeros((len(p), 3))
        ndc[ok] = hom[ok, :3] / wv[ok]
        out = np.empty((len(p), 3))
        out[:, 0] = (ndc[:, 0] + 1.0) * 0.5 * w
        out[:, 1] = (1.0 - ndc[:, 1]) * 0.5 * h
        out[:, 2] = np.where(wv[:, 0] > 0, ndc[:, 2], 2.0)
        return out

    def __repr__(self) -> str:
        return (
            f"<OrbitCamera target={np.round(self.target, 1)} d={self.distance:.1f} "
            f"yaw={self.yaw:.1f} pitch={self._pitch:.1f} fov={self.fov:.0f}>"
        )
