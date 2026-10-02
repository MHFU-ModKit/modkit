# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The map viewport: the section's mesh, its collision by class, the reference layers, every
one a plain toggle, over the stage's own fog colour.

The camera frames where the near-field detail is (`StageMesh.focus`) widened to the exits and
spheres, never the floor box, which on a small area is the whole lattice.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import moderngl
import numpy as np
from mhfu import stage as S

from mhfu_studio.shell.camera import Bounds, Lens, Limit, Mat, OrbitCamera
from mhfu_studio.shell.lines import Lines, axes_geometry, bounds_geometry, concat
from mhfu_studio.shell.target import CLEAR, RGBA, SAMPLES
from mhfu_studio.shell.viewport import Viewport, depth_write_off

from ..core.atlas import stage_title
from ..core.scene import TURN, Array, MapScene
from .overlays import (
    ARRIVAL_COLOR,
    EXIT_COLOR,
    SPHERE_COLOR,
    CollisionOverlay,
    arrival_geometry,
    exit_geometry,
    lattice_geometry,
    sphere_geometry,
)
from .stage_mesh import StageMesh

LENS = Lens(
    views={
        "iso": (45.0, 35.0),
        "top": (0.0, 89.0),
        "front": (0.0, 15.0),
        "back": (180.0, 15.0),
        "side": (90.0, 15.0),
        "low": (30.0, 8.0),
    },
    view="iso",
    fov=45.0,
    distance=10_000.0,
    margin=1.1,
    max_pitch=89.5,
    dolly_rate=1.15,
    near=Limit(distance=0.002, floor=1.0),
    far=Limit(scale=20.0, distance=60.0, floor=1000.0),
    closest=Limit(floor=1.0),
)
EYE_HEIGHT = 140.0
"""The eye above the floor, standing where the hunter stands."""
AHEAD = 1500.0
HUNTER = "hunter"
"""A view that is no lens preset: the eye at the first landing point."""


def hunter(cam: OrbitCamera, floor_point: Sequence[float], yaw_deg: float) -> None:
    """Eye 140 above `floor_point`, level, facing `yaw_deg` (the exit table's, 0 = +Z)."""
    eye = np.asarray(floor_point, np.float64) + (0.0, EYE_HEIGHT, 0.0)
    r = math.radians(yaw_deg)
    d = np.array([math.sin(r), 0.0, math.cos(r)])
    cam.target = eye + d * AHEAD
    cam.distance = AHEAD
    cam.yaw = math.degrees(math.atan2(-d[0], -d[2])) % 360.0
    cam.pitch = 0.0


@dataclass
class Label:
    """A world-anchored text the window draws over the picture."""

    pos: Array
    text: str
    color: RGBA
    kind: str


class MapViewport(Viewport):
    def __init__(
        self, ctx: moderngl.Context, size: tuple[int, int] = (1280, 800), samples: int = SAMPLES
    ) -> None:
        super().__init__(ctx, size, samples, LENS)
        self.scene: MapScene | None = None
        self.mesh: StageMesh | None = None
        self.collision: CollisionOverlay | None = None
        self.arrivals: list[tuple[int, S.Exit]] = []
        self.background = CLEAR
        self.use_fog_background = True
        self.show_mesh = True
        self.wireframe = False
        self.show_collision = False
        self.collision_fill = True
        self.collision_edges = True
        self.collision_xray = False
        self.show_lattice = False
        self.show_exits = True
        self.show_arrivals = True
        self.show_spheres = True
        self.show_bounds = False
        self.show_axes = False
        self.show_labels = True
        self._lattice = Lines(ctx)
        self._exits = Lines(ctx)
        self._arrivals = Lines(ctx)
        self._spheres = Lines(ctx)
        self._box = Lines(ctx)
        self._axes = Lines(ctx)
        self._labels: list[Label] = []
        self.framed = Bounds.of(np.zeros((1, 3)))

    def set_scene(
        self,
        scene: MapScene,
        arrivals: Sequence[tuple[int, S.Exit]] = (),
        frame_camera: bool = True,
    ) -> None:
        self._release_scene()
        self.scene = scene
        self.arrivals = list(arrivals)
        self.mesh = StageMesh(self.ctx, scene)
        self.collision = CollisionOverlay(self.ctx, scene)
        self.framed = self._framing()
        floor = scene.floor
        if floor is not None and floor.n_triangles:
            self._lattice.set(*lattice_geometry(floor, y=float(self.framed.lo[1]) - 1.0))
        else:
            self._lattice.clear()
        self._exits.set(*concat(*[exit_geometry(e) for e in scene.exits]))
        self._arrivals.set(
            *concat(*[arrival_geometry(e.dest, e.yaw * 360.0 / TURN) for _, e in self.arrivals])
        )
        self._spheres.set(*concat(*[sphere_geometry(s) for s in scene.spheres]))
        self._box.set(*bounds_geometry(self.framed))
        self._axes.set(*axes_geometry(max(self.framed.radius * 0.25, 500.0)))
        self._labels = self._build_labels()
        if frame_camera:
            self.frame_all()

    def _framing(self) -> Bounds:
        if self.scene is None or self.mesh is None:
            return Bounds.of(np.zeros((1, 3)))
        pts = [e.trigger for e in self.scene.exits] + [s.pos for s in self.scene.spheres]
        pts += [e.dest for _, e in self.arrivals]
        parts = [self.mesh.focus] + ([Bounds.of(np.array(pts, np.float64))] if pts else [])
        b = Bounds.union(*parts)
        return b if b.radius >= 1.0 else self.mesh.bounds

    def _build_labels(self) -> list[Label]:
        sc = self.scene
        if sc is None:
            return []
        out = []
        for e in sc.exits:
            cx, cy, cz = e.trigger
            text = f"-> {stage_title(e.target)}"
            out.append(Label(np.array((cx, cy + e.height, cz)), text, EXIT_COLOR, "exit"))
        for frm, x in self.arrivals:
            pos = np.array((x.dest[0], x.dest[1] + 220.0, x.dest[2]))
            out.append(Label(pos, f"from st{frm:03d}", ARRIVAL_COLOR, "arrival"))
        for s in sc.spheres:
            pos = np.array((s.pos[0], s.pos[1] + s.radius, s.pos[2]))
            out.append(Label(pos, f"sphere {s.id}", SPHERE_COLOR, "sphere"))
        return out

    def labels(self) -> list[Label]:
        """The labels of the layers that are on."""
        if not self.show_labels:
            return []
        keep = {"exit": self.show_exits, "arrival": self.show_arrivals, "sphere": self.show_spheres}
        return [lb for lb in self._labels if keep.get(lb.kind, True)]

    def bounds(self) -> Bounds:
        return self.framed

    def clear_color(self) -> RGBA:
        if self.use_fog_background and self.scene is not None:
            r, g, b, _ = self.scene.fog_rgba
            return (r / 255.0, g / 255.0, b / 255.0, 1.0)
        return self.background

    def stand_at_entry(self) -> bool:
        """The eye where the hunter arrives: the first landing point, else the floor's middle.
        Returns whether a landing point was used."""
        sc = self.scene
        if sc is None:
            return False
        if self.arrivals:
            _, e = self.arrivals[0]
            y = sc.floor_height(e.dest[0], e.dest[2])
            hunter(
                self.camera,
                (e.dest[0], e.dest[1] if y is None else y, e.dest[2]),
                e.yaw * 360.0 / TURN,
            )
            return True
        c = self.framed.center
        y = sc.floor_height(float(c[0]), float(c[2]))
        hunter(self.camera, (c[0], c[1] if y is None else y, c[2]), 0.0)
        return False

    def look(self, view: str) -> None:
        """A lens preset, or the hunter's eye."""
        if view == HUNTER:
            self.stand_at_entry()
        else:
            self.camera.look(view)

    def draw_scene(self, mvp: Mat) -> None:
        ctx = self.ctx
        if self.show_mesh and self.mesh is not None:
            self.mesh.render(mvp, wireframe=self.wireframe)
        if self.show_collision and self.collision is not None:
            if self.collision_xray:
                ctx.disable(ctx.DEPTH_TEST)
            with depth_write_off(ctx):
                self.collision.render(mvp, fill=self.collision_fill, edges=self.collision_edges)
            if self.collision_xray:
                ctx.enable(ctx.DEPTH_TEST)
        layers = (
            (self.show_lattice, self._lattice),
            (self.show_exits, self._exits),
            (self.show_arrivals, self._arrivals),
            (self.show_spheres, self._spheres),
            (self.show_bounds, self._box),
            (self.show_axes, self._axes),
        )
        with depth_write_off(ctx):
            for on, lines in layers:
                if on:
                    lines.render(mvp)

    def _release_scene(self) -> None:
        for part in (self.mesh, self.collision):
            if part is not None:
                part.release()
        self.mesh = self.collision = None
        self.scene = None

    def release(self) -> None:
        self._release_scene()
        for o in (self._lattice, self._exits, self._arrivals, self._spheres, self._box, self._axes):
            o.release()
        super().release()

    def __repr__(self) -> str:
        name = f"st{self.scene.stage:03d}" if self.scene is not None else "no scene"
        return f"<MapViewport {name} {self.target.size[0]}x{self.target.size[1]} {self.camera!r}>"
