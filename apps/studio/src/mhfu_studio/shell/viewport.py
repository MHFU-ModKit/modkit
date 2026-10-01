# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""One frame of a 3D view, shared by the window and every headless render.

A workspace subclasses `Viewport` and draws its scene in `draw_scene`; the window shows
`target.texture`, a render reads `target`. There is no second renderer.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import TracebackType

import moderngl

from mhfu_studio.shell.camera import Bounds, Lens, Mat, OrbitCamera
from mhfu_studio.shell.target import CLEAR, RGBA, SAMPLES, Target


class Viewport:
    """Camera and target; face culling stays off (game meshes are two-sided)."""

    def __init__(
        self,
        ctx: moderngl.Context,
        size: tuple[int, int] = (1280, 800),
        samples: int = SAMPLES,
        lens: Lens | None = None,
    ) -> None:
        self.ctx = ctx
        self.target = Target(ctx, size, samples)
        self.camera = OrbitCamera(lens)
        self.background: RGBA = CLEAR

    def draw_scene(self, mvp: Mat) -> None:
        """Draws the scene into the bound target; depth test and alpha blending are on."""
        raise NotImplementedError

    def clear_color(self) -> RGBA:
        return self.background

    def bounds(self) -> Bounds:
        """What `frame_all` frames."""
        return Bounds.of([(0.0, 0.0, 0.0)])

    def frame_all(self) -> None:
        self.camera.frame(self.bounds())

    def resize(self, size: tuple[int, int]) -> bool:
        return self.target.resize(size)

    def draw(self) -> moderngl.Texture:
        """Renders one frame into `target` and returns its resolved texture.

        Restores the framebuffer bound on entry: left bound, imgui draws the whole UI into it
        and the window stays black while every offscreen check passes.
        """
        ctx, t = self.ctx, self.target
        prev = bound_framebuffer(ctx)
        try:
            t.use()
            t.clear(self.clear_color())
            ctx.enable(ctx.DEPTH_TEST | ctx.BLEND | ctx.PROGRAM_POINT_SIZE)
            ctx.blend_func = ctx.SRC_ALPHA, ctx.ONE_MINUS_SRC_ALPHA
            self.draw_scene(self.camera.mvp(t.aspect))
            t.resolve()
        finally:
            if prev is not None:
                prev.use()  # also restores the GL viewport rectangle
        return t.texture

    def release(self) -> None:
        """Frees GL objects; subclasses release theirs, then call this."""
        self.target.release()

    def __enter__(self) -> Viewport:
        return self

    def __exit__(
        self, t: type[BaseException] | None, e: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.release()


def bound_framebuffer(ctx: moderngl.Context) -> moderngl.Framebuffer | None:
    """The framebuffer to restore after drawing, or None.

    `ctx.fbo` can be a released framebuffer (its handle an `InvalidObject`), whose `use()`
    raises; a standalone context has no screen, so nothing needs restoring there.
    """
    fbo = ctx.fbo
    if fbo is not None and not isinstance(getattr(fbo, "mglo", None), moderngl.InvalidObject):
        return fbo
    return ctx.screen


@contextmanager
def depth_write_off(ctx: moderngl.Context) -> Iterator[None]:
    """Draws inside test depth without writing it: translucent layers, overlays."""
    ctx.depth_mask = False  # type: ignore[attr-defined]  # missing from moderngl's stubs
    try:
        yield
    finally:
        ctx.depth_mask = True  # type: ignore[attr-defined]
