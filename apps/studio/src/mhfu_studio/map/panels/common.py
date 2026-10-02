# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""What the map panels share: the empty states, in one wording, and texture thumbnails."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QListWidget, QWidget

from mhfu_studio.shell.text import plain
from mhfu_studio.ui import kit

if TYPE_CHECKING:
    from ..core.scene import TextureImage
    from ..workspace import MapWorkspace

NO_DATA = "No game files"
DATA_HINT = (
    "The map editor reads the game's own files: choose your MHFU extraction under Setup on the"
    " start page."
)
NO_AREA = "No area loaded"


class Gate(kit.Pages):
    """A map panel's page, or an empty state that says what is missing and how to get it."""

    empty: kit.Empty

    def __init__(self, page: QWidget, what: str) -> None:
        """`what` ends "Pick an area in the Areas panel to …", say "see its textures"."""
        super().__init__(page, kit.Empty(NO_AREA, ""))
        self.what = what

    def check(self, ws: MapWorkspace, *, section: bool = True) -> bool:
        """The page when the game files are there and, with `section`, an area is loaded;
        else the empty state naming what is missing. True when the page shows."""
        if ws.atlas is None:
            why = plain(ws.data_error) or DATA_HINT
            return self.need(NO_DATA, why[:1].upper() + why[1:])
        if section and (ws.scene is None or ws.session is None):
            return self.need(NO_AREA, f"Pick an area in the Areas panel to {self.what}.")
        return self.ready()

    def need(self, title: str, hint: str) -> bool:
        """The empty state with `title` and `hint`; False."""
        self.empty.say(title, hint)
        self.show_page(False)
        return False

    def ready(self) -> bool:
        self.show_page(True)
        return True


def fit(view: QListWidget, most: int = 5) -> None:
    """`view` as tall as its rows, up to `most` of them."""
    rows = max(1, min(view.count(), most)) * max(view.sizeHintForRow(0), 1)
    view.setFixedHeight(rows + 2 * view.frameWidth() + 4)


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
