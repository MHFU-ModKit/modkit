# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Viewport input as plain data, so workspaces and their tests never see a toolkit event."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Literal


class Button(enum.Flag):
    NONE = 0
    LEFT = enum.auto()
    RIGHT = enum.auto()
    MIDDLE = enum.auto()


class Mod(enum.Flag):
    """CTRL is the platform's command key: Cmd on macOS."""

    NONE = 0
    SHIFT = enum.auto()
    CTRL = enum.auto()
    ALT = enum.auto()


@dataclass(frozen=True)
class Pointer:
    """One mouse event over the viewport, in its points (origin top left).

    A workspace returns the camera gestures an event consumed; what a press consumes stays
    consumed until that button's release, so a drag that started on a handle never orbits.
    """

    kind: Literal["press", "move", "release", "wheel", "leave"]
    x: float
    y: float
    #: the size of the view in points
    size: tuple[int, int]
    #: the button that changed (press, release); NONE otherwise
    button: Button = Button.NONE
    #: the buttons held after this event
    buttons: Button = Button.NONE
    mods: Mod = Mod.NONE
    #: wheel notches, positive away from the user
    wheel: float = 0.0
    #: a double click (press only)
    double: bool = False

    @property
    def pos(self) -> tuple[float, float]:
        return self.x, self.y


@dataclass(frozen=True)
class Key:
    """A key press while the viewport has focus; `name` is "Escape", "Delete", "F", "1", ..."""

    name: str
    mods: Mod = Mod.NONE
