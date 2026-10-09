# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""What `studio render monster` draws: one picture per frame and view of one clip."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import moderngl
from mhfu_port.model import Clip

from mhfu_studio.harness.render import Image8, Shots, offscreen
from mhfu_studio.monster.core.scene import Scene
from mhfu_studio.shell.context import describe

from .mesh import ISOLATE_HIDE, ISOLATE_OFF, ISOLATE_ONLY, MODES
from .viewport import MonsterViewport

ONLY = {None: ISOLATE_OFF, "tagged": ISOLATE_ONLY, "rest": ISOLATE_HIDE}


@dataclass(frozen=True)
class Options:
    """`clip` is a slot or a manifest name, None the default pose's; `frames` None is
    mid-clip; `bind` draws the bind pose and ignores both."""

    views: Sequence[str] = ("three",)
    clip: int | str | None = None
    frames: Sequence[float] | None = None
    bind: bool = False
    shading: str = "textured"
    wireframe: bool = False
    in_place: bool = False
    bones: bool = True
    grid: bool = True
    hilite: Sequence[int] = ()
    only: str | None = None
    severed: bool = False
    """The stump: the tail tip hidden (`Scene.severed`)."""


def configure(vp: MonsterViewport, o: Options) -> None:
    """The flags onto a viewport whose scene is set; the window's startup shares it."""
    if vp.mesh is None:
        raise ValueError("no scene in the viewport")
    vp.show_ground = o.grid
    vp.show_skeleton = o.bones
    vp.wireframe = o.wireframe
    vp.strip_root = o.in_place
    if o.shading not in MODES:
        raise ValueError(f"shading is one of {', '.join(MODES)}, not {o.shading!r}")
    vp.mesh.mode = MODES.index(o.shading)
    vp.tag_joints(o.hilite)
    if o.only not in ONLY:
        raise ValueError(f"only is 'tagged' or 'rest', not {o.only!r}")
    vp.mesh.isolate = ONLY[o.only]


def clip_of(scene: Scene, vp: MonsterViewport, o: Options) -> Clip | None:
    if o.bind:
        return None
    if o.clip is None:
        return vp.clip
    try:
        return scene.clip(o.clip)
    except KeyError:
        have = ", ".join(f"{c.slot}" + (f" {c.name}" if c.names else "") for c in scene.clips)
        raise ValueError(f"no clip {o.clip!r} in {scene.name} (have: {have})") from None


def render(
    scene: Scene,
    o: Options,
    size: tuple[int, int] = (1280, 800),
    samples: int = 4,
    ctx: moderngl.Context | None = None,
) -> Shots:
    """`<clip>_f<frame>_<view>` per frame and view (`bind_<view>` for the bind pose), each framed
    on its own pose."""
    images: dict[str, Image8] = {}
    scene.severed = o.severed
    with offscreen(lambda c: MonsterViewport(c, size, samples), ctx) as vp:
        vp.set_scene(scene)
        configure(vp, o)
        clip = clip_of(scene, vp, o)
        if clip is None:
            frames: Sequence[float | None] = [None]
        elif o.frames is None:
            frames = [vp.frame if o.clip is None else clip.frames * 0.5]
        else:
            frames = list(o.frames)
        for f in frames:
            vp.set_pose(clip, 0.0 if f is None else f)
            name = "bind" if clip is None else f"{clip.name}_f{f:g}"
            for view in o.views:
                vp.camera.frame(vp.bounds()).look(view)
                vp.draw()
                images[f"{name}_{view}"] = vp.target.read()
        return Shots(images, describe(vp.ctx), vp.clear_color())
