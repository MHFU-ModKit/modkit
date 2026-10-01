# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from mhfu_studio.shell.findings import Finding
from mhfu_studio.shell.studio import Studio
from mhfu_studio.shell.testing import FakeDocument
from mhfu_studio.ui import chrome, dialogs, kit, theme
from mhfu_studio.ui.testing import FakeWorkspace, gl_or_skip
from mhfu_studio.ui.window import Window
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QDockWidget, QMainWindow

Make = Callable[..., Window]
LEFT, RIGHT = Qt.DockWidgetArea.LeftDockWidgetArea, Qt.DockWidgetArea.RightDockWidgetArea


def dock(w: Window, name: str) -> QDockWidget:
    d = w.findChild(QDockWidget, name)
    assert d is not None, name
    return d


def switch(w: Window, name: str) -> None:
    w.studio.switch(name)
    w.sync()


def test_layout_per_workspace(make_window: Make) -> None:
    w = make_window()
    items, notes = dock(w, "map/Items"), dock(w, "map/Notes")
    assert items.isVisible() and w.dockWidgetArea(items) == LEFT
    w.addDockWidget(RIGHT, items)
    notes.close()
    switch(w, "monster")
    assert not items.isVisible() and dock(w, "monster/Notes").isVisible()
    assert w.dockWidgetArea(dock(w, "monster/Items")) == LEFT
    switch(w, "map")
    assert items.isVisible() and w.dockWidgetArea(items) == RIGHT and not notes.isVisible()
    assert not dock(w, "monster/Items").isVisible() and dock(w, "Findings").isVisible()


def test_layout_persists(make_window: Make) -> None:
    w = make_window()
    w.addDockWidget(RIGHT, dock(w, "map/Items"))
    w.close()
    again = make_window()
    assert again.dockWidgetArea(dock(again, "map/Items")) == RIGHT


def test_reset_layout(make_window: Make) -> None:
    w = make_window()
    items, notes = dock(w, "map/Items"), dock(w, "map/Notes")
    w.addDockWidget(RIGHT, items)
    notes.close()
    dock(w, "Findings").close()
    w.reset_action.trigger()
    assert w.dockWidgetArea(items) == LEFT and w.dockWidgetArea(notes) == RIGHT
    assert notes.isVisible() and dock(w, "Findings").isVisible()


def test_theme_menu(make_window: Make) -> None:
    w = make_window()
    pick = {a.data(): a for a in [*w.family_actions, *w.mode_actions]}
    pick["Moss"].trigger()
    pick["light"].trigger()
    assert (theme.current().family, theme.current().dark) == ("Moss", False)
    assert w.settings.value("theme/family") == "Moss" and w.settings.value("theme/mode") == "light"
    pick["Ember"].trigger()
    pick["dark"].trigger()
    assert theme.current().name == "Ember dark"
    w.settings.setValue("theme/family", "Moss")
    again = make_window(show=False)
    assert theme.current().name == "Moss dark"
    assert [a.data() for a in again.family_actions + again.mode_actions if a.isChecked()] == [
        "Moss",
        "dark",
    ]


def test_tool_shortcuts(make_window: Make, qtbot: Any) -> None:
    w = make_window()
    w.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is w)
    ws = w.studio.active
    w.view.setFocus()
    qtbot.keyClick(w.view, Qt.Key.Key_W)
    qtbot.keyClick(w.view, Qt.Key.Key_S)
    assert (ws.tool, ws.snap) == ("move", True)
    w.sync()
    assert [a.text() for _, _, a in w._tool_actions if a.isChecked()] == ["Move", "Snap"]
    notes = ws.built["Notes"]
    for field in (notes.field, notes.number):
        field.setFocus()
        qtbot.keyClick(field, Qt.Key.Key_Q)
        qtbot.keyClick(field, Qt.Key.Key_S)
    assert (ws.tool, ws.snap, notes.field.text()) == ("move", True, "qs")


def test_toolbar_follows_workspace(make_window: Make) -> None:
    w = make_window()
    move = next(a for _, t, a in w._tool_actions if t == "move")
    move.trigger()
    assert w.studio.active.tool == "move"
    switch(w, "monster")
    assert [a.isChecked() for _, t, a in w._tool_actions if t == "select"] == [True]


def test_undo_menu(make_window: Make, qtbot: Any) -> None:
    w = make_window()
    ws = w.studio.active
    qtbot.mouseClick(ws.built["Items"].add, Qt.MouseButton.LeftButton)
    w.sync()
    assert ws.doc.history.value == ["item0"] and w.undo_action.isEnabled()
    assert w.bar.chip.text() and ws.built["Items"].list.count() == 1
    w.undo_action.trigger()
    w.sync()
    assert ws.doc.history.value == [] and not w.undo_action.isEnabled()
    assert w.redo_action.isEnabled()


def test_close_asks(make_window: Make, asked: list[Any], monkeypatch: Any, tmp_path: Path) -> None:
    w = make_window()
    w.studio.active.add_item()
    monkeypatch.setattr(
        dialogs, "confirm_unsaved", lambda p, names: asked.append(names) or "cancel"
    )
    assert not w.close() and w.isVisible()
    assert asked == [["untitled map"]]
    monkeypatch.setattr(dialogs, "confirm_unsaved", lambda p, names: "save")
    monkeypatch.setattr(dialogs, "ask_save_as", lambda p, s: tmp_path / "a.toml")
    assert w.close() and not w.isVisible()
    assert w.studio.workspace("map").doc.saved_to == [tmp_path / "a.toml"]


def test_open_asks(make_window: Make, asked: list[Any], monkeypatch: Any, tmp_path: Path) -> None:
    w = make_window()
    w.studio.active.add_item()
    monkeypatch.setattr(dialogs, "ask_open", lambda *a: tmp_path / "b.toml")
    monkeypatch.setattr(dialogs, "confirm_unsaved", lambda p, n: asked.append(n) or "cancel")
    (tmp_path / "b.toml").write_text("x")
    w.open_action.trigger()
    assert asked == [["untitled map"]] and w.studio.active.doc.path is None


def test_refusals_reach_the_status_bar(make_window: Make) -> None:
    w = make_window()
    ws = w.studio.active
    w.studio.act("move", lambda: setattr(ws, "message", "move refused: no room"))()
    w.sync()
    assert w.message.text() == "move refused: no room"


def test_take_focus(make_window: Make) -> None:
    w = make_window()
    ws = w.studio.active
    items = dock(w, "map/Items")
    items.close()
    ws.focus = "Items"
    w.studio.changed()
    w.sync()
    assert items.isVisible() and ws.focus is None


def test_hidden_docks_skip_sync(make_window: Make) -> None:
    w = make_window()
    notes = dock(w, "map/Notes")
    panel = w.studio.active.built["Notes"]
    notes.close()
    n = panel.syncs
    w.sync()
    assert panel.syncs == n
    notes.show()
    assert panel.syncs == n + 1


def test_findings(make_window: Make) -> None:
    w = make_window()
    ws = w.studio.active
    ws.doc = FakeDocument()
    ws.doc.found = [Finding("error", "x", "bad", target=3), Finding("info", "y", "fyi")]
    w.studio.findings.stale()
    w.sync()
    panel = w.findings
    assert panel.list.count() == 2 and panel.pills["error"].text() == "1 error"
    assert panel.pills["warning"].isHidden()
    panel.list.itemClicked.emit(panel.list.item(0))
    assert ws.log[-1] == ("reveal", 3)
    ws.doc = None
    w.sync()
    assert panel.stack.currentWidget() is panel.empty


def test_title_bar_switches(make_window: Make, qtbot: Any) -> None:
    w = make_window()
    qtbot.mouseClick(w.bar.switcher.buttons["monster"], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: w._shown == "monster")
    assert w.studio.active.name == "monster" and dock(w, "monster/Items").isVisible()
    assert [a.data() for a in w.workspace_actions if a.isChecked()] == ["monster"]


def menu_actions(actions: list[QAction]) -> list[QAction]:
    out = []
    for a in actions:
        if a.menu() is not None:
            out += menu_actions(a.menu().actions())
        elif not a.isSeparator():
            out.append(a)
    return out


def test_tips(make_window: Make, qtbot: Any) -> None:
    w = make_window()
    assert kit.missing_tips(w) == []
    bare = [a.text() for a in menu_actions(w.bar.menus.actions()) if a.toolTip() == a.text()]
    assert bare == []
    host = QMainWindow()
    qtbot.addWidget(host)
    bar = chrome.TitleBar(host, Studio([FakeWorkspace()]), native=False)
    assert len(bar.buttons) == 3 and kit.missing_tips(bar) == []


def test_draws(make_window: Make, qtbot: Any) -> None:
    gl_or_skip()
    w = make_window()
    ws = w.studio.active
    qtbot.waitUntil(lambda: ws.vp is not None, timeout=5000)
    img = w.view.grabFramebuffer()
    assert w.studio.error is None and w.studio.errors == []
    colours = {img.pixel(x, y) for x in range(0, img.width(), 7) for y in range(0, img.height(), 7)}
    assert len(colours) > 20, "the viewport is flat: nothing was drawn"
    qtbot.waitUntil(lambda: bool(w.renderer.text()))


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_viewport_follows_theme(make_window: Make, qtbot: Any, mode: str) -> None:
    gl_or_skip()
    w = make_window()
    w.set_theme(family="Moss", mode=mode)
    ws = w.studio.active
    qtbot.waitUntil(lambda: ws.vp is not None, timeout=5000)
    w.view.grabFramebuffer()
    assert ws.vp.background == theme.current().view
