# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from typing import Any

import pytest
from mhfu_studio.ui import kit, theme
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


def test_vec3_fits_a_dock(qtbot: Any) -> None:
    v = kit.Vec3(tip="by", decimals=1)
    qtbot.addWidget(v)
    assert v.minimumSizeHint().width() <= 3 * kit.MIN_FIELD + 16


def test_table_carries_swatch_tip_level(qtbot: Any) -> None:
    t = kit.Table(["slot", "name"], tip="rows", swatch_column=1)
    qtbot.addWidget(t)
    t.set_rows(
        [("1", "walk"), ("2", "run")],
        colors=[(1.0, 0.0, 0.0), None],
        tips=["first", ""],
        levels=["error", None],
    )
    assert t.item(0, 1).icon().isNull() is False and t.item(0, 0).icon().isNull()
    assert t.item(1, 1).icon().isNull() and t.item(0, 0).toolTip() == "first"
    assert t.item(0, 0).foreground().color() == theme.level("error")
    assert not t.set_rows(
        [("1", "walk"), ("2", "run")],
        colors=[(1.0, 0.0, 0.0), None],
        tips=["first", ""],
        levels=["error", None],
    )
    assert t.set_rows([("1", "walk"), ("2", "run")], levels=["warning", None])


def test_table_levels_follow_the_theme(qtbot: Any) -> None:
    theme.apply(theme.theme("Ember", True))
    t = kit.Table(["a"], tip="rows")
    qtbot.addWidget(t)
    t.set_rows([("x",)], levels=["warning"])
    theme.apply(theme.theme("Moss", False))
    assert t.item(0, 0).foreground().color() == theme.level("warning")
    theme.apply(theme.theme("Ember", True))


def test_table_header_aligns_left(qtbot: Any) -> None:
    t = kit.Table(["a"], tip="rows")
    qtbot.addWidget(t)
    assert t.horizontalHeader().defaultAlignment() & Qt.AlignmentFlag.AlignLeft


def test_fit_caps_rows(qtbot: Any) -> None:
    t = kit.Table(["a"], tip="rows")
    qtbot.addWidget(t)
    t.set_rows([(str(i),) for i in range(20)])
    t.fit(3)
    three = t.height()
    t.fit(6)
    assert t.height() > three
    t.set_rows([("x",)])
    t.fit(6)
    assert t.height() < three


def test_grid_edits(qtbot: Any) -> None:
    g = kit.Grid(["a", "b"], tip="values", hi=100)
    qtbot.addWidget(g)
    got: list[tuple[int, int, int]] = []
    g.edited.connect(lambda r, c, v: got.append((r, c, v)))
    assert g.set_cells([["1", "2"]], editable=[True, False], tips=["row"])
    assert not g.set_cells([["1", "2"]], editable=[True, False], tips=["row"])
    assert not g.item(0, 1).flags() & Qt.ItemFlag.ItemIsEditable
    g.item(0, 0).setText("0x10")
    g.item(0, 0).setText("500")
    assert got == [(0, 0, 16)]
    assert g.set_cells([["1", "2"]], editable=[True, False], tips=["row"])  # the old value back


def test_swatch(qtbot: Any) -> None:
    s = kit.Swatch(tip="its colour")
    qtbot.addWidget(s)
    assert s.isHidden()
    s.set((0.2, 0.4, 0.6, 0.5))
    assert not s.isHidden() and not s.pixmap().isNull()
    assert (
        kit.swatch_icon((0.2, 0.4, 0.6)).pixmap(kit.SWATCH).toImage().pixelColor(1, 1).alpha()
        == 255
    )


def test_pages_show_one(qtbot: Any) -> None:
    page, empty = QWidget(), kit.Empty("Nothing", "Open something")
    tall = QWidget()
    tall.setMinimumHeight(300)
    QVBoxLayout(page).addWidget(tall)
    pages = kit.Pages(page, empty)
    qtbot.addWidget(pages)
    assert pages.currentWidget() is page and pages.minimumSizeHint().height() >= 300
    pages.show_page(False)
    assert pages.currentWidget() is empty and pages.minimumSizeHint().height() < 300
    pages.show_page(True)
    assert pages.currentWidget() is page and pages.minimumSizeHint().height() >= 300


def test_numbers_use_a_point(qtbot: Any) -> None:
    b = kit.number(tip="n", value=9.0, decimals=1)
    qtbot.addWidget(b)
    assert b.text() == "9.0"


def test_alert_recolours(qtbot: Any) -> None:
    theme.apply(theme.theme("Ember", True))
    a = kit.Alert("careful")
    qtbot.addWidget(a)
    a.show()
    assert a.palette().color(a.foregroundRole()) == theme.level("warning")
    a.set_level("error")
    assert a.level == "error" and a.palette().color(a.foregroundRole()) == theme.level("error")
    theme.apply(theme.theme("Moss", False))
    assert a.palette().color(a.foregroundRole()) == theme.level("error")
    a.set_level(None)
    assert a.palette().color(a.foregroundRole()).name() == theme.current().text
