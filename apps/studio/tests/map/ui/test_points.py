# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path
from typing import Any

from mhfu import points
from mhfu.points import Point
from mhfu_studio.map.panels.points import PointsPanel, command
from mhfu_studio.map.tools import POINT
from mhfu_studio.map.workspace import MapWorkspace
from mhfu_studio.shell.studio import Studio
from mhfu_studio.ui import kit
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QPushButton

from ..conftest import OTHER, STAGE


def panel(qtbot: Any, ws: MapWorkspace, studio: Studio) -> PointsPanel:
    p = PointsPanel(ws, studio)
    qtbot.addWidget(p)
    studio.listen(p.sync)
    p.sync()
    return p


def button(p: PointsPanel, text: str) -> QPushButton:
    return next(b for b in p.findChildren(QPushButton) if b.text() == text)


def test_set_name_and_save(qtbot: Any, ws: MapWorkspace, studio: Studio, doc_dir: Path) -> None:
    ws.load_stage(STAGE, row=0)
    p = panel(qtbot, ws, studio)
    assert kit.missing_tips(p) == [] and p.form.isHidden()
    button(p, "Set a point").click()
    assert ws.tools.tool == POINT
    studio.act("place", lambda: ws.place_point((1200.0, 0.0, 1500.0)))()
    assert not p.form.isHidden() and p.name.text() == "point_1"
    p.name.setText("wall")
    p.name.editingFinished.emit()
    p.note.setText("the Tigrex sticks here")
    p.note.editingFinished.emit()
    p.kind.activated.emit(p.kind.findData("climb"))
    assert p.heading.isEnabled()
    p.heading.setValue(157.0)
    assert ws.doc.points == [
        Point("wall", STAGE, (1200.0, 0.0, 1500.0), "climb", 157.0, "the Tigrex sticks here")
    ]
    ws.doc.save(doc_dir)
    assert points.load(doc_dir) == ws.doc.points  # what `mhfu rig goto` reads
    button(p, "Copy goto command").click()
    assert QGuiApplication.clipboard().text() == command("wall", doc_dir)
    assert command("wall", doc_dir) == f"mhfu rig goto wall --map {doc_dir}"


def test_refused_name(qtbot: Any, ws: MapWorkspace, studio: Studio) -> None:
    ws.load_stage(STAGE, row=0)
    ws.doc.put_point(Point("a", STAGE, (0.0, 0.0, 0.0)))
    ws.doc.put_point(Point("b", STAGE, (1.0, 0.0, 1.0)))
    ws.point = "b"
    p = panel(qtbot, ws, studio)
    p.name.setText("a")
    p.name.editingFinished.emit()
    assert "already named a" in studio.message and ws.doc.point("b") is not None


def test_pick_loads_its_area_and_remove(qtbot: Any, ws: MapWorkspace, studio: Studio) -> None:
    ws.load_stage(STAGE, row=0)
    ws.doc.put_point(Point("far", OTHER, (5.0, 0.0, 5.0)))
    p = panel(qtbot, ws, studio)
    p.items.picked.emit("far")
    assert ws.scene is not None and ws.scene.stage == OTHER and ws.point == "far"
    button(p, "Remove").click()
    assert ws.doc.points == [] and ws.point is None and p.form.isHidden()
