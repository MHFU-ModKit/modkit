# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from typing import Any

import pytest
from mhfu.files import Extracted
from mhfu_studio.map.core.atlas import Atlas
from mhfu_studio.map.panels.view import ViewPanel
from mhfu_studio.map.workspace import MapWorkspace
from mhfu_studio.shell.studio import Studio
from mhfu_studio.ui import kit
from PySide6.QtWidgets import QPushButton


def panel(qtbot: Any, ws: MapWorkspace, studio: Studio | None = None) -> ViewPanel:
    p = ViewPanel(ws, studio or Studio([ws]))
    qtbot.addWidget(p)
    p.sync()
    return p


def button(p: ViewPanel, text: str) -> QPushButton:
    return next(b for b in p.findChildren(QPushButton) if b.text() == text)


def test_before_the_view(qtbot: Any, game: Extracted, atlas: Atlas) -> None:
    p = panel(qtbot, MapWorkspace(game, atlas))
    assert p.gate.empty.title.text() == "The view is starting" and kit.missing_tips(p) == []


def test_toggles_and_modes(qtbot: Any, ws: MapWorkspace, studio: Studio) -> None:
    p = panel(qtbot, ws, studio)
    vp = ws.vp
    assert vp is not None and vp.mesh is not None and kit.missing_tips(p) == []
    assert "show_collision" not in [attr for _, _, attr in p._toggles]  # the toolbar's
    more = p.findChild(kit.More)
    lattice = next(b for b, _, attr in p._toggles if attr == "show_lattice")
    assert more is not None and more.isAncestorOf(lattice) and more.isAncestorOf(p.gain)
    lattice.click()
    assert vp.show_lattice
    p.mode.setCurrentIndex(3)
    p.mode.activated.emit(3)
    p.gain.slider.setValue(500)
    assert vp.mesh.mode == 3 and vp.mesh.gain == pytest.approx(1.5)
    button(p, "Top").click()
    assert vp.camera.pitch == pytest.approx(89.0)
    button(p, "Hunter's eye").click()
    assert vp.camera.pitch == 0.0


def test_follows_the_view(qtbot: Any, ws: MapWorkspace, studio: Studio) -> None:
    p = panel(qtbot, ws, studio)
    assert ws.vp is not None and ws.vp.mesh is not None
    ws.vp.wireframe = True
    ws.vp.mesh.mode = 4
    ws.vp.camera.fov = 70.0
    p.sync()
    wire = next(b for b, _, attr in p._toggles if attr == "wireframe")
    assert wire.isChecked() and p.mode.currentData() == "4"
    assert p.fov.value() == pytest.approx(70.0, abs=0.1)
