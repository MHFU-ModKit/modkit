# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from types import SimpleNamespace
from typing import Any

from mhfu_studio.shell.camera import OrbitCamera
from mhfu_studio.shell.input import Button, Key, Mod, Pointer
from mhfu_studio.shell.overlay import Ink
from mhfu_studio.shell.studio import Studio
from mhfu_studio.shell.workspace import Gesture
from mhfu_studio.ui import theme, view
from mhfu_studio.ui.testing import FakeWorkspace
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QFont, QImage, QKeyEvent, QMouseEvent, QPainter, QWheelEvent

T, B, M = QEvent.Type, Qt.MouseButton, Qt.KeyboardModifier


def mouse(kind: QEvent.Type, x: float, y: float, button: B, held: B, mods: M = M.NoModifier) -> Any:
    return QMouseEvent(kind, QPointF(x, y), QPointF(x, y), button, held, mods)


def wheel(dy: int, mods: M = M.NoModifier) -> QWheelEvent:
    p, phase = QPointF(1, 2), Qt.ScrollPhase.NoScrollPhase
    return QWheelEvent(p, p, QPoint(0, 0), QPoint(0, dy), B.NoButton, mods, phase, False)


def test_pointer(qapp: object) -> None:
    held, mods = B.LeftButton | B.RightButton, M.ShiftModifier | M.ControlModifier
    e = mouse(T.MouseButtonPress, 5, 6, B.LeftButton, held, mods)
    got = view.pointer("press", e, (100, 50))
    assert got == Pointer(
        "press", 5, 6, (100, 50), Button.LEFT, Button.LEFT | Button.RIGHT, Mod.SHIFT | Mod.CTRL
    )


def test_wheel(qapp: object) -> None:
    got = view.pointer("wheel", wheel(240, M.AltModifier), (10, 10))
    assert (got.kind, got.wheel, got.mods, got.button) == ("wheel", 2.0, Mod.ALT, Button.NONE)


def test_keys(qapp: object) -> None:
    def k(code: Qt.Key, mods: M = M.NoModifier) -> Key | None:
        return view.key(QKeyEvent(T.KeyPress, code, mods))

    assert k(Qt.Key.Key_Escape) == Key("Escape")
    assert k(Qt.Key.Key_Delete) == Key("Delete")
    assert k(Qt.Key.Key_F, M.ControlModifier) == Key("F", Mod.CTRL)
    assert k(Qt.Key.Key_1) == Key("1")
    assert k(Qt.Key.Key_Shift, M.ShiftModifier) is None


def test_press_holds_orbit(qtbot: Any) -> None:
    ws = FakeWorkspace()
    ws.vp = SimpleNamespace(camera=OrbitCamera())  # type: ignore[assignment]
    cam = ws.vp.camera
    v = view.GLView(Studio([ws]))
    qtbot.addWidget(v)
    v.resize(300, 200)

    def drag(button: B, dx: float) -> None:
        v.mousePressEvent(mouse(T.MouseButtonPress, 50, 50, button, button))
        v.mouseMoveEvent(mouse(T.MouseMove, 50 + dx, 50, B.NoButton, button))
        v.mouseReleaseEvent(mouse(T.MouseButtonRelease, 50 + dx, 50, button, B.NoButton))

    ws.consume = Gesture.ORBIT
    yaw = cam.yaw
    drag(B.LeftButton, 40)
    assert cam.yaw == yaw
    target = cam.target.copy()
    drag(B.RightButton, 40)
    assert (cam.target != target).any(), "ORBIT consumed, PAN not"
    ws.consume = Gesture.NONE
    drag(B.LeftButton, 40)
    assert cam.yaw != yaw
    assert [p.kind for p in ws.pointers[:3]] == ["press", "move", "release"]


def test_wheel_dollies(qtbot: Any) -> None:
    ws = FakeWorkspace()
    ws.vp = SimpleNamespace(camera=OrbitCamera())  # type: ignore[assignment]
    v = view.GLView(Studio([ws]))
    qtbot.addWidget(v)
    before = ws.vp.camera.distance
    v.wheelEvent(wheel(120))
    assert ws.vp.camera.distance != before


def test_keys_reach_workspace(qtbot: Any) -> None:
    ws = FakeWorkspace()
    studio = Studio([ws])
    changes: list[int] = []
    studio.listen(lambda: changes.append(1))
    v = view.GLView(studio)
    qtbot.addWidget(v)
    v.keyPressEvent(QKeyEvent(T.KeyPress, Qt.Key.Key_X, M.NoModifier))
    v.keyPressEvent(QKeyEvent(T.KeyPress, Qt.Key.Key_Delete, M.NoModifier))
    assert [k.name for k in ws.keys] == ["X", "Delete"] and changes == [1]


def test_overlay_paints(qapp: object) -> None:
    img = QImage(80, 40, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    o = view.QtOverlay(p, QFont())
    o.rect((2, 2), (20, 20), fill=Ink.SELECTION)
    o.circle((30, 10), 4, color=(1.0, 0.0, 0.0, 1.0), width=2)
    o.text((40, 2), "Hi", size=14)
    p.end()
    assert img.pixelColor(10, 10).rgb() == theme.color(Ink.SELECTION).rgb()
    assert any(img.pixelColor(x, y).alpha() for x in range(40, 80) for y in range(0, 20))
