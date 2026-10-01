# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The map workspace in the real window, offscreen: the view's mouse and keys, the docks and
the menus. The workspace draws on the shared headless context; the view only takes input."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from mhfu.files import Extracted
from mhfu_studio.map.panels.selection import SelectionPanel
from mhfu_studio.map.tools import MOVE, TOOL
from mhfu_studio.map.workspace import MapWorkspace
from mhfu_studio.shell.manipulator import ARM_PX, world_per_px
from mhfu_studio.ui import kit
from mhfu_studio.ui.testing import no_gl_or_skip
from mhfu_studio.ui.window import Window
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QDockWidget

T, B = QEvent.Type, Qt.MouseButton
MINE = ("Selection", "Add", "Groups", "Collision")


def errors(w: Window) -> list[str]:
    """Every action, panel build and sync that raised: none may."""
    return [label for label, _ in w.studio.errors]


def open_map(make_window: Callable[..., Window], ws: MapWorkspace) -> Window:
    no_gl_or_skip()
    assert ws.vp is not None and ws.scene is not None
    w = make_window(ws)
    w.sync()
    return w


def at(w: Window, p: Any) -> tuple[float, float]:
    ws = w.studio.active
    assert isinstance(ws, MapWorkspace) and ws.vp is not None
    x, y, _ = ws.vp.camera.project(np.asarray(p, np.float64), (w.view.width(), w.view.height()))[0]
    return float(x), float(y)


def mouse(w: Window, kind: QEvent.Type, p: tuple[float, float], held: bool) -> None:
    button = B.NoButton if kind == T.MouseMove else B.LeftButton
    e = QMouseEvent(
        kind,
        QPointF(*p),
        QPointF(*p),
        button,
        B.LeftButton if held else B.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    {T.MouseButtonPress: w.view.mousePressEvent, T.MouseMove: w.view.mouseMoveEvent}.get(
        kind, w.view.mouseReleaseEvent
    )(e)


def click(w: Window, p: tuple[float, float]) -> None:
    mouse(w, T.MouseButtonPress, p, True)
    mouse(w, T.MouseButtonRelease, p, False)
    w.sync()


def drag(w: Window, a: tuple[float, float], b: tuple[float, float]) -> None:
    mouse(w, T.MouseButtonPress, a, True)
    mouse(w, T.MouseMove, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), True)
    mouse(w, T.MouseMove, b, True)
    mouse(w, T.MouseButtonRelease, b, False)
    w.sync()


def arm(w: Window, t: float) -> tuple[float, float]:
    """A point `t` arm's lengths along the gizmo's x arrow."""
    ws = w.studio.active
    assert isinstance(ws, MapWorkspace) and ws.vp is not None
    pivot = ws.tools.pose()[:3, 3]
    reach = ARM_PX * world_per_px(ws.vp.camera, pivot, w.view.height())
    return at(w, pivot + np.array([t * reach, 0.0, 0.0]))


def panel(w: Window, label: str) -> Any:
    d = w.findChild(QDockWidget, f"map/{label}")
    assert d is not None, label
    return d.widget()


def test_opens_on_the_village(make_window: Callable[..., Window], ws: MapWorkspace) -> None:
    w = open_map(make_window, ws)
    assert ws.scene is not None and ws.scene.stage == 139 and ws.row == 0
    assert "st139 Pokke village" in w.where.text() and errors(w) == []
    for label in MINE:
        p = panel(w, label)
        p.sync()
        assert p.gate.currentWidget() is p.gate.page, label
        assert kit.missing_tips(p) == [], label
    assert isinstance(panel(w, "Selection"), SelectionPanel)


def test_click_drag_undo(make_window: Callable[..., Window], ws: MapWorkspace) -> None:
    w = open_map(make_window, ws)
    assert ws.vp is not None and ws.scene is not None and ws.session is not None
    ws.vp.camera.look("top")
    g = ws.scene.group(0, 1)
    click(w, at(w, g.positions[g.component_vertices(1)].mean(0)))
    assert ws.selection.parts == {(0, 1): [1]}
    assert "sub0.g1: 1 object(s)" in panel(w, "Selection").what.text()
    ws.set_tool(TOOL, MOVE)
    start = ws.selection.centroid(ws.scene)
    yaw = ws.vp.camera.yaw
    drag(w, arm(w, 0.7), arm(w, 1.7))
    assert len(ws.session.ops) == 1 and ws.vp.camera.yaw == yaw, "the drag must not orbit"
    ws.frame(0.0)
    assert ws.selection.centroid(ws.scene)[0] - start[0] > 100.0
    assert w.undo_action.isEnabled()
    w.undo_action.trigger()
    w.sync()
    assert not ws.session.ops and panel(w, "Selection").count.text() == "No edits yet"
    w.view.keyPressEvent(QKeyEvent(T.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert ws.selection.empty
    assert errors(w) == []


def test_real_village(make_window: Callable[..., Window], mhfu_data: Path, gl: Any) -> None:
    ws = MapWorkspace(Extracted.find(mhfu_data))
    ws.setup(gl)
    assert ws.vp is not None
    ws.vp.resize((640, 400))
    w = open_map(make_window, ws)
    assert ws.vp is not None and ws.scene is not None and ws.session is not None
    assert ws.scene.stage == 139 and "Pokke" in ws.scene.name
    click(w, (w.view.width() / 2, w.view.height() / 2))
    assert not ws.selection.empty, "the middle of the village is not empty"
    ws.set_tool(TOOL, MOVE)
    drag(w, arm(w, 0.7), arm(w, 1.5))
    assert len(ws.session.ops) == 1 and ws.session.ops[0]["op"] == "transform"
    w.undo_action.trigger()
    assert not ws.session.ops
    assert errors(w) == []
