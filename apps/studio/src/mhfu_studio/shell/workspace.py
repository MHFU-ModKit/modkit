# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""What a workspace gives the shell, and the registry workspaces add themselves to.

A workspace module `mhfu_studio.<area>.workspace` calls `register` at import; `discover`
imports every area's. Nothing here imports GL or imgui.
"""

from __future__ import annotations

import enum
import importlib
import importlib.util
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from mhfu_studio.shell.document import Document

if TYPE_CHECKING:
    import moderngl

    from mhfu_studio.shell.viewport import Viewport

#: the dock space the shell's viewport window lives in; a workspace's splits start from it
MAIN = "MainDockSpace"


@dataclass(frozen=True)
class Split:
    """A dock space `name` cut from `parent` on `side`, taking `ratio` of it."""

    parent: str
    name: str
    side: Literal["left", "right", "up", "down"]
    ratio: float


@dataclass(frozen=True)
class Panel:
    """A dockable window; `scroll=False` for panels that fill themselves (a graph, a canvas)."""

    label: str
    dock: str
    draw: Callable[[], None]
    focus: bool = False
    scroll: bool = True


@dataclass(frozen=True)
class View:
    """Where the viewport image is this frame, in imgui points, and what the mouse does on it."""

    origin: tuple[float, float]
    size: tuple[int, int]
    hovered: bool
    #: the capture button is held (true from press to release, wherever the pointer goes)
    active: bool
    #: framebuffer pixels per point
    scale: float = 1.0

    def mouse(self, x: float, y: float) -> tuple[float, float]:
        """A screen position relative to the image."""
        return x - self.origin[0], y - self.origin[1]


class Gesture(enum.Flag):
    """Camera gestures a workspace's input hook consumed this frame."""

    NONE = 0
    ORBIT = enum.auto()
    PAN = enum.auto()
    DOLLY = enum.auto()
    ALL = ORBIT | PAN | DOLLY


class Workspace(Protocol):
    """One kind of document and its tools, docked around the shell's viewport.

    Subclass it to inherit the optional hooks; the methods without a body are required.
    """

    #: unique; the layout name and the menu entry
    name: str
    #: open-dialog filters as portable-file-dialogs pairs: ("Port manifest", "*.toml", ...)
    filters: Sequence[str] = ("All files", "*")

    @property
    def document(self) -> Document | None: ...

    @property
    def viewport(self) -> Viewport | None:
        """None until `setup`."""
        ...

    def can_open(self, path: Path) -> bool: ...

    def open(self, path: Path) -> None:
        """Opens `path` as the document; raises with a message the status bar can show."""
        ...

    def setup(self, ctx: moderngl.Context) -> Viewport:
        """Builds the viewport inside the first frame, when the window's context is current."""
        ...

    def panels(self) -> Sequence[Panel]: ...

    def status(self) -> str: ...

    def close(self) -> None:
        """Releases GL objects; the context goes away after this."""
        ...

    def layout(self) -> Sequence[Split]:
        """Docking splits; the viewport keeps `MAIN`, the shell's Findings go to "Bottom"."""
        return (
            Split(MAIN, "Left", "left", 0.2),
            Split(MAIN, "Right", "right", 0.24),
            Split(MAIN, "Bottom", "down", 0.22),
        )

    def frame(self, dt: float) -> None:
        """Once per frame before the viewport draws (advance playback, sync edits)."""

    def toolbar(self) -> None:
        """Drawn above the viewport image, inside the viewport window."""

    def wants_mouse(self) -> bool:
        """True when an overlay (a gizmo) owns the mouse; the shell then lays no capture."""
        return False

    def input(self, view: View) -> Gesture:
        """Viewport mouse and keys before the camera; returns the gestures it consumed."""
        return Gesture.NONE

    def overlay(self, view: View) -> None:
        """Drawn over the viewport image: gizmos, labels, a selection box."""

    def hud(self) -> str:
        """Lines at the image's top left, above the camera line."""
        return ""

    def animating(self) -> bool:
        """True while the picture changes without input (holds the fps idling off)."""
        return False

    def reveal(self, target: Hashable) -> None:
        """Shows a finding's `target` (select it, frame it)."""

    def refresh(self) -> None:
        """The shell changed the document (undo, redo, save as): re-read what depends on it."""


Factory = Callable[[], Workspace]
_registry: dict[str, Factory] = {}


def register(name: str, factory: Factory) -> None:
    """Adds a workspace; the last registration of a name wins."""
    _registry[name] = factory


def registered() -> dict[str, Factory]:
    return dict(_registry)


def unregister(name: str) -> None:
    _registry.pop(name, None)


def discover(areas: Sequence[str]) -> dict[str, Factory]:
    """Imports `mhfu_studio.<area>.workspace` where one exists; returns the registry."""
    for area in areas:
        module = f"mhfu_studio.{area}.workspace"
        if importlib.util.find_spec(module) is not None:
            importlib.import_module(module)
    return registered()


def pick(workspaces: Sequence[Workspace], path: Path) -> Workspace | None:
    """The first workspace that can open `path`."""
    return next((w for w in workspaces if w.can_open(path)), None)
