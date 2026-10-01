# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from typing import Any

import pytest
from mhfu_studio.monster.panels import moves
from mhfu_studio.monster.panels.moves import MovesPanel
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell.studio import Studio
from mhfu_studio.ui import kit
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

LEFT = Qt.MouseButton.LeftButton


def make(qtbot: Any, ws: MonsterWorkspace) -> MovesPanel:
    """Shown, and synced on every change as the window would."""
    studio = Studio([ws])
    p = MovesPanel(ws, studio)
    studio.listen(p.sync)
    qtbot.addWidget(p)
    p.resize(900, 420)
    p.show()
    qtbot.waitExposed(p)
    p.sync()
    return p


@pytest.fixture
def panel(qtbot: Any, workspace: MonsterWorkspace) -> MovesPanel:
    return make(qtbot, workspace)


def at(p: MovesPanel, pair: tuple[int, int]) -> QPoint:
    return p.canvas.mapFromScene(p.canvas.nodes[pair].rect().center())


def empty_spot(p: MovesPanel) -> QPoint:
    """Canvas with no node under it, below the layout."""
    r = p.canvas.mapFromScene(p.canvas.nodes_rect().bottomLeft())
    return QPoint(r.x() + 4, r.y() + 30)


def drag(widget: Any, a: QPoint, b: QPoint, button: Qt.MouseButton = LEFT) -> None:
    QTest.mousePress(widget, button, Qt.KeyboardModifier.NoModifier, a)
    for i in range(1, 6):
        QTest.mouseMove(widget, a + (b - a) * (i / 5))
    QTest.mouseRelease(widget, button, Qt.KeyboardModifier.NoModifier, b)


def wheel(widget: Any, pos: QPoint, notches: int) -> None:
    ev = QWheelEvent(
        QPointF(pos),
        QPointF(widget.mapToGlobal(pos)),
        QPoint(0, 0),
        QPoint(0, 120 * notches),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(widget, ev)


def test_empty_without_a_manifest(qtbot: Any) -> None:
    p = make(qtbot, MonsterWorkspace())
    assert p.empty.currentWidget() is p.no_scene and not p.tools.isVisible()
    assert kit.missing_tips(p) == []


def test_loaded(panel: MovesPanel) -> None:
    assert kit.missing_tips(panel) == []
    assert panel.pages.currentWidget() is panel.canvas
    assert {(1, 4), (0, 3), (0, 1)} <= set(panel.canvas.nodes)
    assert "(1,4)" in panel.walk.text() or "hubs" in panel.walk.text()


def test_click_picks(panel: MovesPanel, workspace: MonsterWorkspace) -> None:
    vp = panel.canvas.viewport()
    QTest.mouseClick(vp, LEFT, Qt.KeyboardModifier.NoModifier, at(panel, (1, 4)))
    panel.sync()
    assert workspace.graph.picked == (1, 4) == panel.canvas.picked
    lines, hint = panel.canvas.info_lines()
    assert not hint and lines[0].startswith("(1,4)  charge")
    hot = [e for e in panel.canvas.edges if e.hot]
    assert hot and all(e.zValue() > 1 for e in hot)
    QTest.mouseClick(vp, LEFT, Qt.KeyboardModifier.NoModifier, empty_spot(panel))
    panel.sync()
    assert workspace.graph.picked is None


def test_double_click_selects(panel: MovesPanel, workspace: MonsterWorkspace) -> None:
    QTest.mouseDClick(
        panel.canvas.viewport(), LEFT, Qt.KeyboardModifier.NoModifier, at(panel, (0, 3))
    )
    assert workspace.pair == (0, 3) and workspace.take_focus() == "Action"
    panel.sync()
    assert panel.canvas.selected == (0, 3) and "(0,3)" in panel.walk.text()


def test_drag_moves_a_node(panel: MovesPanel, workspace: MonsterWorkspace) -> None:
    node = workspace.graph.layout(workspace.intel, dict(workspace.manifest.moves), None).nodes[
        (1, 4)
    ]  # type: ignore[union-attr]
    x0, y0 = node.x, node.y
    a = at(panel, (1, 4))
    drag(panel.canvas.viewport(), a, a + QPoint(60, 30))
    k = panel.canvas.zoom()
    assert node.x == pytest.approx(x0 + 60 / k, abs=2 / k)
    assert node.y == pytest.approx(y0 + 30 / k, abs=2 / k)
    assert workspace.graph.picked is None, "a drag is not a click"
    rebuilt = MovesPanel(workspace, Studio([workspace]))
    rebuilt.sync()
    assert rebuilt.canvas.nodes[(1, 4)].pos().x() == pytest.approx(node.x)


def test_drag_canvas_pans(panel: MovesPanel) -> None:
    c = panel.canvas
    a = empty_spot(panel)
    before = c.mapToScene(a)
    drag(c.viewport(), a, a + QPoint(50, 0))
    assert c.mapToScene(a).x() == pytest.approx(before.x() - 50 / c.zoom(), abs=2)
    before = c.mapToScene(a)
    drag(c.viewport(), a, a + QPoint(0, 40), Qt.MouseButton.RightButton)
    assert c.mapToScene(a).y() == pytest.approx(before.y() - 40 / c.zoom(), abs=2)


def test_wheel_zooms(panel: MovesPanel) -> None:
    c = panel.canvas
    z = c.zoom()
    wheel(c.viewport(), at(panel, (1, 4)), 1)
    assert c.zoom() == pytest.approx(min(z * moves.STEP, moves.ZOOM[1]))
    wheel(c.viewport(), at(panel, (1, 4)), 40)
    assert c.zoom() == pytest.approx(moves.ZOOM[1])
    panel.fit.click()
    assert moves.FIT_ZOOM[0] <= c.zoom() <= moves.FIT_ZOOM[1]


def test_scope_switches(panel: MovesPanel, workspace: MonsterWorkspace) -> None:
    panel.scope.buttons["attacks"].click()
    panel.sync()
    assert workspace.graph.scope == "attacks" and (3, 9) in panel.canvas.nodes
    panel.scope.buttons["selected"].click()
    panel.sync()
    assert panel.pages.currentWidget() is panel.empty and "select a pair" in panel.note.hint.text()


def test_relayout_forgets_drags(panel: MovesPanel, workspace: MonsterWorkspace) -> None:
    a = at(panel, (1, 4))
    drag(panel.canvas.viewport(), a, a + QPoint(80, 0))
    moved = panel.canvas.nodes[(1, 4)].pos().x()
    panel.relayout.click()
    assert panel.canvas.nodes[(1, 4)].pos().x() < moved
