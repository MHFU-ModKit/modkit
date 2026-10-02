# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Test doubles for the Qt window: a workspace on the Qt hooks, a GL check, screenshots."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QPainter
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QWidget

from mhfu_studio.shell import testing
from mhfu_studio.shell.input import Key, Pointer
from mhfu_studio.shell.overlay import Ink, Overlay
from mhfu_studio.shell.workspace import Dock, Gesture, Tool, ToolGroup
from mhfu_studio.ui import kit

if TYPE_CHECKING:
    import moderngl

    from mhfu_studio.shell.studio import Studio
    from mhfu_studio.shell.viewport import Viewport

TOOLS = ToolGroup(
    "tool",
    "Tool",
    (
        Tool("select", "Select", "Q", "ph.cursor", "Click an item to pick it"),
        Tool("move", "Move", "W", "ph.arrows-out-cardinal", "Drag to move the picked item"),
    ),
)
VIEW = ToolGroup(
    "view", "View", (Tool("snap", "Snap", "S", "ph.magnet", "Moves go in whole steps"),), True
)


class ItemsPanel(kit.Panel):
    """The document's items and a button that adds one."""

    def __init__(self, ws: FakeWorkspace, studio: Studio) -> None:
        super().__init__()
        self.ws = ws
        self.syncs = 0
        self.list = kit.Items(tip="The document's items", empty="No items")
        self.add = kit.button(
            "Add", tip="Adds an item to the document", on=studio.act("add item", ws.add_item)
        )
        self.body.addWidget(self.list)
        self.body.addWidget(self.add)

    def sync(self) -> None:
        self.syncs += 1
        doc = self.ws.doc
        self.list.set_items([kit.Item(s) for s in ([] if doc is None else doc.history.value)])


class NotesPanel(kit.Panel):
    """A text field and a number, to type into."""

    def __init__(self) -> None:
        super().__init__()
        self.syncs = 0
        self.field = kit.text_field(tip="Type a note")
        self.number = kit.number(tip="Type a number")
        self.body.addWidget(self.field)
        self.body.addWidget(self.number)
        self.body.addStretch(1)

    def sync(self) -> None:
        self.syncs += 1


class FakeWorkspace(testing.FakeWorkspace):
    """`shell.testing`'s fake on the Qt hooks: two docks, a tool group and a toggle group,
    and every `Pointer` and `Key` it got."""

    def __init__(
        self,
        name: str = "fake",
        suffix: str = ".txt",
        make_viewport: Callable[[moderngl.Context], Viewport] | None = None,
    ) -> None:
        super().__init__(name, suffix, make_viewport)
        self.tool = "select"
        self.snap = False
        self.pointers: list[Pointer] = []
        self.keys: list[Key] = []
        #: the gestures every press consumes
        self.consume = Gesture.NONE
        #: the dock label `take_focus` hands out once
        self.focus: str | None = None
        self.built: dict[str, kit.Panel] = {}

    def add_item(self) -> None:
        if self.doc is None:
            self.doc = testing.FakeDocument()
        self.doc.edit(f"item{len(self.doc.history.value)}")

    def docks(self) -> Sequence[Dock]:
        return (
            Dock("Items", "left", self._make_items, tip="The document's items, one per row"),
            Dock("Notes", "right", self._make_notes, tip="Fields to type into", focus=True),
        )

    def _make_items(self, studio: Studio) -> QWidget:
        self.built["Items"] = p = ItemsPanel(self, studio)
        return p

    def _make_notes(self, studio: Studio) -> QWidget:
        self.built["Notes"] = p = NotesPanel()
        return p

    def tool_groups(self) -> Sequence[ToolGroup]:
        return (TOOLS, VIEW)

    def tool_on(self, group: str, tool: str) -> bool:
        return self.snap if group == VIEW.id else self.tool == tool

    def set_tool(self, group: str, tool: str, on: bool = True) -> None:
        if group == VIEW.id:
            self.snap = on
        else:
            self.tool = tool

    def pointer(self, ev: Pointer) -> Gesture:
        self.pointers.append(ev)
        return self.consume if ev.kind == "press" else Gesture.NONE

    def key(self, ev: Key) -> bool:
        self.keys.append(ev)
        return ev.name == "Delete"

    def paint(self, o: Overlay) -> None:
        o.line((20.0, 90.0), (120.0, 90.0), Ink.SELECTION, 3.0)
        o.text((20.0, 100.0), f"{self.name} overlay")

    def hud(self) -> str:
        return f"{self.name}: {self.status()}"

    def take_focus(self) -> str | None:
        label, self.focus = self.focus, None
        return label


#: platforms whose windows have no GL (offscreen only with QT_QPA_OFFSCREEN_NO_GLX on Linux)
NO_GL = ("offscreen", "minimal")


def gl_or_skip() -> None:
    """Skips the calling test where Qt cannot make a GL 3.3 context."""
    import pytest
    from PySide6.QtGui import QGuiApplication, QOffscreenSurface, QOpenGLContext

    from mhfu_studio.ui.app import surface_format

    if QGuiApplication.platformName() in NO_GL:
        pytest.skip(f"the {QGuiApplication.platformName()} platform has no GL")
    ctx, surface = QOpenGLContext(), QOffscreenSurface()
    ctx.setFormat(surface_format())
    surface.setFormat(surface_format())
    surface.create()
    if not ctx.create() or not ctx.makeCurrent(surface):
        pytest.skip("Qt could not make a GL context here")
    ok = ctx.format().version() >= (3, 3)
    ctx.doneCurrent()
    if not ok:
        pytest.skip(f"GL {ctx.format().version()} is older than 3.3")


@contextmanager
def elsewhere() -> Iterator[None]:
    """Another GL context current inside, in the window's share group, as Qt's own is once it
    has composed the window: what an action meets when a click runs it."""
    from PySide6.QtGui import QOffscreenSurface, QOpenGLContext

    from mhfu_studio.ui.app import surface_format

    ctx, surface = QOpenGLContext(), QOffscreenSurface()
    ctx.setFormat(surface_format())
    ctx.setShareContext(QOpenGLContext.globalShareContext())
    surface.setFormat(surface_format())
    surface.create()
    if not (ctx.create() and ctx.makeCurrent(surface)):
        raise RuntimeError("no second GL context")
    try:
        yield
    finally:
        ctx.doneCurrent()


def no_gl_or_skip() -> None:
    """Skips the calling test where the view has GL: it would draw a workspace set up on the
    test's headless context from its own context, which does not own those objects."""
    import pytest
    from PySide6.QtGui import QGuiApplication

    if QGuiApplication.platformName() not in NO_GL:
        pytest.skip(f"the {QGuiApplication.platformName()} view draws with its own GL context")


def shot(widget: QWidget, path: Path | str) -> Path:
    """A PNG of `widget` as it shows on screen.

    `grab` redirects a GL view's QPainter into the picture and then covers it with the bare
    framebuffer, so each view's own grab goes on top, inside its rounded mask.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img = widget.grab()
    p = QPainter(img)
    for gl in widget.findChildren(QOpenGLWidget):
        if gl.isVisible():
            at = gl.mapTo(widget, QPoint(0, 0))
            if not gl.mask().isEmpty():
                p.setClipRegion(gl.mask().translated(at))
            p.drawImage(QRect(at, gl.size()), gl.grabFramebuffer())
            p.setClipping(False)
    p.end()
    if not img.save(str(path)):
        raise OSError(f"could not write {path}")
    return path
