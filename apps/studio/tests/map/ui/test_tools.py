# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The map's tool groups on the window's toolbar."""

from collections.abc import Callable
from typing import Any

import pytest
from mhfu.files import Extracted
from mhfu_studio.map.core.atlas import Atlas
from mhfu_studio.map.core.edit import COLLISION, FACE
from mhfu_studio.map.tools import MOVE, PICK, SCALE, TOOL
from mhfu_studio.map.workspace import MapWorkspace
from mhfu_studio.ui.testing import gl_or_skip
from mhfu_studio.ui.window import Window
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QToolButton, QWidget

LABELS = ["Select", "Move", "Rotate", "Scale", "Groups", "Objects", "Faces", "Collision"]


@pytest.fixture
def window(make_window: Callable[..., Window], game: Extracted, atlas: Atlas) -> Window:
    return make_window(MapWorkspace(game, atlas), show=False)


def action(w: Window, text: str) -> QAction:
    return next(a for _, _, a in w._tool_actions if a.text() == text)


def checked(w: Window) -> list[str]:
    w.sync()
    return [a.text() for _, _, a in w._tool_actions if a.isChecked()]


def test_toolbar(window: Window) -> None:
    buttons = [b for b in window.tools.findChildren(QToolButton) if b.defaultAction()]
    toggles = ["Snap", "Local", "Show collision"]
    assert [b.text() for b in buttons] == [*LABELS, *toggles]
    assert [b.text() for b in buttons if b.property("toggle")] == toggles
    assert len([w for w in window.tools.findChildren(QWidget) if w.objectName() == "Seg"]) == 2
    keys = [action(window, t).shortcut().toString() for t in LABELS]
    assert keys == ["Q", "W", "E", "R", "1", "2", "3", "4"]
    assert "(W)" in action(window, "Move").toolTip()
    assert all(not a.icon().isNull() for _, _, a in window._tool_actions)
    assert checked(window) == ["Select", "Objects"]


def test_trigger(window: Window) -> None:
    ws = window.studio.active
    assert isinstance(ws, MapWorkspace)
    action(window, "Scale").trigger()
    action(window, "Faces").trigger()
    assert (ws.tools.tool, ws.tools.kind) == (SCALE, FACE)
    assert checked(window) == ["Scale", "Faces"]
    action(window, "Snap").trigger()
    assert ws.tools.snap and checked(window) == ["Scale", "Faces", "Snap"]
    action(window, "Snap").trigger()
    assert not ws.tools.snap


def test_collision_key(
    make_window: Callable[..., Window], game: Extracted, atlas: Atlas, qtbot: Any
) -> None:
    gl_or_skip()
    ws = MapWorkspace(game, atlas)
    w = make_window(ws)
    qtbot.waitUntil(lambda: ws.vp is not None, timeout=5000)
    w.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is w)
    w.view.setFocus()
    assert ws.vp is not None and action(w, "Show collision").shortcut().toString() == "C"
    qtbot.keyClick(w.view, Qt.Key.Key_C)
    assert ws.vp.show_collision and "Show collision" in checked(w)
    qtbot.keyClick(w.view, Qt.Key.Key_C)
    assert not ws.vp.show_collision
    qtbot.keyClick(w.view, Qt.Key.Key_4)
    assert ws.vp.show_collision and checked(w) == ["Select", "Collision", "Show collision"]
    qtbot.keyClick(w.view, Qt.Key.Key_2)
    assert not ws.vp.show_collision and checked(w) == ["Select", "Objects"]


def test_follows_the_workspace(window: Window) -> None:
    ws = window.studio.active
    ws.set_tool(TOOL, MOVE)
    ws.set_tool(PICK, COLLISION)
    assert checked(window) == ["Move", "Collision"]


def test_shortcuts(
    make_window: Callable[..., Window], game: Extracted, atlas: Atlas, qtbot: Any
) -> None:
    ws = MapWorkspace(game, atlas)
    ws.load_stage(139)  # with nothing loaded the start page shows, and no tools
    w = make_window(ws)
    w.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is w)
    w.view.setFocus()
    qtbot.keyClick(w.view, Qt.Key.Key_E)
    qtbot.keyClick(w.view, Qt.Key.Key_3)
    assert (ws.tools.tool, ws.tools.kind) == ("rotate", FACE)
