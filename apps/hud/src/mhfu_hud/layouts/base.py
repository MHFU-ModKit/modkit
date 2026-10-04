# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Layout base class and what a layout may ask of the reader and the writer.

A layout draws one tab or game context on the fixed canvas, keeps its own UI state (which
monster is selected) and may take key presses. Neither protocol has a debugger client: a layout
draws the snapshot and the writer's status, and hands edits to the writer.
"""

from __future__ import annotations

from typing import Protocol

import pygame

from ..assets import AssetLibrary
from ..calibration import Calibration
from ..edits import Edit, WriterStatus
from ..state import GameSnapshot
from ..theme import C


class Reader(Protocol):
    """What the window and the layouts need of the reader."""

    @property
    def snapshot(self) -> GameSnapshot: ...

    def set_section_override(self, section: int | None) -> None: ...

    def reset_section_tracking(self) -> None: ...


class Writer(Protocol):
    """What the window and QUEST_PREP need of the writer (`writer.GameWriter`)."""

    @property
    def status(self) -> WriterStatus: ...

    def stage(self, edit: Edit) -> None: ...

    def change(self, index: int, edit: Edit | None) -> None:
        """Replace edit `index`, or remove it with None."""

    def toggle(self) -> None:
        """Flip the master switch."""


class Layout:
    name = "base"

    def __init__(self, assets: AssetLibrary, calib: Calibration) -> None:
        self.assets = assets
        self.calib = calib

    def handle_key(self, key: int, snapshot: GameSnapshot) -> bool:
        """True when the key was taken."""
        return False

    def handle_click(self, canvas_pos: tuple[float, float], snapshot: GameSnapshot) -> bool:
        """A left click in canvas coordinates; True when taken."""
        return False

    def handle_motion(self, canvas_pos: tuple[float, float], snapshot: GameSnapshot) -> bool:
        """Mouse motion in canvas coordinates, for hover; never taken."""
        return False

    def render(self, surface: pygame.Surface, snapshot: GameSnapshot) -> None:
        raise NotImplementedError

    @staticmethod
    def blit_cover(
        surface: pygame.Surface, image: pygame.Surface | None, rect: pygame.Rect
    ) -> None:
        """`image` scaled to cover `rect`, cropped and centred."""
        if image is None:
            pygame.draw.rect(surface, C.PANEL, rect)
            return
        iw, ih = image.get_size()
        scale = max(rect.width / iw, rect.height / ih)
        scaled = pygame.transform.smoothscale(image, (int(iw * scale) + 1, int(ih * scale) + 1))
        prev = surface.get_clip()
        surface.set_clip(rect)
        surface.blit(scaled, scaled.get_rect(center=rect.center))
        surface.set_clip(prev)

    @staticmethod
    def dim(surface: pygame.Surface, rect: pygame.Rect, alpha: int = 110) -> None:
        veil = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
        veil.fill((0, 0, 0, alpha))
        surface.blit(veil, rect)
