# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""What the map panels share: the empty states, in one wording, and texture thumbnails."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QStackedWidget, QWidget

from mhfu_studio.shell.widgets import plain
from mhfu_studio.ui import kit

if TYPE_CHECKING:
    from ..core.scene import TextureImage
    from ..workspace import MapWorkspace

NO_DATA = "No game files"
DATA_HINT = (
    "The map editor reads the game's own files. Extract the game with mhp-formats extract,"
    " set MHFU_DATA to its data_files folder and start the studio again."
)
NO_SECTION = "No section loaded"


class Gate(QStackedWidget):
    """A panel's page, or an empty state that says what is missing and how to get it."""

    def __init__(self, page: QWidget, what: str) -> None:
        """`what` ends "Load a section in the Map panel to …", say "see its textures"."""
        super().__init__()
        self.page = page
        self.what = what
        self.empty = kit.Empty(NO_SECTION, "")
        self.addWidget(page)
        self.addWidget(self.empty)

    def check(self, ws: MapWorkspace, *, section: bool = True) -> bool:
        """The page when the game files are there and, with `section`, a section is loaded;
        else the empty state naming what is missing. True when the page shows."""
        if ws.atlas is None:
            why = "" if "MHFU_DATA" in ws.data_error else f"{plain(ws.data_error)}. "
            return self.need(NO_DATA, why + DATA_HINT)
        if section and (ws.scene is None or ws.session is None):
            return self.need(NO_SECTION, f"Load a section in the Map panel to {self.what}.")
        return self.ready()

    def need(self, title: str, hint: str) -> bool:
        """The empty state with `title` and `hint`; False."""
        self.empty.say(title, hint)
        if self.currentWidget() is not self.empty:
            self.setCurrentWidget(self.empty)
        return False

    def ready(self) -> bool:
        if self.currentWidget() is not self.page:
            self.setCurrentWidget(self.page)
        return True


def image(t: TextureImage) -> QImage:
    """The slot's pixels as an image that owns its memory."""
    h, w = t.rgba.shape[:2]
    data = np.ascontiguousarray(t.rgba, np.uint8).tobytes()
    return QImage(data, w, h, 4 * w, QImage.Format.Format_RGBA8888).copy()


def thumbnail(t: TextureImage, side: int) -> QPixmap:
    """The slot fitted into `side` x `side`, pixels kept sharp."""
    return QPixmap.fromImage(image(t)).scaled(
        side,
        side,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.FastTransformation,
    )
