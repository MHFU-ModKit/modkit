# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A move in miniature beside what the mouse is on: the operand bar's Plays choice (closed, its
move; open, the move under the mouse) and a Move node on the graph.

One window per Brain tab. It never takes focus, so an open list stays open, and it draws on the
3D view's context once a frame (`ws.preview`); without that context it shows the words only.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import TYPE_CHECKING

import shiboken6
from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import QComboBox, QFrame, QLabel, QVBoxLayout, QWidget

from mhfu_studio.monster.previews import Reel
from mhfu_studio.monster.render.preview import SIZE, MovePreview
from mhfu_studio.ui import kit, theme

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace
    from mhfu_studio.shell.studio import Studio

Pair = tuple[int, int]
DELAY_MS = 300
"""How long the mouse rests before the window comes up; once up, it follows at once."""
FRAME_MS = 33
GAP = 8


class MovePopup(QFrame):
    """`ask` for a row's move beside a place on screen, `drop` to close."""

    def __init__(self, ws: MonsterWorkspace, studio: Studio, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("MovePreview")  # the theme's frame
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.ws, self.studio = ws, studio
        self.title = kit.label("", role="heading", wrap=False)
        self.picture = QLabel()
        self.picture.setFixedSize(*SIZE)
        self.caption = kit.label("", role="muted")
        self.caption.setFixedWidth(SIZE[0])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        for w in (self.title, self.picture, self.caption):
            lay.addWidget(w)
        self.reel: Reel | None = None
        #: the row and move asked for, the place on screen it goes next to, and whether beside it
        self._asked: tuple[int, Pair, QRect, bool] | None = None
        self._live = False
        self._clock = 0.0
        self._wait = QTimer(self)
        self._wait.setSingleShot(True)
        self._wait.setInterval(DELAY_MS)
        self._wait.timeout.connect(self._appear)
        self.frames = QTimer(self)
        self.frames.setInterval(FRAME_MS)
        self.frames.timeout.connect(studio.guard("move preview", self._frame))

    def ask(self, row: int, pair: Pair, near: QRect, *, beside: bool = False) -> None:
        """Shows row `row`'s move `pair` next to `near` (global): above or below it, else
        `beside` it. At once while the window is up, else once the mouse has rested."""
        moved = self._asked is None or self._asked[:2] != (row, pair)
        self._asked = (row, pair, near, beside)
        if not self.isVisible():
            self._wait.start()
        elif moved:
            self._appear()
        else:
            self._place()

    def drop(self) -> None:
        self._asked = None
        self._wait.stop()
        self.frames.stop()
        self.hide()

    # -- inside -- #

    def _appear(self) -> None:
        if self._asked is None:
            return
        row, (main, sub), _, _ = self._asked
        self.reel = reel = self.ws.move_reel(row, main, sub)
        self.title.setText(reel.title)
        self.caption.setText(reel.caption())
        self._live = False
        self.studio.guard("move preview", self._start)()
        self.picture.setVisible(self._live)
        if not self._live and reel.clips:
            self.caption.setText(f"{reel.caption()}\nThe picture needs the 3D view shown once")
        self._place()
        self.show()
        if self._live:
            self._clock = time.monotonic()
            self.frames.start()
        else:
            self.frames.stop()

    def _start(self) -> None:
        pv, reel = self.ws.preview(), self.reel
        if pv is None or reel is None or reel.scene is None or not reel.clips:
            return
        ratio = self.devicePixelRatioF()
        pv.resize((round(SIZE[0] * ratio), round(SIZE[1] * ratio)))
        pv.show(reel.scene, reel.clips)
        self._draw(pv)
        self._live = True

    def _frame(self) -> None:
        pv, reel = self.ws.preview(), self.reel
        if pv is None or reel is None or not self._live:
            self.frames.stop()
            return
        now = time.monotonic()
        at = pv.at
        pv.advance(now - self._clock)
        self._clock = now
        if pv.at != at:
            self.caption.setText(reel.caption(pv.at))
        self._draw(pv)

    def _draw(self, pv: MovePreview) -> None:
        img = pv.picture(theme.current().view)
        h, w = img.shape[:2]
        pm = QPixmap.fromImage(QImage(img.data, w, h, 4 * w, QImage.Format.Format_RGBA8888))
        pm.setDevicePixelRatio(self.devicePixelRatioF())
        self.picture.setPixmap(pm)

    def _place(self) -> None:
        if self._asked is None:
            return
        _, _, near, beside = self._asked
        self.adjustSize()
        w, h = self.width(), self.height()
        screen = QGuiApplication.screenAt(near.center()) or self.screen()
        room = screen.availableGeometry()
        if beside:
            right = near.right() + GAP
            x = right if right + w <= room.right() else near.left() - GAP - w
            y = near.top()
        else:
            above = near.top() - GAP - h
            x = near.left()
            y = above if above >= room.top() else near.bottom() + GAP
        x = max(room.left(), min(x, room.right() - w))
        y = max(room.top(), min(y, room.bottom() - h))
        self.move(x, y)


def on_screen(widget: QWidget, rect: QRect | None = None) -> QRect:
    """`rect` (the whole widget by default) of `widget` in global coordinates."""
    r = widget.rect() if rect is None else rect
    return QRect(widget.mapToGlobal(r.topLeft()), r.size())


class ChoiceHover(QObject):
    """The popup on a choice of moves: its move while the mouse is on it, the one under the
    mouse (or the arrow keys) while its list is open; it goes when the list or the mouse does."""

    def __init__(
        self, popup: MovePopup, box: QComboBox, row: int, pairs: Sequence[Pair | None]
    ) -> None:
        super().__init__(box)
        self.popup, self.box, self.row, self.pairs = popup, box, row, tuple(pairs)
        self.listed = box.view().window()
        box.installEventFilter(self)
        self.listed.installEventFilter(self)
        box.highlighted.connect(self._highlighted)
        box.activated.connect(lambda _: popup.drop())

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if not shiboken6.isValid(self.box):
            return False  # the bar is being rebuilt: its list still hides
        listed = self.listed
        kind = event.type()
        if watched is self.box and kind == QEvent.Type.Enter and not listed.isVisible():
            self._ask(self.box.currentIndex(), on_screen(self.box))
        elif watched is self.box and kind == QEvent.Type.Leave and not listed.isVisible():
            self.popup.drop()
        elif watched is listed and kind == QEvent.Type.Hide:
            self.popup.drop()
        return False

    def _highlighted(self, k: int) -> None:
        view = self.box.view()
        row = view.visualRect(self.box.model().index(k, 0))
        at = view.viewport().mapToGlobal(row.topLeft())
        listed = on_screen(self.listed)
        self._ask(k, QRect(QPoint(listed.left(), at.y()), listed.size()), beside=True)

    def _ask(self, k: int, near: QRect, *, beside: bool = False) -> None:
        pair = self.pairs[k] if 0 <= k < len(self.pairs) else None
        if pair is not None:
            self.popup.ask(self.row, pair, near, beside=beside)
