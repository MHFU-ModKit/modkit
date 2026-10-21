# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A move in miniature on the Plays choice and on a Move node; offscreen has no GL, so the
picture's turn is a stand-in renderer."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from mhfu.em.intel import SpeciesIntel
from mhfu_port.model import Clip
from mhfu_studio.monster.panels.move_preview import on_screen
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget

from .test_brain_graph import ROW, Rig, _isolated, body_at, built, hub_id, pick_id  # noqa: F401
from .test_operand_bar import Bench


class Fake:
    """`MovePreview` without GL: the next clip every third frame."""

    def __init__(self) -> None:
        self.at, self.frames, self.size = 0, 0, (1, 1)
        self.shown: list[int] = []

    def resize(self, size: tuple[int, int]) -> None:
        self.size = size

    def show(self, scene: Any, clips: Sequence[Clip]) -> None:
        self.shown, self.at = [c.slot for c in clips], 0

    def advance(self, dt: float) -> None:
        self.frames += 1
        self.at = self.frames // 3 % len(self.shown)

    def picture(self, background: Any = None) -> Any:
        return np.full((self.size[1], self.size[0], 4), 200, np.uint8)


@pytest.fixture
def bench(
    qtbot: Any, tmp_path: Path, synthetic_pac: bytes, intel75: SpeciesIntel
) -> Iterator[Bench]:
    ws, clock = built(tmp_path, synthetic_pac, intel75)
    yield Bench(qtbot, ws, clock)
    ws.close()


@pytest.fixture
def rig(qtbot: Any, tmp_path: Path, synthetic_pac: bytes, intel75: SpeciesIntel) -> Iterator[Rig]:
    ws, clock = built(tmp_path, synthetic_pac, intel75)
    yield Rig(qtbot, ws, clock)
    ws.close()


def hover(w: QWidget, at: QPoint) -> None:
    """The mouse moves over `w` at `at`, no button held."""
    ev = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(at),
        QPointF(w.mapToGlobal(at)),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(w, ev)


def item(box: Any, prefix: str) -> int:
    return next(i for i in range(box.count()) if box.itemText(i).startswith(prefix))


def test_the_choice_shows_its_move_once_the_mouse_rests(bench: Bench, qtbot: Any) -> None:
    bench.show(hub_id(bench.program))
    box, popup = bench.combo("pair"), bench.bar.preview
    QApplication.sendEvent(box, QEvent(QEvent.Type.Enter))
    assert not popup.isVisible()
    qtbot.waitUntil(popup.isVisible, timeout=2000)
    assert popup.title.text() == box.itemText(0) and not popup.picture.isVisible()
    assert popup.caption.text().startswith("Names no anim"), "(3, 0) has no intel"
    QApplication.sendEvent(box, QEvent(QEvent.Type.Leave))
    assert not popup.isVisible()


def test_the_open_list_follows_the_mouse(bench: Bench, qtbot: Any) -> None:
    bench.show(hub_id(bench.program))
    box, popup = bench.combo("pair"), bench.bar.preview
    box.showPopup()
    k = item(box, "(1, 4)")
    box.highlighted.emit(k)
    qtbot.waitUntil(popup.isVisible, timeout=2000)
    assert popup.title.text() == box.itemText(k)
    assert popup.caption.text() == f"anim 1 · {bench.ws.scene.clip(1).frames} frames\n" + (
        "The picture needs the 3D view shown once"
    )
    j = item(box, "(1, 3)")
    box.highlighted.emit(j)
    assert popup.title.text() == box.itemText(j), "at once while it is up"
    box.hidePopup()
    assert not popup.isVisible()


def test_it_goes_above_beside_or_below(bench: Bench, qtbot: Any) -> None:
    popup = bench.bar.preview
    popup.ask(ROW, (1, 4), QRect(10, 400, 100, 20))
    qtbot.waitUntil(popup.isVisible, timeout=2000)
    assert popup.geometry().bottom() < 400 and popup.x() == 10, "above"
    popup.ask(ROW, (1, 4), QRect(10, 0, 100, 20))
    assert popup.y() > 20, "below, with no room above"
    popup.ask(ROW, (1, 4), QRect(10, 0, 100, 20), beside=True)
    assert popup.x() > 110 and popup.y() == 0, "beside"


def test_the_picture_plays_each_anim_in_turn(
    bench: Bench, qtbot: Any, species: Any, make_pair: Any
) -> None:
    bench.ws.intel_cache[75] = species([make_pair(1, 4, a1=[1, 2])])
    fake = Fake()
    bench.ws.preview = lambda: fake  # type: ignore[assignment,return-value]
    popup = bench.bar.preview
    popup.ask(ROW, (1, 4), on_screen(bench.bar))
    qtbot.waitUntil(popup.isVisible, timeout=2000)
    assert fake.shown == [1, 2] and popup.picture.isVisible()
    assert not popup.picture.pixmap().isNull() and popup.caption.text().endswith("1 of 2")
    qtbot.waitUntil(lambda: popup.caption.text().endswith("2 of 2"), timeout=2000)
    popup.drop()
    assert not popup.frames.isActive() and not popup.isVisible()


def test_a_move_node_shows_its_move(rig: Rig, qtbot: Any) -> None:
    rig.show("base_0")
    hub, popup = hub_id(rig.program), rig.pane.preview
    port = rig.view.viewer.viewport()
    hover(port, body_at(rig.view, hub))
    qtbot.waitUntil(popup.isVisible, timeout=2000)
    assert popup.title.text().startswith("(3, 0)")
    near = rig.view.node_on_screen(hub)
    assert near is not None and not popup.geometry().intersects(near), "above or below it"
    hover(port, body_at(rig.view, pick_id(rig.program)))
    assert not popup.isVisible(), "a pick is no move"
