# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The seamless title bar on the window gradient, and the window flags it needs.

macOS keeps its native traffic lights and the global menu bar. Linux and Windows get a
frameless window: our own window buttons, the menus inline, resizing from a thin border.
"""

from __future__ import annotations

import sys
import warnings
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QMouseEvent, QShowEvent
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QMenuBar, QToolButton, QWidget

from mhfu_studio.shell.studio import doc_name
from mhfu_studio.ui import kit

if TYPE_CHECKING:
    from mhfu_studio.shell.studio import Studio

MAC = sys.platform == "darwin"
#: the traffic lights span 9 to 69 points from the left and sit centred in the title area,
#: whose height is the window's top safe-area margin (32 points on macOS 26)
MAC_INSET, MAC_HEIGHT = 84, 32
HEIGHT = 40
#: the frameless window's resize border
GRIP = 5

SEND_TIP = "Sends this workspace's edits into the running game"
SWITCH_TIP = (
    "The workspace: which kind of document you edit. Each keeps its own document, panels,"
    " tools and layout."
)


def switch_tip(name: str) -> str:
    return f"Switch to the {name} workspace. {SWITCH_TIP}"


def frame(win: QMainWindow, native: bool = MAC) -> None:
    """The window flags for `TitleBar`; before the window is first shown."""
    if native:
        with warnings.catch_warnings():  # PySide6 resolves it to a deprecated alias and warns
            warnings.simplefilter("ignore", DeprecationWarning)
            win.setWindowFlag(Qt.WindowType.ExpandedClientAreaHint, True)
        win.setWindowFlag(Qt.WindowType.NoTitleBarBackgroundHint, True)
        win.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)
    else:
        win.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        win.setContentsMargins(GRIP, GRIP, GRIP, GRIP)
        Grips(win)


def title(doc: str, app: str, native: bool = MAC) -> str:
    """The window system's title: none on macOS, where AppKit draws it over `TitleBar`, which
    names the document already."""
    return "" if native else f"{doc}[*] - {app}"


class TitleBar(QWidget):
    """The app's name, the workspace switcher, the document and its unsaved chip, Send to game;
    the menus and window buttons too where the platform does not draw them."""

    def __init__(self, win: QMainWindow, studio: Studio, native: bool = MAC) -> None:
        super().__init__(win)
        self.studio = studio
        self.native = native
        self._watching = False
        self.setObjectName("TitleBar")
        self.setFixedHeight(MAC_HEIGHT if native else HEIGHT)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(MAC_INSET if native else 14, 0, 12 if native else 6, 0)
        lay.setSpacing(12)
        lay.addWidget(kit.label("MHFU Studio", role="title", wrap=False))
        self.switcher = kit.Segmented(
            [(n, n.capitalize()) for n in studio.names],
            tip=SWITCH_TIP,
            on=self._switch,
            current=studio.active.name,
        )
        lay.addWidget(self.switcher)
        #: native on macOS (the global bar while this window is active), inline elsewhere
        self.menus = QMenuBar(self)
        if not native:
            self.menus.setNativeMenuBar(False)
            lay.addWidget(self.menus, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addStretch(1)
        self.doc = kit.label(role="muted", wrap=False)
        self.chip = kit.label(role="chip", wrap=False)
        lay.addWidget(self.doc)
        lay.addWidget(self.chip)
        lay.addStretch(1)
        #: the window gives it its action (`setDefaultAction`)
        self.send = QToolButton()
        self.send.setObjectName("Send")
        self.send.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.send.setToolTip(SEND_TIP)
        self.send.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(self.send)
        self.buttons: list[QToolButton] = []
        if not native:
            for icon, tip, on in (
                ("ph.minus", "Minimises the window", win.showMinimized),
                ("ph.square", "Maximises the window, or gives it back its size", self._zoom),
                ("ph.x", "Closes the studio; it asks first about unsaved edits", win.close),
            ):
                b = kit.icon_button(icon, tip=tip, on=on)
                lay.addWidget(b)
                self.buttons.append(b)

    def _switch(self, name: str) -> None:
        self.studio.act(f"switch to {name}", lambda: self.studio.switch(name))()

    def _zoom(self) -> None:
        w = self.window()
        if w.isMaximized():
            w.showNormal()
        else:
            w.showMaximized()

    def sync(self) -> None:
        ws = self.studio.active
        self.switcher.set(ws.name)
        self.doc.setText(doc_name(ws))
        dirty = ws.document is not None and ws.document.dirty
        self.chip.setText("● unsaved" if dirty else "")

    def showEvent(self, e: QShowEvent) -> None:  # noqa: N802
        super().showEvent(e)
        h = self.window().windowHandle()
        if self.native and h is not None and not self._watching:
            h.safeAreaMarginsChanged.connect(self._fit)
            self._watching = True
            self._fit()

    def _fit(self, *_: object) -> None:
        """As tall as the native title area, so the traffic lights sit centred in the bar."""
        h = self.window().windowHandle()
        top = h.safeAreaMargins().top() if h is not None else 0
        self.setFixedHeight(top if top > 0 else MAC_HEIGHT)

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        h = self.window().windowHandle()
        if e.button() == Qt.MouseButton.LeftButton and h is not None:
            h.startSystemMove()
        else:
            super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._zoom()


def _cursor(edges: Qt.Edge) -> Qt.CursorShape:
    e, c = Qt.Edge, Qt.CursorShape
    across = bool(edges & (e.LeftEdge | e.RightEdge))
    along = bool(edges & (e.TopEdge | e.BottomEdge))
    if across and along:
        falling = edges in (e.LeftEdge | e.TopEdge, e.RightEdge | e.BottomEdge)
        return c.SizeFDiagCursor if falling else c.SizeBDiagCursor
    return c.SizeHorCursor if across else c.SizeVerCursor


class Grip(QWidget):
    """One strip or corner of the border; a press hands the resize to the window system."""

    def __init__(self, win: QWidget, edges: Qt.Edge) -> None:
        super().__init__(win)
        self.edges = edges
        self.setCursor(_cursor(edges))

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        h = self.window().windowHandle()
        if e.button() == Qt.MouseButton.LeftButton and h is not None:
            h.startSystemResize(self.edges)


class Grips(QObject):
    """Eight `Grip`s around a frameless window's edge, kept in place as it resizes."""

    def __init__(self, win: QWidget) -> None:
        super().__init__(win)
        self.win = win
        e = Qt.Edge
        self.grips = [
            Grip(win, edges)
            for edges in (
                e.LeftEdge,
                e.RightEdge,
                e.TopEdge,
                e.BottomEdge,
                e.LeftEdge | e.TopEdge,
                e.RightEdge | e.TopEdge,
                e.LeftEdge | e.BottomEdge,
                e.RightEdge | e.BottomEdge,
            )
        ]
        win.installEventFilter(self)

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:  # noqa: N802
        if ev.type() in (QEvent.Type.Resize, QEvent.Type.WindowStateChange, QEvent.Type.Show):
            self.place()
        return False

    def place(self) -> None:
        w, h, g = self.win.width(), self.win.height(), GRIP
        free = not (self.win.isMaximized() or self.win.isFullScreen())
        e = Qt.Edge
        for grip in self.grips:
            x = 0 if grip.edges & e.LeftEdge else w - g if grip.edges & e.RightEdge else g
            y = 0 if grip.edges & e.TopEdge else h - g if grip.edges & e.BottomEdge else g
            gw = g if grip.edges & (e.LeftEdge | e.RightEdge) else w - 2 * g
            gh = g if grip.edges & (e.TopEdge | e.BottomEdge) else h - 2 * g
            grip.setGeometry(x, y, gw, gh)
            grip.setVisible(free)
            grip.raise_()
