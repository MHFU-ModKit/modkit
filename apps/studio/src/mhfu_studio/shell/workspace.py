# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""What a workspace gives the shell, and the registry workspaces add themselves to.

A workspace module `mhfu_studio.<area>.workspace` calls `register` at import; `discover`
imports every area's. Nothing here imports GL or a toolkit.

Two sets of hooks live here while the studio moves to Qt: the imgui ones (`panels`,
`layout`, `toolbar`, `input`, `overlay`) and the Qt ones (`docks`, `tool_groups`, `pointer`,
`key`, `paint`). A workspace implements one set; the imgui set goes when the last
workspace has moved.
"""

from __future__ import annotations

import enum
import importlib
import importlib.util
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from mhfu_studio.shell.document import Document
from mhfu_studio.shell.input import Key, Pointer
from mhfu_studio.shell.overlay import Overlay

if TYPE_CHECKING:
    import moderngl

    from mhfu_studio.shell.studio import Studio
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


@dataclass(frozen=True)
class Dock:
    """A dockable panel. `build` makes its widget once (a QWidget with a `sync()` that re-reads
    what it shows); the shell calls `sync()` after every change. `tip` says what it is for."""

    label: str
    area: Literal["left", "right", "bottom"]
    build: Callable[[Studio], Any]
    tip: str
    #: in front of the other docks tabbed with it when the workspace opens
    focus: bool = False


@dataclass(frozen=True)
class Tool:
    """One viewport mode; `key` is its single-key shortcut ("Q"), `icon` a qtawesome name."""

    id: str
    label: str
    key: str
    icon: str
    tip: str


@dataclass(frozen=True)
class ToolGroup:
    """Tools shown together above the viewport; one is on at a time unless `toggles`."""

    id: str
    label: str
    tools: tuple[Tool, ...]
    toggles: bool = False


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
    #: how often `message` was set: the studio shows each one, a repeat too
    said: int = 0
    _message: str = ""

    @property
    def message(self) -> str:
        """The last action's outcome or refusal, for the status bar."""
        return self._message

    @message.setter
    def message(self, text: str) -> None:
        self._message = text
        self.said += 1

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

    def panels(self) -> Sequence[Panel]:
        return ()

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

    # ---- the Qt shell ------------------------------------------------------------------- #
    def docks(self) -> Sequence[Dock]:
        return ()

    def tool_groups(self) -> Sequence[ToolGroup]:
        return ()

    def tool_on(self, group: str, tool: str) -> bool:
        """Whether `tool` of `group` is on, for the toolbar's checked state."""
        return False

    def set_tool(self, group: str, tool: str, on: bool = True) -> None:
        """Turns `tool` on (excluding the rest of its group unless the group `toggles`)."""

    def pointer(self, ev: Pointer) -> Gesture:
        """A mouse event over the viewport; returns the camera gestures it consumed."""
        return Gesture.NONE

    def key(self, ev: Key) -> bool:
        """A key over the viewport; True when it was used."""
        return False

    def paint(self, o: Overlay) -> None:
        """Drawn over the picture after every frame: gizmo, labels, a selection box."""

    def take_focus(self) -> str | None:
        """A dock label to bring to the front (after `reveal`, say); asked once per change."""
        return None


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
