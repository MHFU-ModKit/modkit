# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Qt application: the controller over every registered workspace, in one `Window`."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QCoreApplication, QLocale, QSettings, Qt
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QApplication

if TYPE_CHECKING:
    from mhfu_studio.ui.window import Window


def surface_format() -> QSurfaceFormat:
    """GL 3.3 core with a depth buffer: what the shell's GL layer draws with."""
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)  # macOS answers 4.1 core
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setDepthBufferSize(24)
    return fmt


def prepare() -> None:
    """Before the QApplication exists (macOS shares GL contexts only then): `surface_format`
    for every QOpenGLWidget, and the C locale, so a number reads 9.0 on every system."""
    QSurfaceFormat.setDefaultFormat(surface_format())
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    QLocale.setDefault(QLocale(QLocale.Language.C))


def application() -> QCoreApplication:
    """The running QApplication, or a new one after `prepare`."""
    app = QApplication.instance()
    if app is None:
        prepare()
        app = QApplication(sys.argv[:1])
    app.setApplicationName("MHFU Studio")
    app.setOrganizationName("mhfu-studio")
    return app


def build(
    path: Path | None = None,
    workspace: str | None = None,
    size: tuple[int, int] | None = None,
    settings: QSettings | None = None,
) -> Window:
    """The window over every registered workspace, not shown yet.

    `path` opens in the workspace that reads it; `size` wins over the restored geometry. Raises
    ValueError for an unknown `workspace` or a `path` that does not open.
    """
    from mhfu_studio.cli import AREAS
    from mhfu_studio.shell.studio import Studio
    from mhfu_studio.shell.workspace import discover
    from mhfu_studio.ui import settings as saved
    from mhfu_studio.ui.window import Window

    settings = settings if settings is not None else QSettings()
    saved.install(settings)  # before the workspaces look for the games
    studio = Studio([make() for make in discover(AREAS).values()])
    if workspace is not None:
        try:
            studio.switch(workspace)
        except KeyError as e:
            raise ValueError(e.args[0]) from None
    if path is not None and not studio.open(path):
        raise ValueError(studio.message)
    w = Window(studio, settings)
    if size is not None:
        w.resize(*size)
    return w


def main(
    path: Path | None = None, workspace: str | None = None, size: tuple[int, int] | None = None
) -> int:
    """Runs the window until it closes."""
    app = application()
    w = build(path, workspace, size)
    w.show()
    return int(app.exec())
