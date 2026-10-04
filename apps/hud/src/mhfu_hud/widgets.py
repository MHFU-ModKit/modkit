# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Low-level pygame drawing helpers shared by the layouts."""

from __future__ import annotations

import math
from collections.abc import Iterable

import pygame

from .theme import C, Color, font

Point = tuple[float, float]
RectLike = pygame.Rect | tuple[float, float, float, float]
_ANCHOR = {"left": "topleft", "center": "midtop", "right": "topright"}


def text(
    surface: pygame.Surface,
    value: object,
    pos: Point,
    size: int = 18,
    color: Color = C.TEXT,
    bold: bool = False,
    align: str = "left",
    shadow: bool = True,
) -> pygame.Rect:
    """Draw `value` with its top `align`ed (left, center, right) at `pos`; its rect."""
    f = font(size, bold)
    img = f.render(str(value), True, color)
    rect = img.get_rect()
    setattr(rect, _ANCHOR[align], pos)
    if shadow:
        surface.blit(f.render(str(value), True, C.SHADOW), (rect.x + 1, rect.y + 1))
    surface.blit(img, rect)
    return rect


def panel(
    surface: pygame.Surface,
    rect: RectLike,
    title: str | None = None,
    fill: Color = C.PANEL,
    border: Color = C.PANEL_BORDER,
) -> pygame.Rect:
    """A rounded panel with an optional title."""
    rect = pygame.Rect(rect)
    pygame.draw.rect(surface, fill, rect, border_radius=6)
    pygame.draw.rect(surface, border, rect, width=1, border_radius=6)
    if title:
        text(surface, title, (rect.x + 10, rect.y + 6), size=13, color=C.ACCENT, bold=True)
    return rect


def bar(
    surface: pygame.Surface,
    rect: RectLike,
    fraction: float,
    fill: Color,
    back: Color = C.BAR_BACK,
    border: Color = C.PANEL_BORDER,
) -> None:
    """A horizontal value bar; `fraction` is clamped to 0..1."""
    rect = pygame.Rect(rect)
    fraction = max(0.0, min(1.0, fraction))
    pygame.draw.rect(surface, back, rect, border_radius=3)
    if fraction > 0:
        fw = max(2, int(rect.width * fraction))
        pygame.draw.rect(surface, fill, (rect.x, rect.y, fw, rect.height), border_radius=3)
    pygame.draw.rect(surface, border, rect, width=1, border_radius=3)


def hp_bar(
    surface: pygame.Surface,
    rect: RectLike,
    current_frac: float,
    recov_frac: float,
    current_color: Color,
    recov_color: Color = C.HP_RECOV,
    back: Color = C.BAR_BACK,
    border: Color = C.PANEL_BORDER,
) -> None:
    """The game's HP bar: the recoverable red ceiling behind the current green."""
    rect = pygame.Rect(rect)
    current_frac = max(0.0, min(1.0, current_frac))
    recov_frac = max(current_frac, min(1.0, recov_frac))
    pygame.draw.rect(surface, back, rect, border_radius=3)
    for frac, color in ((recov_frac, recov_color), (current_frac, current_color)):
        if frac > 0:
            w = max(2, int(rect.width * frac))
            pygame.draw.rect(surface, color, (rect.x, rect.y, w, rect.height), border_radius=3)
    pygame.draw.rect(surface, border, rect, width=1, border_radius=3)


def stat_block(
    surface: pygame.Surface,
    pos: Point,
    label: str,
    value: object,
    size_label: int = 12,
    size_value: int = 24,
    color: Color = C.TEXT,
    placeholder: bool = False,
) -> int:
    """A label over a value; the bottom y."""
    x, y = pos
    text(surface, label, (x, y), size=size_label, color=C.TEXT_DIM, bold=True)
    vcolor = C.PLACEHOLDER if placeholder else color
    r = text(surface, value, (x, y + size_label + 2), size=size_value, color=vcolor, bold=True)
    return r.bottom


def kv_rows(
    surface: pygame.Surface,
    pos: Point,
    rows: Iterable[tuple[str, object]],
    size: int = 14,
    line_h: int = 20,
    key_color: Color = C.TEXT_DIM,
    val_color: Color = C.TEXT,
    key_w: int = 120,
) -> float:
    """(key, value) rows in two aligned columns; the bottom y."""
    x, y = pos
    for key, value in rows:
        text(surface, key, (x, y), size=size, color=key_color)
        text(surface, value, (x + key_w, y), size=size, color=val_color)
        y += line_h
    return y


def marker(
    surface: pygame.Surface,
    center: Point,
    heading_rad: float | None,
    color: Color,
    radius: float = 10,
    selected: bool = False,
) -> None:
    """A triangle pointing along `heading_rad` (0 = up); a dot without a heading."""
    cx, cy = center
    if selected:
        pygame.draw.circle(surface, C.SELECT, (int(cx), int(cy)), radius + 6, width=2)
    if heading_rad is None:
        pygame.draw.circle(surface, color, (int(cx), int(cy)), radius)
        pygame.draw.circle(surface, C.SHADOW, (int(cx), int(cy)), radius, width=1)
        return
    pts = [
        (cx + math.sin(heading_rad + off) * length, cy - math.cos(heading_rad + off) * length)
        for off, length in ((0, radius * 1.6), (2.4, radius), (-2.4, radius))
    ]
    pygame.draw.polygon(surface, color, pts)
    pygame.draw.polygon(surface, C.SHADOW, pts, width=1)


def fit_scale(src_size: tuple[int, int], box_size: tuple[int, int]) -> float:
    """The largest scale that fits `src_size` in `box_size`, keeping the aspect."""
    sw, sh = src_size
    bw, bh = box_size
    if sw <= 0 or sh <= 0:
        return 1.0
    return min(bw / sw, bh / sh)


def placeholder_box(surface: pygame.Surface, rect: RectLike, label: str = "-") -> None:
    """A box standing in for data that is not there."""
    rect = pygame.Rect(rect)
    pygame.draw.rect(surface, C.PANEL, rect, border_radius=4)
    pygame.draw.rect(surface, C.PLACEHOLDER, rect, width=1, border_radius=4)
    text(surface, label, rect.center, size=12, color=C.PLACEHOLDER, align="center")
