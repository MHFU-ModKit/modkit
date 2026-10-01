# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Qt spike: the gizmo's math anywhere, the window wherever Qt has a GL platform."""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mhfu_studio.qt import gizmo  # noqa: E402
from mhfu_studio.qt.window import Window, prepare, qt_filters  # noqa: E402
from mhfu_studio.shell.app import Studio  # noqa: E402
from mhfu_studio.shell.camera import OrbitCamera  # noqa: E402
from mhfu_studio.shell.context import borrowed  # noqa: E402
from mhfu_studio.shell.testing import FakeWorkspace  # noqa: E402

prepare()


@pytest.fixture(scope="module", autouse=True)
def _suite_context_back() -> Iterator[None]:
    """Qt leaves its own context current; the suite's headless one is current again after."""
    with borrowed():
        yield


def test_along_meets_the_ray() -> None:
    t = gizmo.along(
        np.zeros(3), np.array([1.0, 0, 0]), np.array([5.0, 10, 0]), np.array([0, -1.0, 0])
    )
    assert t == pytest.approx(5.0)


def test_drag_snaps() -> None:
    down = np.array([0, -1.0, 0])
    drag = gizmo.Drag(np.zeros(3), 0, (np.array([1.0, 10, 0]), down))
    assert drag.offset((np.array([63.0, 10, 0]), down)) == pytest.approx([62, 0, 0])
    assert drag.offset((np.array([63.0, 10, 0]), down), snap=50) == pytest.approx([50, 0, 0])


def test_handles_and_hit() -> None:
    cam = OrbitCamera(target=(0, 0, 0), distance=1000, yaw=30, pitch=20)
    hs = gizmo.handles(cam, np.zeros(3), (800, 600))
    assert [h.axis for h in hs] == [0, 1, 2]
    h = hs[1]
    mid = ((h.a[0] + h.b[0]) / 2, (h.a[1] + h.b[1]) / 2)
    assert gizmo.hit(hs, *mid) == 1
    assert gizmo.hit(hs, mid[0] + 200, mid[1] + 200) is None
    assert gizmo.handles(cam, cam.eye + cam.direction * 50, (800, 600)) == []


def test_filters() -> None:
    assert qt_filters(("Map document", "map.toml", "All files", "*")) == (
        "Map document (map.toml);;All files (*)"
    )


@pytest.fixture
def window(qtbot: Any, tmp_path: Path) -> Window:
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QOpenGLContext

    probe = QOpenGLContext()
    if not probe.create():
        pytest.skip(f"Qt platform {os.environ['QT_QPA_PLATFORM']!r} has no GL")
    doc = tmp_path / "a.txt"
    doc.write_text("x y")
    studio = Studio([FakeWorkspace("fake", ".txt")])
    assert studio.open(doc)
    w = Window(studio, QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(w)
    w.resize(900, 600)
    w.show()
    qtbot.waitExposed(w)
    return w


def test_window_draws(window: Window) -> None:
    img = window.view.grabFramebuffer()
    a = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine())
    assert window.studio.renderer and window.studio.error is None
    assert len(np.unique(a)) > 16, "the viewport is one flat colour"


def test_undo_through_the_menu(window: Window) -> None:
    doc = window.studio.active.document
    assert doc is not None and not window.undo_action.isEnabled()
    doc.edit("z")  # type: ignore[attr-defined]
    window.sync()
    assert window.undo_action.isEnabled() and window.isWindowModified()
    window.undo_action.trigger()
    assert doc.history.value == ["x", "y"]  # type: ignore[attr-defined]
    assert window.redo_action.isEnabled()


def test_docks_come_back(window: Window, qtbot: Any) -> None:
    findings = window.docks[0]
    findings.hide()
    window.close()
    again = Window(window.studio, window.settings)
    qtbot.addWidget(again)
    again.show()
    assert again.docks[0].isHidden()
