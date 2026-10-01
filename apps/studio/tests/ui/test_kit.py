# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from typing import Any

import pytest
from mhfu_studio.ui import kit
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget


def test_a_control_needs_a_tip(qapp: object) -> None:
    with pytest.raises(ValueError, match="tip"):
        kit.button("Go", tip=" ", on=lambda: None)


def test_missing_tips_names_bare_controls(qapp: object) -> None:
    root = QWidget()
    lay = QVBoxLayout(root)
    bare = QPushButton("bare")
    for w in (bare, kit.number(tip="how far"), kit.choice([("a", "A")], tip="which", on=print)):
        lay.addWidget(w)
    lay.addWidget(kit.Vec3(tip="by"))
    lay.addWidget(kit.Segmented([("a", "A"), ("b", "B")], tip="mode", on=print))
    assert kit.missing_tips(root) == [bare]


def test_segmented_reports_clicks_not_sets(qtbot: Any) -> None:
    got: list[str] = []
    seg = kit.Segmented([("a", "A"), ("b", "B")], tip="mode", on=got.append)
    qtbot.addWidget(seg)
    seg.set("b")
    assert seg.value == "b" and not got
    qtbot.mouseClick(seg.buttons["a"], Qt.MouseButton.LeftButton)
    assert got == ["a"] and seg.value == "a"


def test_items_rebuild_on_change_only(qtbot: Any) -> None:
    items = kit.Items(tip="things", empty="none")
    qtbot.addWidget(items)
    assert items.set_items([]) and items.count() == 1
    rows = [kit.Item("one", 1), kit.Item("two", 2, level="error")]
    assert items.set_items(rows) and not items.set_items(list(rows))
    got: list[object] = []
    items.picked.connect(got.append)
    items.itemClicked.emit(items.item(1))
    assert got == [2]


def test_vec3_resets(qtbot: Any) -> None:
    v = kit.Vec3((1.0, 2.0, 3.0), tip="by")
    qtbot.addWidget(v)
    v.set([4, 5, 6])
    assert v.value() == [4.0, 5.0, 6.0]
    v.reset()
    assert v.value() == [1.0, 2.0, 3.0]


def test_table_picks_row_data(qtbot: Any) -> None:
    t = kit.Table(["name", "size"], tip="rows")
    qtbot.addWidget(t)
    t.set_rows([("a", "1"), ("b", "2")], data=["A", "B"])
    got: list[object] = []
    t.picked.connect(got.append)
    t.cellClicked.emit(1, 0)
    assert got == ["B"] and not t.set_rows([("a", "1"), ("b", "2")], data=["A", "B"])


def test_refill_rebuilds_on_change_only(qtbot: Any) -> None:
    got: list[str] = []
    box = kit.choice([("a", "A")], tip="which", on=got.append)
    qtbot.addWidget(box)
    assert kit.refill(box, [("a", "A"), ("b", "B")], current="b")
    assert not kit.refill(box, [("a", "A"), ("b", "B")]) and box.currentData() == "b" and not got


def test_put_is_quiet(qtbot: Any) -> None:
    got: list[object] = []
    boxes = [
        kit.number(tip="n", decimals=1, on=got.append),
        kit.integer(tip="i", on=got.append),
        kit.check("c", tip="c", on=got.append),
        kit.choice([("a", "A"), ("b", "B")], tip="ch", on=got.append),
        kit.Segmented([("a", "A"), ("b", "B")], tip="s", on=got.append),
        kit.Vec3(tip="v", on=got.append),
    ]
    for w, v in zip(boxes, (2.5, 3, True, "b", "b", (1, 2, 3)), strict=True):
        qtbot.addWidget(w)
        kit.put(w, v)
    assert not got and boxes[0].value() == 2.5 and boxes[3].currentData() == "b"
    assert boxes[5].value() == [1.0, 2.0, 3.0]
