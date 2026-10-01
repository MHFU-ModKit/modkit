# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The viewport as a QOpenGLWidget: the workspace's offscreen render, blitted, then QPainter.

moderngl attaches to the widget's context in `initializeGL`; the picture is the same
`Viewport.draw` the imgui window and the headless renders use.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeyEvent, QMouseEvent, QPainter, QPen, QWheelEvent
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QWidget

from mhfu_studio.shell.widgets import camera_line, plain

from . import gizmo

if TYPE_CHECKING:
    import moderngl

    from mhfu_studio.map.workspace import MapWorkspace
    from mhfu_studio.shell.app import Studio
    from mhfu_studio.shell.viewport import Viewport

CLICK_SLOP = 4.0
GL_UNPACK_ALIGNMENT, GL_PACK_ALIGNMENT = 0x0CF5, 0x0D05


class GLView(QOpenGLWidget):
    #: the selection or the document changed here: panels re-read
    changed = Signal()

    def __init__(self, studio: Studio, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.studio = studio
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 240)
        self._screen: tuple[int, moderngl.Framebuffer] | None = None
        self._clock = time.monotonic()
        self._last: QPointF | None = None
        self._mode = ""
        self._press: tuple[float, float] | None = None
        self._box: tuple[float, float, float, float] | None = None
        self._drag: gizmo.Drag | None = None
        self._offset = np.zeros(3)

    # GL

    def initializeGL(self) -> None:  # noqa: N802
        ctx = self.context()
        if ctx is not None:
            ctx.aboutToBeDestroyed.connect(self.release)

    def release(self) -> None:
        """The workspaces' GL objects and moderngl's view of the context, while it exists."""
        ctx = self.studio.ctx
        self.makeCurrent()
        self.studio.close()
        if ctx is not None:
            ctx.release()
        self.doneCurrent()

    def _vp(self) -> Viewport | None:
        if self.studio.error:
            return None
        return self.studio._ensure_gl(self.studio.active)  # spike: the shell's attach, as is

    def paintGL(self) -> None:  # noqa: N802
        vp = self._vp()
        if vp is None:
            self._paint_error()
            return
        ws = self.studio.active
        now = time.monotonic()
        ws.frame(now - self._clock)
        self._clock = now
        dpr = self.devicePixelRatioF()
        vp.resize((round(self.width() * dpr), round(self.height() * dpr)))
        vp.draw()
        screen = self._screen_fbo(vp.ctx)
        resolved = vp.target._resolve  # spike: Target has no public resolved framebuffer
        assert resolved is not None
        vp.ctx.copy_framebuffer(screen, resolved)
        screen.use()
        self._pixel_store()
        p = QPainter(self)  # after moderngl, not around it: a native bracket loses the text
        self._paint_overlay(p, vp)
        p.end()
        if ws.animating():
            self.update()

    def _screen_fbo(self, ctx: moderngl.Context) -> moderngl.Framebuffer:
        """The widget's own framebuffer; Qt makes a new one on every resize."""
        glo = int(self.defaultFramebufferObject())
        if self._screen is None or self._screen[0] != glo:
            self._screen = (glo, ctx.detect_framebuffer(glo))
        return self._screen[1]

    # painting over the picture

    def _paint_error(self) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(26, 28, 33))
        p.setPen(QColor(240, 160, 140))
        p.drawText(
            self.rect().adjusted(16, 16, -16, -16),
            Qt.TextFlag.TextWordWrap,
            self.studio.error or "no GL",
        )
        p.end()

    def _pixel_store(self) -> None:
        """Back to GL's defaults: moderngl uploads with alignment 1, Qt's glyph cache with 4."""
        ctx = self.context()
        if ctx is not None:
            gl = ctx.functions()
            gl.glPixelStorei(GL_UNPACK_ALIGNMENT, 4)
            gl.glPixelStorei(GL_PACK_ALIGNMENT, 4)

    def _paint_overlay(self, p: QPainter, vp: Viewport) -> None:
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        ws = self._map()
        if ws is not None:
            self._labels(p, ws)
            self._gizmo(p, ws)
        if self._box is not None:
            x0, y0, x1, y1 = self._box
            r = QRectF(QPointF(x0, y0), QPointF(x1, y1)).normalized()
            p.fillRect(r, QColor(100, 180, 255, 38))
            p.setPen(QPen(QColor(128, 200, 255, 230), 1))
            p.drawRect(r)
        hud = "\n".join(t for t in (self.studio.active.hud(), camera_line(vp.camera)) if t)
        self._text(p, 10, 8, plain(hud))

    def _text(self, p: QPainter, x: float, y: float, text: str) -> None:
        p.setFont(QFont(self.font().family(), 11))
        line = p.fontMetrics().height()
        for i, s in enumerate(text.splitlines()):
            at = QPointF(x, y + (i + 1) * line)
            p.setPen(QColor(0, 0, 0, 150))
            p.drawText(at + QPointF(1, 1), s)
            p.setPen(QColor(235, 240, 250, 230))
            p.drawText(at, s)

    def _labels(self, p: QPainter, ws: MapWorkspace) -> None:
        vp = ws.vp
        labels = vp.labels() if vp is not None else []
        if vp is None or not labels:
            return
        size = (self.width(), self.height())
        pts = vp.camera.project(np.array([lb.pos for lb in labels]), size)
        p.setFont(QFont(self.font().family(), 10))
        for lb, q in zip(labels, pts, strict=True):
            if 0 <= q[2] <= 1 and 0 <= q[0] <= size[0] and 0 <= q[1] <= size[1]:
                r, g, b, _ = lb.color
                at = QPointF(float(q[0]) + 6, float(q[1]) + 4)
                p.setPen(QColor(0, 0, 0, 190))
                p.drawText(at + QPointF(1, 1), lb.text)
                p.setPen(QColor.fromRgbF(r, g, b, 0.95))
                p.drawText(at, lb.text)

    # the map workspace's tools (the spike drives one workspace)

    def _map(self) -> MapWorkspace | None:
        from mhfu_studio.map.workspace import MapWorkspace

        ws = self.studio.active
        if isinstance(ws, MapWorkspace) and ws.vp is not None and ws.scene is not None:
            return ws
        return None

    def _pivot(self, ws: MapWorkspace) -> Any:
        from mhfu_studio.map.core.edit import COLLISION

        assert ws.scene is not None
        sel = ws.col_sel if ws.tools.kind == COLLISION else ws.selection
        return sel.centroid(ws.scene)

    def _handles(self, ws: MapWorkspace) -> list[gizmo.Handle]:
        from mhfu_studio.map.tools import MOVE

        if ws.vp is None or ws.tools.tool != MOVE or not ws.tools.gizmo_visible:
            return []
        pivot = self._pivot(ws) + self._offset
        return gizmo.handles(ws.vp.camera, pivot, (self.width(), self.height()))

    def _gizmo(self, p: QPainter, ws: MapWorkspace) -> None:
        for h in self._handles(ws):
            active = self._drag is not None and self._drag.axis == h.axis
            c = QColor(*gizmo.COLORS[h.axis])
            p.setPen(QPen(c.lighter(140) if active else c, 4 if active else 3))
            p.drawLine(QPointF(*h.a), QPointF(*h.b))
            p.setBrush(c)
            p.drawEllipse(QPointF(*h.b), 5, 5)

    def _ray(self, x: float, y: float) -> tuple[Any, Any]:
        vp = self.studio.active.viewport
        assert vp is not None
        return vp.camera.ray(x, y, (self.width(), self.height()))

    # input

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        from mhfu_studio.map.tools import SELECT

        self._last = e.position()
        x, y = e.position().x(), e.position().y()
        ws = self._map()
        if e.button() == Qt.MouseButton.LeftButton and ws is not None:
            axis = gizmo.hit(self._handles(ws), x, y)
            if axis is not None:
                self._begin_drag(ws, axis, x, y)
                return
            if ws.tools.tool == SELECT and not e.modifiers() & Qt.KeyboardModifier.AltModifier:
                self._mode, self._press = "pick", (x, y)
                return
        if e.button() == Qt.MouseButton.LeftButton:
            self._mode = "orbit"
        elif e.button() in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton):
            self._mode = "pan"

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        vp = self.studio.active.viewport
        x, y = e.position().x(), e.position().y()
        last, self._last = self._last, e.position()
        if vp is None:
            return
        ws = self._map()
        if self._drag is not None and ws is not None and ws.session is not None:
            self._move_drag(ws, x, y)
        elif self._mode == "pick" and self._press is not None:
            px, py = self._press
            if abs(x - px) > CLICK_SLOP or abs(y - py) > CLICK_SLOP:
                self._box = (px, py, x, y)
        elif self._mode and last is not None:
            dx, dy = x - last.x(), y - last.y()
            if self._mode == "orbit":
                vp.camera.orbit(dx, dy)
            else:
                vp.camera.pan(dx, dy, self.height())
        elif ws is not None:
            ws.tools._hover((x, y), (self.width(), self.height()))  # spike: tools' own hover
        else:
            return
        self.update()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        ws = self._map()
        x, y = e.position().x(), e.position().y()
        shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        size = (self.width(), self.height())
        if self._drag is not None and ws is not None:
            self._end_drag(ws, x, y)
        elif self._mode == "pick" and ws is not None:
            if self._box is not None:
                ws.tools.box_select(self._box, size, shift)
            else:
                ws.tools.click((x, y), size, shift)
            self.changed.emit()
        self._mode, self._press, self._box = "", None, None
        self.update()

    def wheelEvent(self, e: QWheelEvent) -> None:  # noqa: N802
        vp = self.studio.active.viewport
        if vp is not None:
            vp.camera.dolly(e.angleDelta().y() / 120.0)
            self.update()

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        from mhfu_studio.map.core.edit import COLLISION, CollisionSelection, Selection

        ws = self._map()
        if ws is None:
            return super().keyPressEvent(e)
        k = e.key()
        if k == Qt.Key.Key_Escape:
            if ws.tools.kind == COLLISION:
                ws.tools.select_collision(CollisionSelection())
            else:
                ws.tools.select(Selection(ws.tools.kind))
        elif k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            ws.delete_selected()
        elif k == Qt.Key.Key_F:
            ws.frame_selection()
        else:
            return super().keyPressEvent(e)
        self.changed.emit()
        self.update()

    # the gizmo drag: the session's begin, preview and commit, as the imgui tools do

    def _begin_drag(self, ws: MapWorkspace, axis: int, x: float, y: float) -> None:
        from mhfu_studio.map.core.edit import COLLISION

        sess = ws.session
        assert sess is not None
        if ws.tools.kind == COLLISION:
            sess.begin_collision(ws.col_sel)
        else:
            sess.begin(ws.selection)
        self._drag = gizmo.Drag(self._pivot(ws), axis, self._ray(x, y))

    def _move_drag(self, ws: MapWorkspace, x: float, y: float) -> None:
        from mhfu_studio.map.core.edit import compose
        from mhfu_studio.map.tools import MOVE

        assert self._drag is not None and ws.session is not None
        snap = ws.tools.snaps[MOVE] if ws.tools.snap else None
        self._offset = self._drag.offset(self._ray(x, y), snap)
        ws.session.preview(compose(by=self._offset))

    def _end_drag(self, ws: MapWorkspace, x: float, y: float) -> None:
        from mhfu_studio.map.core.edit import EditError, compose

        drag, self._drag = self._drag, None
        offset, self._offset = self._offset, np.zeros(3)
        assert drag is not None and ws.session is not None
        try:
            ops = ws.session.commit(compose(by=offset), drag.pivot)
        except EditError as err:
            ops, ws.message = [], f"refused: {err}"
        ws.after_commit(ops)
        ws.tools.reseat()
        self.changed.emit()
