# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The offscreen framebuffer every viewport draws into, window or not.

The window blits it and a headless render reads it back, so both are the same pixels. A
multisample buffer cannot be read, so `read` resolves into a plain texture first. GL's row 0 is
the bottom: `read` flips.
"""

from __future__ import annotations

from pathlib import Path
from types import TracebackType

import moderngl
import numpy as np
import numpy.typing as npt
from PIL import Image

from mhfu_studio.shell.context import max_samples

SAMPLES = 4
RGBA = tuple[float, float, float, float]
CLEAR: RGBA = (0.10, 0.11, 0.13, 1.0)


class Target:
    """A colour+depth framebuffer, resizable, readable as RGBA8 with row 0 at the top."""

    def __init__(self, ctx: moderngl.Context, size: tuple[int, int], samples: int = SAMPLES):
        self.ctx = ctx
        self.samples = max_samples(ctx, samples)
        self.size = (0, 0)
        self._fbo: moderngl.Framebuffer | None = None
        self._resolve: moderngl.Framebuffer | None = None
        self._tex: moderngl.Texture | None = None
        self._depth: moderngl.Renderbuffer | None = None
        self._color: moderngl.Renderbuffer | None = None
        self.resize(size)

    def resize(self, size: tuple[int, int]) -> bool:
        """Rebuilds at `size` (at least 1x1) if it changed; returns whether it did."""
        w, h = max(1, int(size[0])), max(1, int(size[1]))
        if (w, h) == self.size:
            return False
        self.release()
        self.size = (w, h)
        ctx = self.ctx
        self._tex = ctx.texture((w, h), 4)
        self._tex.repeat_x = self._tex.repeat_y = False
        self._resolve = ctx.framebuffer(color_attachments=[self._tex])
        self._depth = ctx.depth_renderbuffer((w, h), samples=self.samples)
        if self.samples:
            self._color = ctx.renderbuffer((w, h), 4, samples=self.samples)
            self._fbo = ctx.framebuffer([self._color], self._depth)
        else:
            self._fbo = ctx.framebuffer([self._tex], self._depth)
        return True

    def release(self) -> None:
        for obj in (self._fbo, self._resolve, self._color, self._depth, self._tex):
            if obj is not None:
                obj.release()
        self._fbo = self._resolve = self._tex = None
        self._color = self._depth = None
        self.size = (0, 0)

    def __enter__(self) -> Target:
        return self

    def __exit__(
        self, t: type[BaseException] | None, e: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.release()

    @property
    def aspect(self) -> float:
        return self.size[0] / float(self.size[1])

    def clear(self, rgba: RGBA = CLEAR) -> None:
        self._framebuffer.clear(*rgba, depth=1.0)

    def use(self) -> None:
        """Binds it and sets the viewport to its full extent."""
        self._framebuffer.use()
        self.ctx.viewport = (0, 0, self.size[0], self.size[1])

    def resolve(self) -> None:
        """Collapses the multisample buffer into the readable texture; no-op without MSAA."""
        if self.samples and self._resolve is not None:
            self.ctx.copy_framebuffer(self._resolve, self._framebuffer)

    @property
    def texture(self) -> moderngl.Texture:
        """The single-sample colour texture; `resolve` first."""
        if self._tex is None:
            raise RuntimeError("the target was released")
        return self._tex

    @property
    def resolved(self) -> moderngl.Framebuffer:
        """The single-sample framebuffer around `texture`; `resolve` first. A window blits it."""
        if self._resolve is None:
            raise RuntimeError("the target was released")
        return self._resolve

    def read(self, *, flip: bool = True) -> npt.NDArray[np.uint8]:
        """`(h, w, 4)` uint8, row 0 at the top."""
        self.resolve()
        if self._resolve is None:
            raise RuntimeError("the target was released")
        raw = self._resolve.read(components=4, alignment=1)
        w, h = self.size
        img = np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 4)
        return img[::-1].copy() if flip else img.copy()

    def save(self, path: Path | str) -> Path:
        return write_png(path, self.read())

    @property
    def _framebuffer(self) -> moderngl.Framebuffer:
        if self._fbo is None:
            raise RuntimeError("the target was released")
        return self._fbo

    def __repr__(self) -> str:
        return f"<Target {self.size[0]}x{self.size[1]} msaa={self.samples}x>"


def write_png(path: Path | str, rgba: npt.NDArray[np.uint8]) -> Path:
    """Writes `(h, w, 4)` uint8 as PNG, creating the directory."""
    path = Path(path)
    if rgba.ndim != 3 or rgba.shape[2] != 4:
        raise ValueError(f"write_png wants (h, w, 4) uint8, got {rgba.shape}")
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.ascontiguousarray(rgba, dtype=np.uint8)).save(path)
    return path
