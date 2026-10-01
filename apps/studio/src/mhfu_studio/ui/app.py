# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Qt application: the controller over every registered workspace, in one `Window`."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QApplication


def surface_format() -> QSurfaceFormat:
    """GL 3.3 core with a depth buffer: what the shell's GL layer draws with."""
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)  # macOS answers 4.1 core
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setDepthBufferSize(24)
    return fmt


def prepare() -> None:
    """`surface_format` for every QOpenGLWidget; before the QApplication exists (macOS shares
    contexts only then)."""
    QSurfaceFormat.setDefaultFormat(surface_format())
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)


def main(argv: Sequence[str] | None = None, path: Path | None = None) -> int:
    """Runs the window until it closes; `path` opens in the workspace that reads it."""
    from mhfu_studio.cli import AREAS
    from mhfu_studio.shell.studio import Studio
    from mhfu_studio.shell.workspace import discover
    from mhfu_studio.ui.window import Window

    app = QApplication.instance()
    if app is None:
        prepare()
        app = QApplication(list(argv) if argv is not None else sys.argv[:1])
    app.setApplicationName("MHFU Studio")
    app.setOrganizationName("mhfu-studio")
    studio = Studio([make() for make in discover(AREAS).values()])
    if path is not None and not studio.open(path):
        raise ValueError(studio.message)
    w = Window(studio)
    w.show()
    return int(app.exec())
