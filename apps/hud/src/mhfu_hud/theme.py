"""Colours, fonts and the canvas size.

Layouts draw on a fixed canvas, twice the PSP's 480x272, that the window scales to fit, so they
use absolute coordinates.
"""

from __future__ import annotations

import pygame

CANVAS_W = 960
CANVAS_H = 544
STATUS_H = 18
"""The window's status row above the canvas."""

Color = tuple[int, int, int]


class C:
    """Palette: dark slate panels, warm amber accents."""

    BG: Color = (10, 12, 16)
    BG_QUEST: Color = (16, 18, 22)
    PANEL: Color = (26, 30, 38)
    PANEL_HI: Color = (34, 39, 49)
    PANEL_BORDER: Color = (62, 70, 86)
    TEXT: Color = (230, 232, 237)
    TEXT_DIM: Color = (132, 140, 154)
    TEXT_FAINT: Color = (84, 90, 102)
    ACCENT: Color = (235, 196, 92)
    HP_HIGH: Color = (96, 200, 104)
    HP_MID: Color = (228, 192, 80)
    HP_LOW: Color = (224, 78, 66)
    HP_RECOV: Color = (212, 96, 88)  # the recoverable red bar behind the green
    STAMINA: Color = (236, 214, 110)
    BAR_BACK: Color = (38, 42, 50)
    MONSTER: Color = (224, 96, 88)
    MONSTER_DIM: Color = (120, 64, 62)
    PLAYER: Color = (96, 184, 236)
    SELECT: Color = (255, 222, 120)
    OK: Color = (110, 200, 120)
    WARN: Color = (228, 168, 72)
    ERR: Color = (226, 86, 78)
    PLACEHOLDER: Color = (96, 102, 116)
    SHADOW: Color = (0, 0, 0)


_MONO_CANDIDATES = "menlo,monaco,dejavusansmono,consolas,couriernew,monospace"
_fonts: dict[tuple[int, bool], pygame.font.Font] = {}


def font(size: int, bold: bool = False) -> pygame.font.Font:
    """A cached monospace font."""
    key = (size, bold)
    if key not in _fonts:
        try:
            _fonts[key] = pygame.font.SysFont(_MONO_CANDIDATES, size, bold=bold)
        except Exception:
            _fonts[key] = pygame.font.Font(None, size)
    return _fonts[key]


def hp_color(fraction: float) -> Color:
    """Bar colour by the HP fraction left."""
    if fraction <= 0.2:
        return C.HP_LOW
    if fraction <= 0.5:
        return C.HP_MID
    return C.HP_HIGH
