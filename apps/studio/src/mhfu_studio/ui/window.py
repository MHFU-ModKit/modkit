# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The main window: the viewport card, each workspace's docks in its own saved layout, the
toolbar, the menus, the title bar and the status bar.

Every change ends in `studio.changed()`; the window turns that into one `sync()` per
event-loop turn, which re-reads everything it shows. Nothing else updates a control.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QByteArray, QPoint, QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QHBoxLayout,
    QMainWindow,
    QMenu,
    QSizePolicy,
    QTabBar,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import getCppPointer

from mhfu_studio.shell.studio import doc_name
from mhfu_studio.shell.text import plain
from mhfu_studio.shell.workspace import Dock, Tool, ToolGroup, Workspace
from mhfu_studio.ui import chrome, dialogs, kit, theme
from mhfu_studio.ui.findings import FindingsPanel
from mhfu_studio.ui.job import JobLog, ProcessRunner
from mhfu_studio.ui.view import GLView

if TYPE_CHECKING:
    from mhfu_studio.shell.studio import Studio

#: bump when the docks change shape, so an old saved layout is not forced onto new docks
STATE_VERSION = 1
FINDINGS = "Findings"
FINDINGS_TIP = "What the checks found in the document: errors, warnings and notes"
AREAS = {
    "left": Qt.DockWidgetArea.LeftDockWidgetArea,
    "right": Qt.DockWidgetArea.RightDockWidgetArea,
    "bottom": Qt.DockWidgetArea.BottomDockWidgetArea,
}
#: the share of the window each side's docks take in a fresh layout
SHARES = {"left": 0.22, "right": 0.22, "bottom": 0.32}
FAMILIES = {
    "Ember": "Warm: orange on dark brown, or burnt orange on cream",
    "Moss": "Cool: green on dark pine, or deep green on mint",
}
MODES: dict[theme.Mode, tuple[str, str]] = {
    "system": ("Match system", "Dark or light as the system is, following it when it changes"),
    "dark": ("Dark", "Always dark, whatever the system is set to"),
    "light": ("Light", "Always light, whatever the system is set to"),
}


class DockTitle(QWidget):
    """A dock's title: its name in small caps, which explains the dock, and a close button.
    Tabbed with others a dock shows only its tab, whose right-click menu hides it."""

    def __init__(self, dock: QDockWidget, tip: str) -> None:
        super().__init__()
        name = dock.windowTitle()
        self.setToolTip(tip)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 6, 6, 2)
        label = kit.label(name.upper(), role="caps", wrap=False)
        label.setToolTip(tip)
        lay.addWidget(label)
        lay.addStretch(1)
        self.hide_button = kit.icon_button(
            "ph.x", tip=f"Hides {name}; View > {name} brings it back", on=dock.close
        )
        self.hide_button.setIconSize(QSize(12, 12))
        lay.addWidget(self.hide_button)


class Window(QMainWindow):
    def __init__(self, studio: Studio, settings: QSettings | None = None) -> None:
        super().__init__()
        self.studio = studio
        self.settings = settings if settings is not None else QSettings()
        self._closed = False
        self._theming = False
        #: per dock: its title bar, and the blank one it wears while tabbed
        self._titles: dict[QDockWidget, tuple[DockTitle, QWidget]] = {}
        self._retitling = QTimer(self)
        self._retitling.setSingleShot(True)
        self._retitling.setInterval(0)
        self._retitling.timeout.connect(self._retitle)
        #: read here, so a test can draw the other platforms' chrome
        self.native = native = chrome.MAC
        chrome.frame(self, native)
        self.setDockOptions(
            QMainWindow.DockOption.AnimatedDocks
            | QMainWindow.DockOption.AllowTabbedDocks
            | QMainWindow.DockOption.AllowNestedDocks
        )
        self.setTabPosition(Qt.DockWidgetArea.AllDockWidgetAreas, QTabWidget.TabPosition.North)
        # the stock menu lists every dock, other workspaces' too; the View menu is ours
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)

        self.view = GLView(studio)
        card = QWidget()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(4, 0, 4, 2)
        lay.addWidget(self.view)
        self.setCentralWidget(card)
        self.bar = chrome.TitleBar(self, studio, native)
        self.setMenuWidget(self.bar)

        self.tools = QToolBar("Tools")
        self.tools.setObjectName("Tools")
        self.tools.setMovable(False)
        self.tools.setIconSize(QSize(16, 16))
        self.tools.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(self.tools)
        #: per workspace: the toolbar's actions, and (group, tool, action) for each tool
        self._tool_sets: dict[str, tuple[list[QAction], list[tuple[str, str, QAction]]]] = {}
        self._tool_actions: list[tuple[str, str, QAction]] = []

        self.findings = FindingsPanel(studio, ask_open=studio.act("open", self.ask_open))
        self.findings_dock = self._dock(FINDINGS, FINDINGS, self.findings, FINDINGS_TIP)
        self._docks: dict[str, list[QDockWidget]] = {}
        self._specs: dict[QDockWidget, Dock] = {}
        self._panels: dict[QDockWidget, Any] = {self.findings_dock: self.findings}
        self._shown: str | None = None

        studio.runner = self.runner = ProcessRunner(studio, self)
        self._log: JobLog | None = None
        self._menus()
        self.bar.send.setDefaultAction(self.send_action)
        status = self.statusBar()
        status.setSizeGripEnabled(False)
        self.log_button = kit.icon_button(
            "ph.terminal-window",
            tip="Shows what Send to game printed, with Stop and Copy",
            on=self.show_log,
        )
        self.message = kit.label(wrap=False)
        self.message.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.where = kit.label(wrap=False)
        self.renderer = kit.label(wrap=False)
        status.addWidget(self.log_button)
        status.addWidget(self.message, 1)
        status.addPermanentWidget(self.where)
        status.addPermanentWidget(self.renderer)

        self._pending = QTimer(self)
        self._pending.setSingleShot(True)
        self._pending.setInterval(0)
        self._pending.timeout.connect(self.sync)
        self._slow = QTimer(self)
        self._slow.setInterval(250)
        self._slow.timeout.connect(self._recheck)
        self._slow.start()

        family = str(self.settings.value("theme/family", "Ember"))
        mode = str(self.settings.value("theme/mode", "system"))
        self.family = family if family in theme.FAMILIES else "Ember"
        self.mode: theme.Mode = next((m for m in theme.MODES if m == mode), "system")
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._scheme_changed)
        self._apply_theme()

        studio.ask_discard = lambda names: dialogs.confirm_unsaved(self, names)
        studio.ask_path = lambda ws: dialogs.ask_save_as(self, ws)
        geo = self.settings.value("geometry")
        if not (isinstance(geo, QByteArray) and self.restoreGeometry(geo)):
            self.resize(1500, 940)
        studio.listen(self._schedule)
        self.sync()

    # ---- docks ----------------------------------------------------------------------- #

    def _dock(self, name: str, label: str, widget: QWidget, tip: str) -> QDockWidget:
        d = QDockWidget(label, self)
        d.setObjectName(name)  # saveState keys on it
        d.setAllowedAreas(AREAS["left"] | AREAS["right"] | AREAS["bottom"])
        d.setWidget(widget)
        title = DockTitle(d, tip)
        self._titles[d] = (title, QWidget())
        d.setTitleBarWidget(title)
        d.toggleViewAction().setToolTip(tip)
        d.visibilityChanged.connect(partial(self._dock_shown, d))
        d.topLevelChanged.connect(self._retitle_soon)
        d.dockLocationChanged.connect(self._retitle_soon)
        return d

    def _dock_shown(self, d: QDockWidget, visible: bool) -> None:
        self._retitle_soon()
        if visible:
            self._sync_panel(d)

    def _retitle_soon(self, *_: object) -> None:
        """After the layout settles: a tab moved, a dock closed, floated or docked."""
        if not self._closed:
            self._retitling.start()

    def _retitle(self) -> None:
        """A dock alone in its area or floating wears its title bar; tabbed, only its tab."""
        for d, (title, blank) in self._titles.items():
            tabbed = not d.isFloating() and bool(self.tabifiedDockWidgets(d))
            want = blank if tabbed else title
            if d.titleBarWidget() is not want:
                d.setTitleBarWidget(want)
        # the dock tab bars: Qt makes a new one for every tab group
        for bar in self.findChildren(QTabBar, options=Qt.FindChildOption.FindDirectChildrenOnly):
            if bar.contextMenuPolicy() != Qt.ContextMenuPolicy.CustomContextMenu:
                bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                bar.customContextMenuRequested.connect(partial(self._tab_menu, bar))

    def _tab_menu(self, bar: QTabBar, pos: QPoint) -> None:
        """A dock tab's right-click menu: its View menu entry, which hides it, and Float."""
        docks = {getCppPointer(d)[0]: d for d in self._titles}
        d = docks.get(bar.tabData(bar.tabAt(pos)))  # Qt keys a tab by its dock's address
        if d is None:
            return
        name = d.windowTitle()
        m = QMenu(self)
        m.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        m.setToolTipsVisible(True)
        m.addAction(d.toggleViewAction())
        a = m.addAction(f"Float {name}")
        a.setToolTip(f"Takes {name} out into its own window; double-click its title to dock it")
        a.triggered.connect(lambda: d.setFloating(True))
        m.popup(bar.mapToGlobal(pos))

    def _build(self, ws: Workspace) -> list[QDockWidget]:
        out = []
        for spec in ws.docks():
            widget = self._panel(spec)
            d = self._dock(f"{ws.name}/{spec.label}", spec.label, widget, spec.tip)
            self._specs[d] = spec
            self._panels[d] = widget
            out.append(d)
        return out

    def _panel(self, spec: Dock) -> Any:
        """`spec`'s widget, or a note saying why there is none."""
        built: list[Any] = []
        self.studio.guard(f"{spec.label} panel", lambda: built.append(spec.build(self.studio)))()
        if built:
            return built[0]
        p = kit.Panel()
        p.body.addWidget(kit.label(f"{spec.label} could not be built: {self.studio.message}"))
        return p

    def _area(self, d: QDockWidget) -> str:
        """`d`'s side in the built-in arrangement."""
        spec = self._specs.get(d)
        return spec.area if spec is not None else "bottom"

    def _arrange(self, name: str) -> None:
        """The built-in arrangement: each side's docks tabbed together, focus docks in front."""
        sides: dict[str, list[QDockWidget]] = {}
        for d in [*self._docks[name], self.findings_dock]:
            sides.setdefault(self._area(d), []).append(d)
        for area, docks in sides.items():
            for d in docks:
                d.setFloating(False)
                self.removeDockWidget(d)
                self.addDockWidget(AREAS[area], d)
                if d is not docks[0]:
                    self.tabifyDockWidget(docks[0], d)
                d.show()
            ([d for d in docks if self._focus(d)] or docks)[0].raise_()
            across = area != "bottom"
            size = self.width() if across else self.height()
            self.resizeDocks(
                [docks[0]],
                [round(size * SHARES[area])],
                Qt.Orientation.Horizontal if across else Qt.Orientation.Vertical,
            )
        self._retitle()

    def _focus(self, d: QDockWidget) -> bool:
        spec = self._specs.get(d)
        return spec is not None and spec.focus

    def _enter(self, ws: Workspace) -> None:
        """Shows `ws`'s docks and tools in its saved layout, after saving the last one's."""
        if self._shown is not None:
            self._save_layout(self._shown)
        self._shown = ws.name
        self._hide_others()
        fresh = ws.name not in self._docks
        if fresh:
            self._docks[ws.name] = self._build(ws)
        else:
            for d in self._docks[ws.name]:
                d.show()
        # restored or arranged, never both: a replaced layout keeps its tab bars a loop turn
        if self._restore(ws.name):
            self._hide_others()  # in case a saved layout knew them as shown
        elif fresh:
            self._arrange(ws.name)
        self._retitle()
        self._fill_view_menu(ws)
        self._show_tools(ws)

    def _restore(self, name: str) -> bool:
        """`name`'s saved layout, if it has one; a dock the layout does not know joins its side."""
        state = self.settings.value(f"layout/{name}")
        if not (isinstance(state, QByteArray) and self.restoreState(state, STATE_VERSION)):
            return False
        for d in [*self._docks[name], self.findings_dock]:
            if not d.isFloating() and self.dockWidgetArea(d) == Qt.DockWidgetArea.NoDockWidgetArea:
                self.addDockWidget(AREAS[self._area(d)], d)
        return True

    def _hide_others(self) -> None:
        for name, docks in self._docks.items():
            if name != self._shown:
                for d in docks:
                    d.hide()

    def _save_layout(self, name: str) -> None:
        self.settings.setValue(f"layout/{name}", self.saveState(STATE_VERSION))

    def reset_layout(self) -> None:
        if self._shown is not None:
            self._arrange(self._shown)

    def _take_focus(self, ws: Workspace) -> None:
        label = ws.take_focus()
        d = self.findChild(QDockWidget, f"{ws.name}/{label}") if label else None
        if d is not None:
            d.show()
            d.raise_()

    # ---- toolbar --------------------------------------------------------------------- #

    def _show_tools(self, ws: Workspace) -> None:
        """`ws`'s tools on the toolbar; made once and kept, as its docks are."""
        if ws.name not in self._tool_sets:
            self._tool_sets[ws.name] = self._make_tools(ws)
        shown, self._tool_actions = self._tool_sets[ws.name]
        self.tools.clear()
        self.tools.addActions(shown)
        self.tools.setVisible(bool(shown))

    def _make_tools(self, ws: Workspace) -> tuple[list[QAction], list[tuple[str, str, QAction]]]:
        shown: list[QAction] = []
        tools: list[tuple[str, str, QAction]] = []
        for i, group in enumerate(ws.tool_groups()):
            if i:
                sep = QAction(self)
                sep.setSeparator(True)
                shown.append(sep)
            exclusive = None if group.toggles else QActionGroup(self)
            for tool in group.tools:
                a = QAction(tool.label, self)
                a.setCheckable(True)
                theme.bind(a, tool.icon)
                if tool.key:
                    a.setShortcut(QKeySequence(tool.key))
                a.setToolTip(f"{tool.tip} ({tool.key})" if tool.key else tool.tip)
                slot = partial(self._set_tool, ws, group, tool, a)
                a.triggered.connect(self.studio.act(f"tool {tool.label}", slot))
                if exclusive is not None:
                    exclusive.addAction(a)
                shown.append(a)
                tools.append((group.id, tool.id, a))
        return shown, tools

    @staticmethod
    def _set_tool(ws: Workspace, group: ToolGroup, tool: Tool, a: QAction) -> None:
        ws.set_tool(group.id, tool.id, a.isChecked() if group.toggles else True)

    # ---- menus ----------------------------------------------------------------------- #

    def _action(
        self,
        text: str,
        tip: str,
        slot: Callable[[], object],
        key: QKeySequence.StandardKey | QKeySequence | None = None,
    ) -> QAction:
        a = QAction(text, self)
        a.setToolTip(tip)
        a.setStatusTip(tip)
        if key is not None:
            a.setShortcut(QKeySequence(key))
        a.triggered.connect(self.studio.act(text.rstrip("…").lower(), slot))
        return a

    def _menu(self, title: str) -> QMenu:
        m = self.bar.menus.addMenu(title)
        m.setToolTipsVisible(True)
        return m

    def _menus(self) -> None:
        k, s = QKeySequence.StandardKey, self.studio
        f = self._menu("&File")
        self.open_action = self._action(
            "Open…", "Opens a document; the workspace that reads it comes up", self.ask_open, k.Open
        )
        self.save_action = self._action(
            "Save",
            "Writes the document back to its file; asks for one the first time",
            s.save,
            k.Save,
        )
        self.save_as_action = self._action(
            "Save As…",
            "Writes the document to a new file and goes on with that one",
            self.save_as,
            k.SaveAs,
        )
        quit_ = self._action(
            "Quit", "Closes the studio; it asks first about unsaved edits", self.close, k.Quit
        )
        # its text and tip follow the studio (`_sync_send`); Ctrl is Cmd on macOS
        self.send_action = self._action(
            "Send to game", chrome.SEND_TIP, self._send_or_stop, QKeySequence("Ctrl+Return")
        )
        self.send_action.setAutoRepeat(False)  # a held key would send, then stop
        f.addActions([self.open_action, self.save_action, self.save_as_action])
        f.addSeparator()
        f.addAction(self.send_action)
        f.addSeparator()
        f.addAction(quit_)

        e = self._menu("&Edit")
        self.undo_action = self._action("Undo", "Takes back the last edit", s.undo, k.Undo)
        self.redo_action = self._action("Redo", "Puts back what Undo took back", s.redo, k.Redo)
        e.addActions([self.undo_action, self.redo_action])

        self.view_menu = self._menu("&View")
        self.reset_action = self._action(
            "Reset layout",
            "Puts this workspace's panels back where they started",
            self.reset_layout,
        )
        self.theme_menu = QMenu("Theme", self)
        self.theme_menu.setToolTipsVisible(True)
        self.family_actions = self._radio(
            self.theme_menu,
            [(name, name, tip) for name, tip in FAMILIES.items()],
            lambda name: self.set_theme(family=name),
        )
        self.theme_menu.addSeparator()
        self.mode_actions = self._radio(
            self.theme_menu,
            [(mode, label, tip) for mode, (label, tip) in MODES.items()],
            lambda mode: self.set_theme(mode=mode),
        )

        w = self._menu("&Workspace")
        self.workspace_actions = self._radio(
            w,
            [(n, n.capitalize(), chrome.switch_tip(n)) for n in s.names],
            s.switch,
        )

    def _radio(
        self, menu: QMenu, items: list[tuple[str, str, str]], pick: Callable[[str], object]
    ) -> list[QAction]:
        """Checkable actions, one of them on; `pick(id)` on a choice."""
        group = QActionGroup(self)
        out = []
        for cid, text, tip in items:
            a = QAction(text, self)
            a.setCheckable(True)
            a.setData(cid)
            a.setToolTip(tip)
            a.setStatusTip(tip)
            a.triggered.connect(self.studio.act(f"pick {text}", partial(pick, cid)))
            group.addAction(a)
            menu.addAction(a)
            out.append(a)
        return out

    def _fill_view_menu(self, ws: Workspace) -> None:
        m = self.view_menu
        m.clear()
        docks = self._docks.get(ws.name, [])
        m.addActions([d.toggleViewAction() for d in docks])
        if docks:
            m.addSeparator()
        m.addAction(self.findings_dock.toggleViewAction())
        m.addSeparator()
        m.addAction(self.reset_action)
        m.addMenu(self.theme_menu)

    # ---- the game -------------------------------------------------------------------- #

    def _send_or_stop(self) -> None:
        if self.studio.job is not None:
            self.studio.stop()
        else:
            self.studio.send()

    def _sync_send(self) -> None:
        """Send to game sends, says why it cannot, or stops the running job."""
        a, job = self.send_action, self.studio.job
        why = None if job is not None else self.studio.send_blocker()
        key = a.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
        tip = f"Stops {job.title}" if job is not None else why or f"{chrome.SEND_TIP} ({key})"
        a.setText("Stop sending" if job is not None else "Send to game")
        a.setIconText("Stop" if job is not None else "Send to game")
        a.setToolTip(tip)
        a.setStatusTip(tip)
        a.setEnabled(why is None)
        b = self.bar.send
        if b.property("busy") != (job is not None):
            b.setProperty("busy", job is not None)
            b.style().unpolish(b)  # the stylesheet's busy rule applies on a re-polish
            b.style().polish(b)

    def show_log(self) -> JobLog:
        """The job log window, made on first use and raised."""
        if self._log is None:
            self._log = JobLog(self.studio, self)
        self._log.sync()
        self._log.show()
        self._log.raise_()
        self._log.activateWindow()
        return self._log

    # ---- theme ----------------------------------------------------------------------- #

    def set_theme(self, family: str | None = None, mode: str | None = None) -> None:
        """Applies and remembers the theme; the menu's only way in."""
        self.family = family if family in theme.FAMILIES else self.family
        self.mode = next((m for m in theme.MODES if m == mode), self.mode)
        self.settings.setValue("theme/family", self.family)
        self.settings.setValue("theme/mode", self.mode)
        self._apply_theme()

    def _apply_theme(self) -> None:
        self._theming = True
        try:
            theme.apply(theme.resolve(self.family, self.mode))
            if self.mode == "system":  # apply pins the scheme; unpinned it follows the system
                QGuiApplication.styleHints().unsetColorScheme()
        finally:
            self._theming = False
        for a in self.family_actions:
            a.setChecked(a.data() == self.family)
        for a in self.mode_actions:
            a.setChecked(a.data() == self.mode)
        self._schedule()

    def _scheme_changed(self, *_: object) -> None:
        if self._closed or self._theming or self.mode != "system":
            return
        if theme.resolve(self.family, self.mode) != theme.current():
            self._apply_theme()

    # ---- documents ------------------------------------------------------------------- #

    def ask_open(self) -> None:
        dialogs.open_document(self, self.studio)

    def save_as(self) -> bool:
        path = dialogs.ask_save_as(self, self.studio.active)
        return path is not None and self.studio.save(path)

    # ---- sync ------------------------------------------------------------------------ #

    def _schedule(self) -> None:
        """Coalesces every change of this event-loop turn into one `sync`."""
        if not self._closed and not self._pending.isActive():
            self._pending.start()

    def sync(self) -> None:
        """Re-reads everything the window shows."""
        if self._closed:
            return
        ws = self.studio.active
        if ws.name != self._shown:
            self._enter(ws)
        self.studio.guard("take focus", lambda: self._take_focus(ws))()
        for d in [*self._docks.get(ws.name, []), self.findings_dock]:
            if d.isVisible():
                self._sync_panel(d)
        self.studio.guard("sync", lambda: self._sync_window(ws))()
        self.studio.guard("send to game", self._sync_send)()
        if self._log is not None and self._log.isVisible():
            self._log.sync()
        self._retitle()
        self.view.update()

    def _sync_panel(self, d: QDockWidget) -> None:
        sync = getattr(self._panels.get(d), "sync", None)
        if callable(sync):
            self.studio.guard(f"{d.objectName()} panel", sync)()

    def _sync_window(self, ws: Workspace) -> None:
        doc = ws.document
        self.undo_action.setEnabled(doc is not None and doc.can_undo())
        self.redo_action.setEnabled(doc is not None and doc.can_redo())
        self.save_action.setEnabled(doc is not None)
        self.save_as_action.setEnabled(doc is not None)
        for group, tool, a in self._tool_actions:
            a.setChecked(ws.tool_on(group, tool))
        for a in self.workspace_actions:
            a.setChecked(a.data() == ws.name)
        self.bar.sync()
        title = chrome.title(doc_name(ws), self.studio.title, self.native)
        self.setWindowTitle(title)
        if title or QGuiApplication.platformName() == "cocoa":  # cocoa: a dot in the close button
            self.setWindowModified(doc is not None and doc.dirty)
        message = plain(self.studio.message)
        self.message.setText(message)
        self.message.setToolTip(message)  # the bar clips it
        self.where.setText(plain(ws.status()))
        self.renderer.setText(self.studio.renderer)

    def _recheck(self) -> None:
        """The findings re-check on their own clock."""
        if self.findings_dock.isVisible():
            self._sync_panel(self.findings_dock)

    # ---- closing --------------------------------------------------------------------- #

    def closeEvent(self, e: QCloseEvent) -> None:  # noqa: N802
        if not self.studio.discard_ok(*self.studio.workspaces):
            e.ignore()
            return
        self._closed = True
        self._pending.stop()
        self._slow.stop()
        self._retitling.stop()
        if self._shown is not None:
            self._save_layout(self._shown)
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.sync()
        self.runner.close()
        self.view.release()
        e.accept()
