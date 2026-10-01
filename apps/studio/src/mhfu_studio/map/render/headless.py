# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Stages drawn offscreen with fixed cameras: one stage from several views, or a whole map row
one tile per section. A row's tiles draw over the studio's background instead of each
section's fog, so one clear colour measures them all."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import moderngl
from mhfu import stage as S

from mhfu_studio.harness.render import Image8, Shots, offscreen
from mhfu_studio.shell.context import describe

from ..core.scene import MapScene
from .viewport import MapViewport

Arrivals = Sequence[tuple[int, S.Exit]]


@dataclass(frozen=True)
class Layers:
    """What a render draws besides the mesh."""

    collision: bool = False
    lattice: bool = False
    backdrop: bool = True
    mode: int = 0
    """The draw mode: 0 textured, 1 vertex colour, 2 texture only, 3 by group, 4 flat."""
    fog: bool = True

    def apply(self, vp: MapViewport) -> None:
        vp.show_collision = self.collision
        vp.show_lattice = self.lattice
        vp.use_fog_background = self.fog
        if vp.mesh is not None:
            vp.mesh.show_backdrop = self.backdrop
            vp.mesh.mode = self.mode


def views(
    scene: MapScene,
    names: Sequence[str],
    *,
    arrivals: Arrivals = (),
    layers: Layers | None = None,
    size: tuple[int, int] = (1280, 800),
    samples: int = 4,
    ctx: moderngl.Context | None = None,
    label: Callable[[str], str] = lambda view: view,
) -> Shots:
    """One frame per view (a lens preset, or `hunter`), each framed on the section first."""
    with offscreen(lambda c: MapViewport(c, size, samples), ctx) as vp:
        vp.set_scene(scene, arrivals)
        (layers or Layers()).apply(vp)
        out: dict[str, Image8] = {}
        for name in names:
            vp.frame_all()
            vp.look(name)
            vp.draw()
            out[label(name)] = vp.target.read()
        return Shots(out, describe(vp.ctx), vp.clear_color())


def tiles(
    scenes: Sequence[tuple[MapScene, Arrivals]],
    view: str,
    *,
    layers: Layers | None = None,
    size: tuple[int, int] = (480, 300),
    samples: int = 4,
    ctx: moderngl.Context | None = None,
) -> Shots:
    """One tile per scene from the same view, named stNNN, over the plain background."""
    lay = layers or Layers()
    flat = Layers(lay.collision, lay.lattice, lay.backdrop, lay.mode, fog=False)
    with offscreen(lambda c: MapViewport(c, size, samples), ctx) as vp:
        out: dict[str, Image8] = {}
        for scene, arrivals in scenes:
            vp.set_scene(scene, arrivals)
            flat.apply(vp)
            vp.look(view)
            vp.draw()
            out[f"st{scene.stage:03d}"] = vp.target.read()
        return Shots(out, describe(vp.ctx), vp.clear_color())
