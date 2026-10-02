# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Collision volumes on the live pose: hurtboxes (by part) and attack volumes (by set).

A volume sits at a bone-relative offset, so it rides the bone's whole world matrix, not only
its origin (one that followed the origin would look right at bind and drift as soon as the
bone turned). A volume on a bone the rig lacks draws nowhere and is counted in `orphans`.
Bones 126/127 are the attack node's own space and draw at the actor's origin; 125 is a joiner
with no geometry, like the hurtboxes' 0x7D.
"""

from __future__ import annotations

import colorsys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

import moderngl
import numpy as np
import numpy.typing as npt
from mhfu import hitzone
from mhfu.em.intel import AttackSet, HitSphere
from mhfu_port.manifest import NODE_CAPSULE, NODE_SPHERE, Hitbox, Hurtbox

from mhfu_studio.shell.camera import Mat, OrbitCamera
from mhfu_studio.shell.lines import Lines
from mhfu_studio.shell.viewport import depth_write_off

from .skeleton import nearest

Floats = npt.NDArray[np.float64]
Palette = Literal["part", "set"]
RGB = tuple[float, float, float]

#: one per `entity+0x3B8` accumulator; 0 is grey, it means nobody
PART_COLORS: tuple[RGB, ...] = (
    (0.55, 0.58, 0.64),
    (0.95, 0.35, 0.35),
    (0.98, 0.68, 0.25),
    (0.92, 0.88, 0.30),
    (0.42, 0.82, 0.45),
    (0.30, 0.75, 0.80),
    (0.45, 0.60, 0.95),
    (0.80, 0.50, 0.92),
)
#: an unselected group while something is selected, the rest, and the selection's shell
DIM_ALPHA = 0.05
LIVE_ALPHA = 0.85
FILL_ALPHA = 0.16
RINGS_SEGMENTS = 24
FILL_LAT = 8
FILL_LON = 14


def set_color(group: int) -> RGB:
    """A golden-angle hue per attack set, folded warm: the hurtboxes own blue and green."""
    hue = (0.02 + int(group) * 0.381966) % 1.0
    hue = hue * 0.55 if hue < 0.5 else 0.72 + (hue - 0.5) * 0.56
    return colorsys.hsv_to_rgb(hue % 1.0, 0.72, 0.96)


def group_color(group: int, palette: Palette) -> RGB:
    return set_color(group) if palette == "set" else PART_COLORS[int(group) % len(PART_COLORS)]


def sphere_geometry(centre: npt.ArrayLike, radius: float, segments: int = RINGS_SEGMENTS) -> Floats:
    """Three great circles as line endpoints."""
    c = np.asarray(centre, np.float64).reshape(3)
    t = np.linspace(0.0, 2.0 * np.pi, segments + 1)
    cos, sin, z = np.cos(t) * radius, np.sin(t) * radius, np.zeros(segments + 1)
    rings = (np.stack(r, axis=1) + c for r in ((cos, sin, z), (cos, z, sin), (z, cos, sin)))
    return np.concatenate([np.stack([p[:-1], p[1:]], axis=1).reshape(-1, 3) for p in rings])


def _basis(axis: Floats) -> tuple[Floats, Floats]:
    seed = np.array([1.0, 0.0, 0.0]) if abs(float(axis[1])) > 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(axis, seed)
    u /= np.linalg.norm(u)
    return u, np.cross(axis, u)


def capsule_geometry(
    a: npt.ArrayLike, b: npt.ArrayLike, radius: float, segments: int = RINGS_SEGMENTS
) -> Floats:
    """Both end spheres and four rails along the axis."""
    a_, b_ = np.asarray(a, np.float64).reshape(3), np.asarray(b, np.float64).reshape(3)
    parts = [sphere_geometry(a_, radius, segments), sphere_geometry(b_, radius, segments)]
    n = float(np.linalg.norm(b_ - a_))
    if n > 1e-9:
        u, v = _basis((b_ - a_) / n)
        parts += [np.stack([a_ + d * radius, b_ + d * radius]) for d in (u, -u, v, -v)]
    return np.concatenate(parts)


def _grid_tris(pts: Floats) -> Floats:
    """An `(R, C, 3)` lattice as a triangle soup."""
    a, b, c, d = pts[:-1, :-1], pts[1:, :-1], pts[1:, 1:], pts[:-1, 1:]
    return np.stack([a, b, c, a, c, d], axis=2).reshape(-1, 3)


def sphere_surface(
    centre: npt.ArrayLike, radius: float, lat: int = FILL_LAT, lon: int = FILL_LON
) -> Floats:
    theta = np.linspace(0.0, np.pi, 2 * lat + 1)[:, None]
    phi = np.linspace(0.0, 2.0 * np.pi, lon + 1)[None, :]
    st, ct = np.sin(theta), np.cos(theta)
    unit = np.stack([st * np.cos(phi), np.tile(ct, (1, lon + 1)), st * np.sin(phi)], axis=2)
    return _grid_tris(unit * radius + np.asarray(centre, np.float64).reshape(1, 1, 3))


def capsule_surface(
    a: npt.ArrayLike, b: npt.ArrayLike, radius: float, lat: int = FILL_LAT, lon: int = FILL_LON
) -> Floats:
    """One sphere lattice whose centre is `b` above the equator and `a` below, the equator
    ring twice: the quads between the two copies are the tube."""
    a_, b_ = np.asarray(a, np.float64).reshape(3), np.asarray(b, np.float64).reshape(3)
    n = float(np.linalg.norm(b_ - a_))
    if n <= 1e-9:
        return sphere_surface(a_, radius, lat, lon)
    axis = (b_ - a_) / n
    u, v = _basis(axis)
    half = np.linspace(0.0, np.pi / 2.0, lat + 1)
    theta = np.concatenate([half, np.pi - half[::-1]])[:, None]
    phi = np.linspace(0.0, 2.0 * np.pi, lon + 1)[None, :]
    radial = u * np.cos(phi)[..., None] + v * np.sin(phi)[..., None]
    dirs = axis * np.cos(theta)[..., None] + radial * np.sin(theta)[..., None]
    centre = np.where((np.cos(theta) >= 0.0)[..., None], b_, a_)
    return _grid_tris(centre + dirs * radius)


@dataclass(frozen=True)
class Volume:
    """One drawable volume in the rig's own indices; `group` is the part for a hurtbox and
    the set for an attack volume, what it is coloured and filtered by."""

    bone: int
    radius: float
    part: int = 0
    hitzone_row: int = 0
    a: tuple[float, float, float] = (0.0, 0.0, 0.0)
    b: tuple[float, float, float] | None = None
    label: str = ""
    group: int = 0

    @property
    def is_capsule(self) -> bool:
        return self.b is not None

    @property
    def is_node_space(self) -> bool:
        return self.bone in (NODE_CAPSULE, NODE_SPHERE)

    @property
    def is_marker(self) -> bool:
        return self.bone in hitzone.MARKER_BONES


def _vec(v: Sequence[float] | None) -> tuple[float, float, float] | None:
    return None if v is None else (float(v[0]), float(v[1]), float(v[2]))


def volume(s: HitSphere | Hurtbox | Hitbox | Volume, group: int | None = None) -> Volume:
    """A host sphere (stamped with `group`, its set, when it is an attack's), a manifest
    hurtbox, or a manifest hitbox (grouped by its set)."""
    if isinstance(s, Volume):
        return s
    if isinstance(s, HitSphere):
        part = s.part & hitzone.PART_MASK
        b = _vec(s.b) if s.is_capsule else None
        g = part if group is None else group
        return Volume(s.bone, s.radius, part, s.hitzone_row, _vec(s.a) or (0, 0, 0), b, "", g)
    a = _vec(s.offset) or (0.0, 0.0, 0.0)
    b = _vec(s.to) if s.is_capsule else None
    if isinstance(s, Hitbox):
        return Volume(s.bone, s.radius, 0, 0, a, b, s.label, int(s.set))
    part = (s.part or 0) & hitzone.PART_MASK
    return Volume(s.bone, s.radius, part, s.hitzone_row or 0, a, b, s.label, part)


def volumes(source: Iterable[HitSphere | Hurtbox | Hitbox | Volume]) -> list[Volume]:
    return [volume(s) for s in source]


def attack_volumes(sets: Iterable[AttackSet]) -> list[Volume]:
    """The host's attack sets as one list, each sphere grouped by its set."""
    return [volume(s, st.index) for st in sets for s in st.spheres]


def _upload(batch: Lines, pos: list[Floats], col: list[npt.NDArray[np.float32]]) -> None:
    if pos:
        batch.set(np.concatenate(pos), np.concatenate(col))
    else:
        batch.set(np.zeros((0, 3), "f4"), np.zeros((0, 4), "f4"))


class HitboxOverlay:
    """Volumes placed by the pose's world matrices, rebuilt whenever the pose moves; the
    selected group (or one selected volume) gets a translucent shell and the rest dims."""

    def __init__(
        self,
        ctx: moderngl.Context,
        vols: Sequence[Volume],
        n_bones: int,
        palette: Palette = "part",
    ) -> None:
        self.ctx = ctx
        self.volumes = list(vols)
        #: as given: `preview` puts one back from here
        self._given = list(vols)
        self.n_bones = int(n_bones)
        self.palette: Palette = palette
        self.visible: frozenset[int] | None = None
        self.selected_group: int | None = None
        self.selected_volume: int | None = None
        self.orphans = tuple(
            v for v in self.volumes if not 0 <= v.bone < self.n_bones and not v.is_marker
        )
        self._lines = Lines(ctx)
        self._fill = Lines(ctx, ctx.TRIANGLES)
        self._world: Floats | None = None
        self._dirty = True

    def set_pose(self, world: npt.ArrayLike) -> None:
        """`(joints, 4, 4)` world matrices, `Pose.world`."""
        self._world = np.asarray(world, np.float64)
        self._dirty = True

    def _key(self, g: int) -> int:
        return int(g) & hitzone.PART_MASK if self.palette == "part" else int(g)

    def set_visible(self, groups: Iterable[int] | None) -> None:
        v = None if groups is None else frozenset(self._key(g) for g in groups)
        if v != self.visible:
            self.visible, self._dirty = v, True

    def set_selected_group(self, group: int | None) -> None:
        g = None if group is None else self._key(group)
        if g != self.selected_group:
            self.selected_group, self._dirty = g, True

    def set_selected_volume(self, index: int | None) -> None:
        """Out of range clears: the list may have just shrunk."""
        i = index if index is not None and 0 <= index < len(self.volumes) else None
        if i != self.selected_volume:
            self.selected_volume, self._dirty = i, True

    def index_of(self, v: Volume) -> int | None:
        return next((i for i, x in enumerate(self.volumes) if x is v), None)

    def preview(self, index: int, v: Volume | None) -> None:
        """Draws `v` in place of volume `index`, a drag under way; None puts it back."""
        self.volumes[index] = self._given[index] if v is None else v
        self._dirty = True

    def drawable(self, v: Volume) -> bool:
        return 0 <= v.bone < self.n_bones or v.is_node_space

    def shows(self, index: int) -> bool:
        """Volume `index` is drawn: on the rig and in a visible group."""
        if not 0 <= index < len(self.volumes):
            return False
        v = self.volumes[index]
        return self.drawable(v) and (self.visible is None or v.group in self.visible)

    def shown(self) -> list[Volume]:
        return [v for i, v in enumerate(self.volumes) if self.shows(i)]

    def groups(self) -> list[int]:
        return sorted({v.group for v in self.volumes})

    def frame(self, v: Volume) -> Mat:
        """Its bone's world matrix under the current pose; the identity for the attack's own
        place, which draws at the actor's origin."""
        if v.is_node_space or self._world is None or not 0 <= v.bone < len(self._world):
            return np.eye(4)
        out: Mat = self._world[v.bone]
        return out

    def place(self, v: Volume) -> tuple[Floats, Floats | None]:
        """Its end points in world space under the current pose."""
        m = self.frame(v)
        rot, t = m[:3, :3], m[:3, 3]
        a = rot @ np.asarray(v.a, np.float64) + t
        return a, None if v.b is None else rot @ np.asarray(v.b, np.float64) + t

    def world_centres(self) -> Floats:
        """One point per shown volume."""
        out = []
        for v in self.shown():
            a, b = self.place(v)
            out.append(a if b is None else (a + b) * 0.5)
        return np.array(out, np.float64).reshape(-1, 3)

    def _rebuild(self) -> None:
        pos: list[Floats] = []
        col: list[npt.NDArray[np.float32]] = []
        fpos: list[Floats] = []
        fcol: list[npt.NDArray[np.float32]] = []
        one = None if self.selected_volume is None else self.volumes[self.selected_volume]
        focusing = one is not None or self.selected_group is not None
        for v in self.shown():
            a, b = self.place(v)
            g = sphere_geometry(a, v.radius) if b is None else capsule_geometry(a, b, v.radius)
            rgb = group_color(v.group, self.palette)
            focus = v is one if one is not None else v.group == self.selected_group
            alpha = DIM_ALPHA if focusing and not focus else LIVE_ALPHA
            pos.append(g)
            col.append(np.tile(np.array((*rgb, alpha), "f4"), (len(g), 1)))
            if focus:
                f = sphere_surface(a, v.radius) if b is None else capsule_surface(a, b, v.radius)
                fpos.append(f)
                fcol.append(np.tile(np.array((*rgb, FILL_ALPHA), "f4"), (len(f), 1)))
        _upload(self._lines, pos, col)
        _upload(self._fill, fpos, fcol)
        self._dirty = False

    @property
    def filled(self) -> int:
        """Vertices in the selection's shell, as last built."""
        return self._fill.count

    def render(self, mvp: Mat, *, alpha: float = 1.0) -> None:
        if self._dirty:
            self._rebuild()
        if self._fill.count:  # the shell first, depth writes off, or it hides its own outline
            with depth_write_off(self.ctx):
                self._fill.alpha = alpha
                self._fill.render(mvp)
        self._lines.alpha = alpha
        self._lines.render(mvp)

    def pick(
        self, camera: OrbitCamera, size: tuple[int, int], x: float, y: float, radius: float = 18.0
    ) -> Volume | None:
        """The shown volume whose centre is nearest pixel `(x, y)`, row 0 at the top."""
        i = nearest(camera, self.world_centres(), size, x, y, radius)
        return None if i is None else self.shown()[i]

    def release(self) -> None:
        self._lines.release()
        self._fill.release()

    def __repr__(self) -> str:
        kind = "sets" if self.palette == "set" else "parts"
        return (
            f"<HitboxOverlay {len(self.volumes)} volume(s), {len(self.orphans)} orphan(s), "
            f"{kind} {self.groups()}>"
        )
