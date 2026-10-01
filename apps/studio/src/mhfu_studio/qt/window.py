# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Qt window: menus, the map toolbar, docks with saved state, native dialogs.

The actions are the imgui shell's own `Studio` (open, save, undo, redo, findings), so both
windows drive the documents through one place.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QToolBar,
    QWidget,
)

from mhfu_studio.shell.widgets import plain

from .panels import FindingsPanel, HistoryPanel, SelectionPanel
from .view import GLView

if TYPE_CHECKING:
    from mhfu_studio.map.workspace import MapWorkspace
    from mhfu_studio.shell.app import Studio

#: bump when the docks change, so an old saved layout is not restored onto new docks
STATE_VERSION = 1


def qt_filters(pairs: Sequence[str]) -> str:
    """portable-file-dialogs pairs ("Map document", "map.toml", ...) as a Qt filter string."""
    return ";;".join(
        f"{name} ({pattern})" for name, pattern in zip(pairs[::2], pairs[1::2], strict=True)
    )


class Window(QMainWindow):
    def __init__(self, studio: Studio, settings: QSettings | None = None) -> None:
        super().__init__()
        self.studio = studio
        self.settings = settings or QSettings("mhfu-studio", "qt-spike")
        self.setWindowTitle("MHFU Studio (Qt spike)[*]")
        self.setDockOptions(
            QMainWindow.DockOption.AnimatedDocks
            | QMainWindow.DockOption.AllowTabbedDocks
            | QMainWindow.DockOption.AllowNestedDocks
        )
        self.view = GLView(studio)
        self.view.changed.connect(self.sync)
        self.setCentralWidget(self.view)
        self.ws = self._map()
        self.docks: list[QDockWidget] = []
        self.panels: list[SelectionPanel | HistoryPanel | FindingsPanel] = []
        if self.ws is not None:
            sel = SelectionPanel(self.ws, self.sync)
            hist = HistoryPanel(self.ws, studio, self.sync)
            self._dock("Selection", sel, Qt.DockWidgetArea.RightDockWidgetArea)
            self._dock("Edits", hist, Qt.DockWidgetArea.RightDockWidgetArea)
            self.panels += [sel, hist]
        findings = FindingsPanel(studio, self.sync)
        self._dock("Findings", findings, Qt.DockWidgetArea.BottomDockWidgetArea)
        self.panels.append(findings)
        self._default = self.saveState(STATE_VERSION)
        self._menus()
        if self.ws is not None:
            self._toolbar(self.ws)
        self.where = QLabel()
        self.statusBar().addPermanentWidget(self.where)
        self.restore()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(250)
        self.sync()

    def _map(self) -> MapWorkspace | None:
        from mhfu_studio.map.workspace import MapWorkspace

        ws = self.studio.active
        return ws if isinstance(ws, MapWorkspace) else None

    def _dock(self, title: str, widget: QWidget, area: Qt.DockWidgetArea) -> None:
        d = QDockWidget(title, self)
        d.setObjectName(title)  # saveState keys on it
        d.setWidget(widget)
        self.addDockWidget(area, d)
        self.docks.append(d)

    def _action(
        self,
        menu_text: str,
        slot: Callable[[], object],
        key: QKeySequence | QKeySequence.StandardKey | str | None = None,
        tip: str = "",
    ) -> QAction:
        a = QAction(menu_text, self)
        if key is not None:
            a.setShortcut(QKeySequence(key))
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)

        def run() -> None:
            slot()
            self.sync()

        a.triggered.connect(run)
        return a

    def _menus(self) -> None:
        bar = self.menuBar()
        f = bar.addMenu("&File")
        f.addAction(self._action("&Open...", self.ask_open, QKeySequence.StandardKey.Open))
        self.save_action = self._action("&Save", self.studio.save, QKeySequence.StandardKey.Save)
        f.addAction(self.save_action)
        f.addAction(self._action("Save &As...", self.ask_save_as, QKeySequence.StandardKey.SaveAs))
        f.addSeparator()
        f.addAction(self._action("&Quit", self.close, QKeySequence.StandardKey.Quit))
        e = bar.addMenu("&Edit")
        self.undo_action = self._action("&Undo", self.studio.undo, QKeySequence.StandardKey.Undo)
        self.redo_action = self._action("&Redo", self.studio.redo, QKeySequence.StandardKey.Redo)
        e.addActions([self.undo_action, self.redo_action])
        v = bar.addMenu("&View")
        for d in self.docks:
            v.addAction(d.toggleViewAction())
        v.addSeparator()
        v.addAction(
            self._action("Reset layout", lambda: self.restoreState(self._default, STATE_VERSION))
        )

    def _toolbar(self, ws: MapWorkspace) -> None:
        from mhfu_studio.map.core.edit import COLLISION, FACE, GROUP, OBJECT
        from mhfu_studio.map.tools import MOVE, ROTATE, SCALE, SELECT

        tb = QToolBar("Tools")
        tb.setObjectName("Tools")
        self.addToolBar(tb)
        self.tool_actions: dict[str, QAction] = {}
        self.kind_actions: dict[str, QAction] = {}
        group = QActionGroup(self)
        for text, tool, key, tip in (
            ("Select", SELECT, "Q", "Click or drag a box to select"),
            ("Move", MOVE, "W", "Drag an arrow to move the selection along it"),
            ("Rotate", ROTATE, "E", "The spike's gizmo only moves: rotate in the Selection form"),
            ("Scale", SCALE, "R", "The spike's gizmo only moves: scale in the Selection form"),
        ):
            a = self._action(text, partial(ws.tools.set_tool, tool), key, f"{tip} ({key})")
            a.setCheckable(True)
            group.addAction(a)
            tb.addAction(a)
            self.tool_actions[tool] = a
        tb.addSeparator()
        kinds = QActionGroup(self)
        for text, kind, key, tip in (
            ("Groups", GROUP, "1", "A click picks a whole draw group"),
            ("Objects", OBJECT, "2", "A click picks one connected object"),
            ("Faces", FACE, "3", "A click picks one triangle"),
            ("Collision", COLLISION, "4", "A click picks collision triangles"),
        ):
            a = self._action(text, partial(ws.tools.set_kind, kind), key, f"{tip} ({key})")
            a.setCheckable(True)
            kinds.addAction(a)
            tb.addAction(a)
            self.kind_actions[kind] = a
        tb.addSeparator()
        snap = self._action(
            "Snap",
            lambda: setattr(ws.tools, "snap", not ws.tools.snap),
            None,
            "Moves go in steps of 50 units",
        )
        snap.setCheckable(True)
        tb.addAction(snap)

    # dialogs

    def ask_open(self) -> None:
        pairs: list[str] = []
        for w in self.studio.workspaces:
            pairs += w.filters
        start = str(Path.cwd())
        got, _ = QFileDialog.getOpenFileName(self, "Open a document", start, qt_filters(pairs))
        if got and not self.studio.open(got):
            QMessageBox.warning(self, "Not opened", plain(self.studio.message))

    def ask_save_as(self) -> None:
        doc = self.studio.active.document
        if doc is None:
            return
        start = str(doc.path or Path.cwd() / "map.toml")
        got, _ = QFileDialog.getSaveFileName(
            self, "Save the document as", start, qt_filters(self.studio.active.filters)
        )
        if got:
            self.studio.save(got)

    # state

    def sync(self) -> None:
        """Re-reads everything a panel, an action or the status bar shows."""
        for p in self.panels:
            p.sync()
        doc = self.studio.active.document
        self.undo_action.setEnabled(doc is not None and doc.can_undo())
        self.redo_action.setEnabled(doc is not None and doc.can_redo())
        self.save_action.setEnabled(doc is not None)
        if self.ws is not None:
            self.tool_actions[self.ws.tools.tool].setChecked(True)
            self.kind_actions[self.ws.tools.kind].setChecked(True)
        name = "" if doc is None or doc.path is None else doc.path.name
        dirty = " (unsaved)" if doc is not None and doc.dirty else ""
        self.setWindowModified(bool(dirty))
        self.statusBar().showMessage(plain(self.studio.message))
        self.where.setText(
            "   ".join(
                p
                for p in (
                    f"{name}{dirty}".strip(),
                    plain(self.studio.active.status()),
                    self.studio.renderer,
                )
                if p
            )
        )
        self.view.update()

    def _tick(self) -> None:
        self.panels[-1].sync()  # findings re-check on their own clock

    def restore(self) -> None:
        geo, state = self.settings.value("geometry"), self.settings.value("state")
        if geo is not None:
            self.restoreGeometry(geo)
        else:
            self.resize(1500, 940)
        if state is not None:
            self.restoreState(state, STATE_VERSION)

    def closeEvent(self, e: QCloseEvent) -> None:  # noqa: N802
        doc = self.studio.active.document
        if doc is not None and doc.dirty:
            ask = QMessageBox.question(
                self,
                "Unsaved edits",
                "Save the document before closing?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if ask == QMessageBox.StandardButton.Cancel:
                e.ignore()
                return
            if ask == QMessageBox.StandardButton.Save:
                self.studio.save() if doc.path is not None else self.ask_save_as()
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("state", self.saveState(STATE_VERSION))
        self.view.release()
        e.accept()


def prepare() -> None:
    """GL 3.3 core for every QOpenGLWidget; before the QApplication exists."""
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtWidgets import QApplication

    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)  # macOS answers 4.1 core
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setDepthBufferSize(24)
    QSurfaceFormat.setDefaultFormat(fmt)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)


def main(argv: Sequence[str] | None = None, path: Path | None = None) -> int:
    from PySide6.QtWidgets import QApplication

    from mhfu_studio.map.workspace import MapWorkspace
    from mhfu_studio.shell.app import Studio

    prepare()
    app = QApplication.instance() or QApplication(list(argv or sys.argv[:1]))
    app.setApplicationName("MHFU Studio")
    studio = Studio([MapWorkspace()])
    if path is not None and not studio.open(path):
        raise ValueError(studio.message)
    w = Window(studio)
    w.show()
    return int(app.exec())
