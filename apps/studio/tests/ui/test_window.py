# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from mhfu_studio.shell.findings import Finding
from mhfu_studio.shell.studio import Studio
from mhfu_studio.shell.testing import FakeDocument
from mhfu_studio.shell.workspace import Dock
from mhfu_studio.ui import about, chrome, dialogs, kit, theme
from mhfu_studio.ui.testing import FakeWorkspace, elsewhere, gl_or_skip
from mhfu_studio.ui.window import STATE_VERSION, DockTitle, Window
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QOpenGLContext
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QTabBar,
    QWidget,
)
from shiboken6 import getCppPointer

Make = Callable[..., Window]
LEFT, RIGHT = Qt.DockWidgetArea.LeftDockWidgetArea, Qt.DockWidgetArea.RightDockWidgetArea
BOTTOM = Qt.DockWidgetArea.BottomDockWidgetArea


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
    assert not dock(w, "monster/Items").isVisible() and not dock(w, "Findings").isVisible()


def test_layout_persists(make_window: Make) -> None:
    w = make_window()
    w.addDockWidget(RIGHT, dock(w, "map/Items"))
    w.close()
    again = make_window()
    assert again.dockWidgetArea(dock(again, "map/Items")) == RIGHT


class Tabbed(FakeWorkspace):
    """Two docks a side, so every side is a tab group."""

    def docks(self) -> tuple[Dock, ...]:
        own = tuple(super().docks())
        more = (Dock(f"{d.label} 2", d.area, lambda s: kit.Panel(), d.tip) for d in own)
        return (*own, *more)


def stray_tabs(w: Window) -> list[list[str]]:
    """The tabs of every shown tab bar over the title bar, the toolbar or the view."""
    others = [w.menuWidget(), w.tools, w.centralWidget()]
    bars = w.findChildren(QTabBar, options=Qt.FindChildOption.FindDirectChildrenOnly)
    return [
        [b.tabText(i) for i in range(b.count())]
        for b in bars
        if b.isVisible()
        and any(o.isVisible() and b.geometry().intersects(o.geometry()) for o in others)
    ]


def test_arranged_once(make_window: Make, qtbot: Any, monkeypatch: Any) -> None:
    arranged: list[str] = []
    arrange = Window._arrange
    monkeypatch.setattr(Window, "_arrange", lambda w, n: arranged.append(n) or arrange(w, n))
    w = make_window(Tabbed("map"), Tabbed("monster"))
    switch(w, "monster")
    switch(w, "map")
    assert arranged == ["map", "monster"]
    w.close()
    again = make_window(Tabbed("map"), Tabbed("monster"))
    switch(again, "monster")
    switch(again, "map")
    assert arranged == ["map", "monster"] and dock(again, "map/Items 2").isVisible()
    again.reset_layout()
    qtbot.wait(20)  # Qt deletes the tab bars a layout let go of on the loop's next turn
    assert arranged[-1] == "map" and stray_tabs(again) == []


class Closed(FakeWorkspace):
    """Items and Notes open; Extra closed on the right; Strip alone at the bottom, More closed."""

    def docks(self) -> tuple[Dock, ...]:
        def blank(s: Studio) -> kit.Panel:
            return kit.Panel()

        return (
            *super().docks(),
            Dock("Extra", "right", blank, "A closed panel", shown=False),
            Dock("Strip", "bottom", blank, "A panel never tabbed", alone=True, size=90),
            Dock("More", "bottom", blank, "Another closed panel", shown=False),
        )


def test_closed_docks_open_in_place(make_window: Make, qtbot: Any) -> None:
    w = make_window(Closed("map"), Closed("monster"))
    extra, more, strip = dock(w, "map/Extra"), dock(w, "map/More"), dock(w, "map/Strip")
    assert strip.isVisible() and not extra.isVisible() and not more.isVisible()
    assert extra.toggleViewAction() in w.panels_menu.actions()
    extra.toggleViewAction().trigger()
    more.toggleViewAction().trigger()
    dock(w, "Findings").toggleViewAction().trigger()
    qtbot.wait(20)
    assert w.dockWidgetArea(extra) == RIGHT and w.tabifiedDockWidgets(extra) == [
        dock(w, "map/Notes")
    ]
    assert not extra.visibleRegion().isEmpty()
    assert w.dockWidgetArea(more) == BOTTOM and w.tabifiedDockWidgets(strip) == []
    assert dock(w, "Findings") in w.tabifiedDockWidgets(more)
    assert more.geometry().bottom() < strip.geometry().top() and abs(strip.height() - 90) < 12


def test_take_focus_opens_a_closed_dock(make_window: Make, qtbot: Any) -> None:
    w = make_window(Closed("map"), Closed("monster"))
    more, strip = dock(w, "map/More"), dock(w, "map/Strip")
    w.studio.active.focus = "More"
    w.studio.changed()
    w.sync()
    qtbot.wait(20)
    assert more.isVisible() and more.height() > 2 * strip.height()


def test_old_layouts_are_dropped(make_window: Make) -> None:
    w = make_window()
    w.addDockWidget(RIGHT, dock(w, "map/Items"))
    old = w.saveState(STATE_VERSION - 1)
    w.close()
    w.settings.setValue("layout/map", old)
    again = make_window()
    assert again.dockWidgetArea(dock(again, "map/Items")) == LEFT


def test_restore_places_new_docks(make_window: Make) -> None:
    make_window().close()
    w = make_window(Tabbed("map"), Tabbed("monster"))
    assert w.dockWidgetArea(dock(w, "map/Notes 2")) == RIGHT


def test_reset_layout(make_window: Make) -> None:
    w = make_window()
    items, notes = dock(w, "map/Items"), dock(w, "map/Notes")
    w.addDockWidget(RIGHT, items)
    notes.close()
    dock(w, "Findings").show()
    w.reset_action.trigger()
    assert w.dockWidgetArea(items) == LEFT and w.dockWidgetArea(notes) == RIGHT
    assert notes.isVisible() and not dock(w, "Findings").isVisible()


def test_tabbed_docks_show_only_their_tab(make_window: Make, qtbot: Any) -> None:
    w = make_window()
    items, notes = dock(w, "map/Items"), dock(w, "map/Notes")
    titled = [d for d in (items, notes) if isinstance(d.titleBarWidget(), DockTitle)]
    assert titled == [items, notes]
    w.tabifyDockWidget(items, notes)
    w.sync()
    assert not isinstance(items.titleBarWidget(), DockTitle)
    assert not isinstance(notes.titleBarWidget(), DockTitle)
    notes.close()
    qtbot.waitUntil(lambda: isinstance(items.titleBarWidget(), DockTitle))
    notes.show()
    qtbot.waitUntil(lambda: not isinstance(items.titleBarWidget(), DockTitle))
    items.setFloating(True)
    qtbot.waitUntil(lambda: isinstance(items.titleBarWidget(), DockTitle))


def tab_menu(w: Window, d: QDockWidget) -> QMenu:
    """The menu a right-click on `d`'s tab opens."""
    w.sync()
    me = getCppPointer(d)[0]
    for bar in w.findChildren(QTabBar):
        for i in range(bar.count()):
            if bar.tabData(i) == me:
                bar.customContextMenuRequested.emit(bar.tabRect(i).center())
                m = QApplication.activePopupWidget()
                assert isinstance(m, QMenu)
                return m
    raise AssertionError(f"{d.objectName()} has no tab")


def test_tab_menu(make_window: Make, qtbot: Any) -> None:
    w = make_window()
    items, notes = dock(w, "map/Items"), dock(w, "map/Notes")
    w.tabifyDockWidget(items, notes)
    m = tab_menu(w, notes)
    m.actions()[0].trigger()
    m.close()
    assert notes.isHidden() and not notes.toggleViewAction().isChecked()
    assert notes.toggleViewAction() in w.panels_menu.actions()
    notes.show()
    w.addDockWidget(RIGHT, items)
    w.addDockWidget(RIGHT, notes)
    w.tabifyDockWidget(notes, items)
    m = tab_menu(w, items)
    m.actions()[0].trigger()
    m.close()
    assert items.isHidden() and notes.isVisible()
    items.show()
    switch(w, "monster")
    switch(w, "map")  # a saved layout comes back: new tab bars
    assert w.tabifiedDockWidgets(notes) == [items]
    m = tab_menu(w, notes)
    m.actions()[1].trigger()
    m.close()
    qtbot.waitUntil(lambda: isinstance(notes.titleBarWidget(), DockTitle))
    assert notes.isFloating() and kit.missing_tips(w) == []
    qtbot.mouseDClick(notes.titleBarWidget(), Qt.MouseButton.LeftButton)
    assert not notes.isFloating()


def test_default_shares(make_window: Make) -> None:
    w = make_window()
    w.resize(1200, 800)
    w.reset_layout()
    dock(w, "Findings").toggleViewAction().trigger()
    QApplication.processEvents()
    side, bottom = dock(w, "map/Items").width(), dock(w, "Findings").height()
    assert 0.18 < side / w.width() < 0.26 and 0.28 < bottom / w.height() < 0.36


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
    monkeypatch.setattr(dialogs, "confirm_unsaved", lambda p, n: "discard")  # teardown closes


def test_revert_menu(make_window: Make, asked: list[Any], tmp_path: Path) -> None:
    w = make_window()
    (tmp_path / "a.toml").write_text("x y")
    assert w.studio.open(tmp_path / "a.toml")
    ws = w.studio.active
    w.sync()
    assert not w.revert_action.isEnabled()
    ws.add_item()
    w.sync()
    assert w.revert_action.isEnabled()
    w.revert_action.trigger()
    w.sync()
    assert asked == ["a.toml"] and ws.doc.history.value == ["x", "y"]
    assert not w.revert_action.isEnabled() and w.message.text() == "back to the saved a.toml"


def test_revert_question(qtbot: Any) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    box = dialogs.revert_box(parent, "a.toml")
    roles = {box.buttonRole(b): b.text() for b in box.buttons()}
    cancel = box.button(QMessageBox.StandardButton.Cancel)
    assert box.defaultButton() is cancel and box.escapeButton() is cancel
    assert roles[QMessageBox.ButtonRole.DestructiveRole] == "Revert" and "a.toml" in box.text()


def test_refusals_reach_the_status_bar(make_window: Make) -> None:
    w = make_window()
    ws = w.studio.active
    w.studio.act("move", lambda: setattr(ws, "message", "move refused: no room"))()
    w.sync()
    assert w.message.text() == "move refused: no room"


def test_status_line(make_window: Make, monkeypatch: Any) -> None:
    w = make_window()
    ws = w.studio.active
    assert w.hint.text() == "select: Delete removes"
    monkeypatch.setattr(ws, "send_blocker", lambda: None)
    ws.set_tool("tool", "move")
    w.sync()
    send = about.native(QKeySequence("Ctrl+Return"))
    assert w.hint.text() == f"move: Delete removes \u00b7 {send} send to game"


def test_problems(make_window: Make) -> None:
    w = make_window()
    ws = w.studio.active
    ws.doc = FakeDocument()
    w.sync()
    assert w.problems.isHidden()
    ws.doc.found = [Finding("warning", "w", "odd"), Finding("info", "i", "fyi")]
    w.studio.findings.stale()
    w.sync()
    assert w.problems.text() == "1 problem" and w.problems.property("level") == "warning"
    ws.doc.found.append(Finding("error", "e", "bad"))
    w.studio.findings.stale()
    w._recheck()
    assert w.problems.text() == "2 problems" and w.problems.property("level") == "error"
    w.problems.click()
    assert w.findings_dock.isVisible() and w.findings.list.count() == 3


def test_help(make_window: Make) -> None:
    w = make_window()
    keys = {(where, k): does for where, k, does in about.rows(w.studio, w.bar.menus.actions())}
    assert keys["File menu", about.native(QKeySequence.StandardKey.Save)] == "Save"
    assert keys["Map toolbar", "W"] == "Tool: Move" and ("Monster toolbar", "W") in keys
    assert keys["Map view", "Del"] == "Removes the picked item"
    assert ("The view", "Wheel") in keys
    table = w.show_shortcuts().findChild(kit.Table)
    assert table is not None and table.rowCount() == len(keys)
    box = w.show_about()
    assert "0 items" in [lb.text() for lb in box.findChildren(QLabel)]
    for d in (box, table.window()):
        assert kit.missing_tips(d) == []
        d.close()


def test_camera_readout(make_window: Make) -> None:
    w = make_window()
    assert not w.view.show_camera and not w.camera_action.isChecked()
    w.camera_action.trigger()
    assert w.view.show_camera
    w.close()
    assert make_window().view.show_camera


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
    w.set_theme(family="Ember", mode="dark")
    ws = w.studio.active
    ws.doc = FakeDocument()
    ws.doc.found = [Finding("error", "x", "bad", target=3), Finding("info", "y", "fyi")]
    w.studio.findings.stale()
    w.findings_dock.show()
    w.sync()
    panel = w.findings
    assert panel.list.count() == 2 and panel.pills["error"].text() == "1 error"
    first = panel.list.item(0)
    assert first.text() == "bad" and first.toolTip().startswith("error: x")
    assert panel.pills["warning"].isHidden()
    w.set_theme(family="Moss", mode="light")
    assert panel.list.item(0).foreground().color() == theme.level("error")
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


@pytest.mark.parametrize("native", [True, False])
def test_window_title(make_window: Make, monkeypatch: Any, qtlog: Any, native: bool) -> None:
    monkeypatch.setattr(chrome, "MAC", native)
    w = make_window()
    w.studio.active.add_item()
    w.sync()
    assert w.windowTitle() == ("" if native else "untitled map[*] - MHFU Studio")
    assert w.bar.doc.text() == "untitled map" and w.bar.chip.text()
    assert not [r for r in qtlog.records if "[*]" in r.message]


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
    menus = menu_actions(w.bar.menus.actions())
    assert [a.text() for a in menus if a.toolTip() == a.text()] == []
    assert [a.text() for a in menus if a.statusTip()] == []
    labels = [lb for t in w.findChildren(DockTitle) for lb in t.findChildren(QLabel)]
    assert labels and not [lb for lb in labels if lb.toolTip()]
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
    qtbot.waitUntil(lambda: bool(w.studio.renderer))


def test_view_follows_resize(make_window: Make, qtbot: Any) -> None:
    gl_or_skip()
    w = make_window()
    ws, v = w.studio.active, w.view
    qtbot.waitUntil(lambda: ws.vp is not None, timeout=5000)

    def follows() -> bool:
        v.grabFramebuffer()
        return bool(ws.vp.target.size == (v.size() * v.devicePixelRatioF()).toTuple())

    for size in ((700, 500), (1200, 800)):
        before = v.size()
        w.resize(*size)
        qtbot.waitUntil(lambda: v.size() != before)  # noqa: B023
        qtbot.waitUntil(follows)


def test_actions_run_in_the_view_context(make_window: Make, qtbot: Any) -> None:
    """Whatever context a click left current, an action's GL objects are the view's."""
    gl_or_skip()
    w = make_window()
    ws = w.studio.active
    qtbot.waitUntil(lambda: ws.vp is not None, timeout=5000)
    seen: list[Any] = []
    with elsewhere():
        w.studio.act("look", lambda: seen.append(QOpenGLContext.currentContext()))()
    assert seen == [w.view.context()]
    img = w.view.grabFramebuffer()  # a guard inside the paint leaves its context alone
    colours = {img.pixel(x, y) for x in range(0, img.width(), 7) for y in range(0, img.height(), 7)}
    assert len(colours) > 20 and w.studio.errors == []


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_viewport_follows_theme(make_window: Make, qtbot: Any, mode: str) -> None:
    gl_or_skip()
    w = make_window()
    w.set_theme(family="Moss", mode=mode)
    ws = w.studio.active
    qtbot.waitUntil(lambda: ws.vp is not None, timeout=5000)
    w.view.grabFramebuffer()
    assert ws.vp.background == theme.current().view
