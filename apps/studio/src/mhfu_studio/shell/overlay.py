# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""What a workspace draws over the viewport picture, without a toolkit.

Coordinates are the view's points (origin top left). A colour is an `Ink` role, which the
theme resolves, or an RGBA a document chose (a hitbox's own colour, say).
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from typing import Any, Protocol


class Ink(enum.Enum):
    AXIS_X = "axis_x"
    AXIS_Y = "axis_y"
    AXIS_Z = "axis_z"
    #: the gizmo's view-facing ring and uniform handle
    AXIS_VIEW = "axis_view"
    #: a handle under the pointer or being dragged
    HOT = "hot"
    SELECTION = "selection"
    HOVER = "hover"
    #: the rubber band of a box select
    BOX = "box"
    #: text over the picture (labels, the HUD)
    TEXT = "text"
    SHADOW = "shadow"
    WARNING = "warning"
    ERROR = "error"


RGBA = tuple[float, float, float, float]
Color = Ink | RGBA
Point = tuple[float, float]


class Overlay(Protocol):
    def line(self, a: Point, b: Point, color: Color, width: float = 1.0) -> None: ...

    def polyline(
        self, pts: Sequence[Point], color: Color, width: float = 1.0, closed: bool = False
    ) -> None: ...

    def polygon(self, pts: Sequence[Point], fill: Color) -> None: ...

    def rect(
        self, a: Point, b: Point, color: Color | None = None, fill: Color | None = None
    ) -> None: ...

    def circle(
        self,
        c: Point,
        r: float,
        color: Color | None = None,
        fill: Color | None = None,
        width: float = 1.0,
    ) -> None: ...

    def text(
        self, p: Point, s: str, color: Color = Ink.TEXT, size: float = 11.0, shadow: bool = True
    ) -> None:
        """`p` is the top left of the first line; `s` may hold several lines."""
        ...


class Recorder:
    """An `Overlay` that keeps every call as `(method, args...)`, for tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def line(self, a: Point, b: Point, color: Color, width: float = 1.0) -> None:
        self.calls.append(("line", a, b, color, width))

    def polyline(
        self, pts: Sequence[Point], color: Color, width: float = 1.0, closed: bool = False
    ) -> None:
        self.calls.append(("polyline", list(pts), color, width, closed))

    def polygon(self, pts: Sequence[Point], fill: Color) -> None:
        self.calls.append(("polygon", list(pts), fill))

    def rect(
        self, a: Point, b: Point, color: Color | None = None, fill: Color | None = None
    ) -> None:
        self.calls.append(("rect", a, b, color, fill))

    def circle(
        self,
        c: Point,
        r: float,
        color: Color | None = None,
        fill: Color | None = None,
        width: float = 1.0,
    ) -> None:
        self.calls.append(("circle", c, r, color, fill, width))

    def text(
        self, p: Point, s: str, color: Color = Ink.TEXT, size: float = 11.0, shadow: bool = True
    ) -> None:
        self.calls.append(("text", p, s, color, size, shadow))

    def texts(self) -> list[str]:
        return [c[2] for c in self.calls if c[0] == "text"]

    def kinds(self) -> list[str]:
        return [c[0] for c in self.calls]
