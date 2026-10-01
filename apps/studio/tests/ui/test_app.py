# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path
from typing import Any

import pytest
from mhfu_studio.map.document import MapDocument
from mhfu_studio.ui.app import build
from PySide6.QtCore import QSettings
from PySide6.QtGui import QSurfaceFormat


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "studio.ini"), QSettings.Format.IniFormat)


def test_prepared(qapp: object) -> None:
    fmt = QSurfaceFormat.defaultFormat()
    assert fmt.version() == (3, 3) and fmt.depthBufferSize() == 24


def test_build(qtbot: Any, settings: QSettings, tmp_path: Path, asked: list[Any]) -> None:
    w = build(workspace="monster", settings=settings)
    qtbot.addWidget(w)
    assert w.studio.active.name == "monster" and not w.isVisible()
    MapDocument("d", 11, tmp_path / "doc").save()
    w = build(tmp_path / "doc" / "map.toml", workspace="monster", settings=settings)
    qtbot.addWidget(w)
    assert w.studio.active.name == "map" and w.studio.active.document is not None


def test_build_refuses(qapp: object, settings: QSettings, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no workspace 'nope'"):
        build(workspace="nope", settings=settings)
    with pytest.raises(ValueError, match="nothing here opens x.png"):
        build(tmp_path / "x.png", settings=settings)


def test_size_beats_the_saved_geometry(qtbot: Any, settings: QSettings, asked: list[Any]) -> None:
    w = build(settings=settings)
    qtbot.addWidget(w)
    w.resize(1000, 700)
    w.close()  # saves the geometry
    w = build(size=(1200, 800), settings=settings)
    qtbot.addWidget(w)
    assert (w.width(), w.height()) == (1200, 800)
    w = build(settings=settings)
    qtbot.addWidget(w)
    assert w.height() == 700  # restored; the offscreen screen is too narrow for the width
