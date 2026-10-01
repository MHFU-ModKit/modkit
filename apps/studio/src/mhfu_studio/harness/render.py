# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A viewport drawn offscreen, and contact sheets of several renders."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import TypeVar

import moderngl
import numpy as np
import numpy.typing as npt
from PIL import Image, ImageDraw

from mhfu_studio.shell.context import describe, standalone
from mhfu_studio.shell.target import RGBA
from mhfu_studio.shell.viewport import Viewport

V = TypeVar("V", bound=Viewport)
Image8 = npt.NDArray[np.uint8]


@dataclass(frozen=True)
class Shots:
    """Named renders, the renderer that made them and the clear colour under them."""

    images: dict[str, Image8]
    renderer: str
    clear: RGBA


@contextmanager
def offscreen(
    make: Callable[[moderngl.Context], V], ctx: moderngl.Context | None = None
) -> Iterator[V]:
    """A viewport from `make(ctx)` on `ctx`, or on a standalone context made for it."""
    with ExitStack() as stack:
        if ctx is None:
            ctx = stack.enter_context(standalone())
        vp = make(ctx)
        stack.callback(vp.release)
        yield vp


def views(vp: Viewport, names: Sequence[str]) -> Shots:
    """One frame per named camera view."""
    out: dict[str, Image8] = {}
    for name in names:
        vp.camera.look(name)
        vp.draw()
        out[name] = vp.target.read()
    return Shots(out, describe(vp.ctx), vp.clear_color())


def contact_sheet(
    tiles: Sequence[Image8],
    labels: Sequence[str] = (),
    columns: int = 4,
    pad: int = 4,
    background: tuple[int, int, int, int] = (24, 26, 30, 255),
) -> Image8:
    """Tiles in a grid, row-major, each with its label at the top left."""
    if not tiles:
        raise ValueError("a contact sheet needs at least one tile")
    th = max(t.shape[0] for t in tiles)
    tw = max(t.shape[1] for t in tiles)
    columns = max(1, min(columns, len(tiles)))
    rows = -(-len(tiles) // columns)
    sheet = Image.new("RGBA", (pad + columns * (tw + pad), pad + rows * (th + pad)), background)
    draw = ImageDraw.Draw(sheet)
    for i, tile in enumerate(tiles):
        x = pad + (i % columns) * (tw + pad)
        y = pad + (i // columns) * (th + pad)
        sheet.paste(Image.fromarray(np.ascontiguousarray(tile, dtype=np.uint8)), (x, y))
        if i < len(labels):
            draw.text((x + 5, y + 5), labels[i], fill=(0, 0, 0, 255))
            draw.text((x + 4, y + 4), labels[i], fill=(235, 238, 245, 255))
    return np.asarray(sheet, dtype=np.uint8).copy()
