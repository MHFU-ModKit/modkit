# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The viewport: the active workspace's offscreen render blitted into a QOpenGLWidget, then
its overlay and the HUD through QPainter. Input reaches the workspace as `shell.input` data.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING, Literal

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QFont,
    QKeyEvent,
    QMouseEvent,
    QOpenGLContext,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QRegion,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QWidget

from mhfu_studio.shell.context import borrowed
from mhfu_studio.shell.input import Button, Key, Mod, Pointer
from mhfu_studio.shell.overlay import Color, Ink, Point
from mhfu_studio.shell.text import camera_line, plain
from mhfu_studio.shell.workspace import Gesture, Shortcut
from mhfu_studio.ui import theme

if TYPE_CHECKING:
    import moderngl

    from mhfu_studio.shell.studio import Studio

#: moderngl leaves the pixel store at 1 and QPainter's glyph uploads then shear
GL_UNPACK_ALIGNMENT, GL_PACK_ALIGNMENT = 0x0CF5, 0x0D05
#: the card's corner radius, the theme's #Card one
RADIUS = 8
HUD_AT: Point = (10.0, 8.0)
PAN_BUTTONS = Button.RIGHT | Button.MIDDLE
#: the camera's gestures, for Help > Keyboard shortcuts
MOUSE = (
    Shortcut(("Left-drag",), "Turns the view (Alt-drag where a drag selects)"),
    Shortcut(("Right-drag", "Middle-drag"), "Pans the view"),
    Shortcut(("Wheel",), "Zooms the view"),
)

_BUTTONS = (
    (Qt.MouseButton.LeftButton, Button.LEFT),
    (Qt.MouseButton.RightButton, Button.RIGHT),
    (Qt.MouseButton.MiddleButton, Button.MIDDLE),
)
_MODS = (
    (Qt.KeyboardModifier.ShiftModifier, Mod.SHIFT),
    (Qt.KeyboardModifier.ControlModifier, Mod.CTRL),  # Cmd on macOS
    (Qt.KeyboardModifier.AltModifier, Mod.ALT),
)
_MODIFIER_KEYS = {
    Qt.Key.Key_Shift,
    Qt.Key.Key_Control,
    Qt.Key.Key_Meta,
    Qt.Key.Key_Alt,
    Qt.Key.Key_AltGr,
    Qt.Key.Key_CapsLock,
}

Kind = Literal["press", "move", "release", "wheel", "leave"]


# ---- Qt events as shell.input data ------------------------------------------------------- #


def buttons(b: Qt.MouseButton) -> Button:
    out = Button.NONE
    for q, ours in _BUTTONS:
        if b & q:
            out |= ours
    return out


def mods(m: Qt.KeyboardModifier) -> Mod:
    out = Mod.NONE
    for q, ours in _MODS:
        if m & q:
            out |= ours
    return out


def pointer(
    kind: Kind, e: QMouseEvent | QWheelEvent, size: tuple[int, int], double: bool = False
) -> Pointer:
    p = e.position()
    return Pointer(
        kind,
        p.x(),
        p.y(),
        size,
        button=buttons(e.button()) if isinstance(e, QMouseEvent) else Button.NONE,
        buttons=buttons(e.buttons()),
        mods=mods(e.modifiers()),
        wheel=e.angleDelta().y() / 120.0 if isinstance(e, QWheelEvent) else 0.0,
        double=double,
    )


def key(e: QKeyEvent) -> Key | None:
    """None for a bare modifier and keys Qt has no name for."""
    try:
        k = Qt.Key(e.key())
    except ValueError:
        return None
    if k in _MODIFIER_KEYS:
        return None
    return Key(k.name.removeprefix("Key_"), mods(e.modifiers()))


# ---- the overlay ------------------------------------------------------------------------- #


class QtOverlay:
    """`shell.overlay.Overlay` on a QPainter; `Ink` roles come from the theme."""

    def __init__(self, painter: QPainter, font: QFont, size: tuple[int, int] | None = None) -> None:
        dev = painter.device()
        self.size = size or (dev.width(), dev.height())
        self.painter = painter
        self.font = QFont(font)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    def _ink(self, color: Color | None, fill: Color | None = None, width: float = 1.0) -> None:
        p = self.painter
        p.setPen(Qt.PenStyle.NoPen if color is None else QPen(theme.color(color), width))
        p.setBrush(Qt.BrushStyle.NoBrush if fill is None else theme.color(fill))

    def line(self, a: Point, b: Point, color: Color, width: float = 1.0) -> None:
        self._ink(color, None, width)
        self.painter.drawLine(QPointF(*a), QPointF(*b))

    def polyline(
        self, pts: Sequence[Point], color: Color, width: float = 1.0, closed: bool = False
    ) -> None:
        self._ink(color, None, width)
        poly = QPolygonF([QPointF(*q) for q in pts])
        if closed:
            self.painter.drawPolygon(poly)
        else:
            self.painter.drawPolyline(poly)

    def polygon(self, pts: Sequence[Point], fill: Color) -> None:
        self._ink(None, fill)
        self.painter.drawPolygon(QPolygonF([QPointF(*q) for q in pts]))

    def rect(
        self, a: Point, b: Point, color: Color | None = None, fill: Color | None = None
    ) -> None:
        self._ink(color, fill)
        self.painter.drawRect(QRectF(QPointF(*a), QPointF(*b)).normalized())

    def circle(
        self,
        c: Point,
        r: float,
        color: Color | None = None,
        fill: Color | None = None,
        width: float = 1.0,
    ) -> None:
        self._ink(color, fill, width)
        self.painter.drawEllipse(QPointF(*c), r, r)

    def text(
        self, p: Point, s: str, color: Color = Ink.TEXT, size: float = 11.0, shadow: bool = True
    ) -> None:
        """`size` in points of the view, like the coordinates."""
        f = QFont(self.font)
        f.setPixelSize(max(1, round(size)))
        self.painter.setFont(f)
        m = self.painter.fontMetrics()
        x, y = p
        for i, line in enumerate(s.splitlines()):
            at = QPointF(x, y + m.ascent() + i * m.lineSpacing())
            if shadow:
                self.painter.setPen(theme.color(Ink.SHADOW))
                self.painter.drawText(at + QPointF(1, 1), line)
            self.painter.setPen(theme.color(color))
            self.painter.drawText(at, line)


# ---- the widget -------------------------------------------------------------------------- #


class GLView(QOpenGLWidget):
    """Draws `studio.active`, whichever workspace that is."""

    def __init__(self, studio: Studio, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.studio = studio
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 240)
        self._clock = time.monotonic()
        #: the gestures each held button's press consumed, until that button's release
        self._held: dict[Button, Gesture] = {}
        self._last: tuple[float, float] | None = None
        #: the camera's yaw, pitch and distance under the HUD (View > Show camera readout)
        self.show_camera = False

    # ---- GL ---------------------------------------------------------------------------- #

    def initializeGL(self) -> None:  # noqa: N802
        ctx = self.context()
        if ctx is not None:
            ctx.aboutToBeDestroyed.connect(self.release)

    def release(self) -> None:
        """The workspaces' GL objects, then moderngl's view of the context, while current."""
        ctx = self.studio.ctx
        live = self.isValid()
        if live:
            self.makeCurrent()
        self.studio.close()
        if ctx is not None:
            ctx.release()
        if live:
            self.doneCurrent()

    @contextmanager
    def current(self) -> Iterator[None]:
        """This view's GL context current inside (`Studio.gl_current`), none after; nothing to
        do before the view has a context, or while it is current (a paint)."""
        ctx = self.context()
        if ctx is None or not self.isValid() or QOpenGLContext.currentContext() is ctx:
            yield
            return
        with borrowed():
            self.makeCurrent()
            try:
                yield
            finally:
                self.doneCurrent()

    def paintGL(self) -> None:  # noqa: N802
        self.studio.guard("draw", self._draw)()

    def _draw(self) -> None:
        ws = self.studio.active
        fresh = ws.viewport is None
        vp = self.studio.ensure_gl(ws)
        if vp is None:
            self._paint_error()
            return
        if fresh:
            self.studio.changed()  # set up: the status and the renderer have something to say
        now = time.monotonic()
        ws.frame(now - self._clock)
        self._clock = now
        vp.background = theme.current().view
        screen = self._screen_fbo(vp.ctx)
        screen.use()  # Viewport.draw comes back to it
        vp.resize(screen.size)
        vp.draw()
        vp.ctx.copy_framebuffer(screen, vp.target.resolved)
        screen.use()
        self._pixel_store()
        p = QPainter(self)  # after moderngl, not around it: a native bracket loses the text
        try:
            o = QtOverlay(p, self.font(), (self.width(), self.height()))
            ws.paint(o)
            camera = camera_line(vp.camera) if self.show_camera else ""
            hud = "\n".join(t for t in (ws.hud(), camera) if t)
            o.text(HUD_AT, plain(hud))
        finally:
            p.end()
        if ws.animating():
            self.update()

    def _screen_fbo(self, ctx: moderngl.Context) -> moderngl.Framebuffer:
        """The widget's own framebuffer, asked for every frame: a resize makes a new one, which
        may reuse the old one's name. A reference: never released."""
        return ctx.detect_framebuffer(int(self.defaultFramebufferObject()))

    def _pixel_store(self) -> None:
        ctx = self.context()
        if ctx is not None:
            gl = ctx.functions()
            gl.glPixelStorei(GL_UNPACK_ALIGNMENT, 4)
            gl.glPixelStorei(GL_PACK_ALIGNMENT, 4)

    def _paint_error(self) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), theme.color(theme.current().view))
        p.setPen(theme.color(Ink.TEXT))
        p.drawText(
            QRectF(self.rect()).adjusted(16, 16, -16, -16),
            Qt.TextFlag.TextWordWrap.value,
            f"No 3D view: {self.studio.error or 'no GL'}",
        )
        p.end()

    def resizeEvent(self, e: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(e)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), RADIUS, RADIUS)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    # ---- input ------------------------------------------------------------------------- #

    @property
    def held(self) -> bool:
        """A mouse button is down over the view: a drag may be under way."""
        return bool(self._held)

    def _size(self) -> tuple[int, int]:
        return self.width(), self.height()

    def _ask(self, label: str, fn: Callable[[], object]) -> object:
        """`fn()` guarded: a workspace's error goes to the status line, not out of Qt."""
        got: list[object] = [None]
        self.studio.guard(label, lambda: got.__setitem__(0, fn()))()
        return got[0]

    def _send(self, ev: Pointer) -> Gesture:
        got = self._ask("pointer", lambda: self.studio.active.pointer(ev))
        return got if isinstance(got, Gesture) else Gesture.NONE

    def _consumed(self) -> Gesture:
        out = Gesture.NONE
        for g in self._held.values():
            out |= g
        return out

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        self._press(pointer("press", e, self._size()))

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        self._press(pointer("press", e, self._size(), double=True))

    def _press(self, ev: Pointer) -> None:
        self._held[ev.button] = self._send(ev)
        self._last = ev.pos
        self.studio.changed()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        ev = pointer("move", e, self._size())
        used = self._send(ev) | self._consumed()
        last, self._last = self._last, ev.pos
        vp = self.studio.active.viewport
        if vp is not None and last is not None:
            dx, dy = ev.x - last[0], ev.y - last[1]
            if Button.LEFT in self._held and Button.LEFT in ev.buttons:
                if Gesture.ORBIT not in used:
                    vp.camera.orbit(dx, dy)
            elif any(b in self._held for b in PAN_BUTTONS) and ev.buttons & PAN_BUTTONS:
                if Gesture.PAN not in used:
                    vp.camera.pan(dx, dy, self.height())
        self.update()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        ev = pointer("release", e, self._size())
        self._send(ev)
        self._held.pop(ev.button, None)
        self._last = ev.pos
        self.studio.changed()

    def wheelEvent(self, e: QWheelEvent) -> None:  # noqa: N802
        ev = pointer("wheel", e, self._size())
        used = self._send(ev)
        vp = self.studio.active.viewport
        if vp is not None and Gesture.DOLLY not in (used | self._consumed()):
            vp.camera.dolly(ev.wheel)
        if used:
            self.studio.changed()
        else:
            self.update()

    def leaveEvent(self, e: QEvent) -> None:  # noqa: N802
        x, y = self._last or (-1.0, -1.0)
        self._send(Pointer("leave", x, y, self._size()))
        self.update()
        super().leaveEvent(e)

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        k = key(e)
        if k is not None and self._ask("key", lambda: self.studio.active.key(k)):
            self.studio.changed()
        else:
            super().keyPressEvent(e)
