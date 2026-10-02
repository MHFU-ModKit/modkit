# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""What a workspace gives the shell, and the registry workspaces add themselves to.

A workspace module `mhfu_studio.<area>.workspace` calls `register` at import; `discover`
imports every area's. Nothing here imports GL or a toolkit.
"""

from __future__ import annotations

import enum
import importlib
import importlib.util
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from mhfu_studio.shell import places
from mhfu_studio.shell.document import Document
from mhfu_studio.shell.input import Key, Pointer
from mhfu_studio.shell.overlay import Overlay

if TYPE_CHECKING:
    import moderngl

    from mhfu_studio.shell.studio import Studio
    from mhfu_studio.shell.viewport import Viewport


@dataclass(frozen=True)
class Dock:
    """A dockable panel. `build` makes its widget once (a QWidget with a `sync()` that re-reads
    what it shows); the shell calls `sync()` after every change. `tip` says what it is for."""

    label: str
    area: Literal["left", "right", "bottom"]
    build: Callable[[Studio], Any]
    tip: str
    #: open in the default layout; the rest wait in View > Panels, tabbed at `area` when opened
    shown: bool = True
    #: never tabbed: the area's other docks tab together beside it
    alone: bool = False
    #: its starting width (left, right) or height (bottom) in points; 0 is the area's share
    size: int = 0


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


@dataclass(frozen=True)
class Shortcut:
    """A key the view acts on, for Help > Keyboard shortcuts; `keys` are Qt names ("Escape")."""

    keys: tuple[str, ...]
    does: str


@dataclass(frozen=True)
class Job:
    """A studio command the window runs in the background, its output going to the send log."""

    #: what it does, for the log and the status line: "push st139 into the game"
    title: str
    #: the command line after `studio`: ("map", "push", ...)
    argv: tuple[str, ...]
    stdin: bytes = b""


#: Send to game's key, as Qt names it
SEND_KEY = "Ctrl+Return"


@dataclass(frozen=True)
class Choice:
    """A start-page entry: `path` opens as a document, else `key` goes to `Workspace.choose`."""

    label: str
    tip: str
    key: str = ""
    path: Path | None = None
    #: small, after the label: an id, a folder
    detail: str = ""
    #: the heading it sits under in its shelf
    group: str = ""


@dataclass(frozen=True)
class Shelf:
    """A list on the start page."""

    title: str
    choices: tuple[Choice, ...] = ()
    #: said under the list, or in its place when it is empty
    note: str = ""
    #: a button after the list that asks for a document: (label, tip)
    browse: tuple[str, str] | None = None
    #: the places its choices read: they wait while one is missing
    needs: tuple[places.Place, ...] = ()


@dataclass(frozen=True)
class Step:
    """A step of the next-steps line; `key` as Qt names it ("W"), shown the system's way."""

    text: str
    done: bool = False
    key: str = ""


@dataclass(frozen=True)
class Warmup:
    """What `open` would do slowly, run off the GUI thread first: no GL, no change to the
    workspace. `done` takes `run`'s result back on the GUI thread, before `open`."""

    #: what it is doing, for the start page while it runs
    what: str
    run: Callable[[], object]
    done: Callable[[object], None] | None = None


class Gesture(enum.Flag):
    """Camera gestures a workspace's `pointer` hook consumed."""

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
    #: open-dialog filters as (name, pattern) pairs: ("Port manifest", "*.toml", ...)
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

    def status(self) -> str: ...

    def close(self) -> None:
        """Releases GL objects; the context goes away after this."""
        ...

    def frame(self, dt: float) -> None:
        """Once per frame before the viewport draws (advance playback, sync edits)."""

    def hud(self) -> str:
        """About the picture, at its top left: what is loaded, small."""
        return ""

    def hint(self) -> str:
        """The status line: what is selected and the keys that act on it, else how to select."""
        return ""

    def animating(self) -> bool:
        """True while the picture changes without input (the view keeps redrawing)."""
        return False

    def reveal(self, target: Hashable) -> None:
        """Shows a finding's `target` (select it, frame it)."""

    def refresh(self) -> None:
        """The shell changed the document (undo, redo, save as): re-read what depends on it."""

    def locate(self) -> None:
        """A place changed (`shell.places`): find the game files again."""

    def revert(self) -> None:
        """Drops the unsaved edits: the document as its file has it, opened again."""
        doc = self.document
        if doc is not None and doc.path is not None:
            self.open(doc.path)

    def send_blocker(self) -> str | None:
        """Why "Send to game" cannot run now, in words for its tooltip; None when it can."""
        return "nothing here goes to the game"

    def send(self) -> Job | None:
        """Sends the edits to the game: a `Job` for the window to run, or None when it is done
        already (the outcome in `message`). Called only while `send_blocker()` is None."""
        return None

    def ended(self, job: Job, ok: bool) -> None:
        """A job this workspace started has ended; `ok` when it exited 0 unstopped."""

    # ---- the start page ----------------------------------------------------------------- #
    def shown(self) -> Hashable | None:
        """What the view shows, compared between changes; None while there is nothing worth
        showing, when the window shows the start page in its place."""
        return self.name

    def start(self) -> Sequence[Shelf]:
        """The start page's lists: what to open here."""
        return ()

    def choose(self, key: str) -> None:
        """Opens what a `Choice.key` names, keeping the document's edits; raises with a
        message."""
        raise ValueError(f"nothing here is {key!r}")

    def warmup(self, path: Path) -> Warmup | None:
        """The slow part of `open(path)`, for the window to run in the background first."""
        return None

    def next_steps(self) -> Sequence[Step]:
        """The task on screen in steps, each ticked once the workspace's state shows it done."""
        return ()

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

    def shortcuts(self) -> Sequence[Shortcut]:
        """The keys `key` acts on."""
        return ()

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
