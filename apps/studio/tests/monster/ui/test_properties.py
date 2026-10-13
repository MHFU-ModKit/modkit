# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The property bar and its controls, alone: what each control tells of an edit, and what the
bar does when it is shown a node again."""

from __future__ import annotations

from typing import Any

import pytest
from mhfu_port.behaviour import Param
from mhfu_studio.monster.panels.properties import Detail, ParamControl, PropertyBar
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QLabel, QLineEdit, QSpinBox, QToolButton

COUNT = Param("n", "int", "Times", 1, lo=1, hi=9, tip="how often")
HP = Param("hp", "float", "HP below", optional=True, lo=0, hi=1, tip="a fraction")
OP = Param("op", "choice", "Compare", choices=("below", "above"), tip="which way")
PART = Param("part", "part", "Part", optional=True, tip="a part")
MAINS = Param("mains", "mains", "Mains", tip="mains")
HEADS = [("Head", 1), ("Tail", 2)]
PARTS = [("any", None), *HEADS]
MAINS_AT = [("one", 1), ("two", 2), ("three", 3)]


def told(c: ParamControl) -> list[object]:
    """What a control says each time it reports an edit."""
    out: list[object] = []
    c.edited.connect(lambda: out.append(c.get_value()))
    return out


def test_each_control_tells_the_users_edit(qtbot: Any) -> None:
    spin, real = ParamControl(COUNT), ParamControl(HP)
    combo = ParamControl(OP, [("below", "below"), ("above", "above")])
    part, mains = ParamControl(PART, PARTS), ParamControl(MAINS, MAINS_AT)
    note = ParamControl(None)
    kinds = (QSpinBox, QDoubleSpinBox, QComboBox, QComboBox, QToolButton, QLineEdit)
    got = [c.ctl for c in (spin, real, combo, part, mains, note)]
    assert [type(w) for w in got] == list(kinds)
    seen = {id(c): told(c) for c in (spin, real, combo, part, mains, note)}
    spin.set_value(3)
    mains.set_value((1,))
    assert all(v == [] for v in seen.values()), "set_value is not an edit"
    got[0].setValue(5)
    got[1].setValue(0.25)
    got[2].setCurrentIndex(1)
    got[3].setCurrentIndex(2)
    mains.ctl.menu().actions()[2].trigger()
    got[5].setText("why")
    got[5].editingFinished.emit()
    assert [seen[id(c)] for c in (spin, real, combo, part, mains, note)] == [
        [5],
        [0.25],
        ["above"],
        [2],
        [(1, 3)],
        ["why"],
    ]


def test_optional_numbers_have_none(qtbot: Any) -> None:
    real = ParamControl(HP)
    assert real.ctl.specialValueText() == "none"
    real.set_value(0.5)
    assert real.get_value() == 0.5
    real.set_value(None)
    assert real.get_value() is None and real.ctl.value() == real.ctl.minimum()
    real.ctl.setValue(0.0)
    assert real.get_value() == 0.0
    assert real.same(0.0) and not real.same(None)


def test_an_option_of_none_is_the_owners_own_none(qtbot: Any) -> None:
    part = ParamControl(PART, PARTS)
    ctl = part.ctl
    assert [ctl.itemText(i) for i in range(ctl.count())] == ["any", "Head", "Tail"]
    assert part.get_value() is None
    told_ = told(part)
    ctl.setCurrentIndex(2)
    ctl.setCurrentIndex(0)
    assert told_ == [2, None]


def test_a_value_the_options_lack_still_shows(qtbot: Any) -> None:
    part = ParamControl(PART, HEADS)
    part.set_value(9)
    assert part.get_value() == 9 and part.ctl.currentText() == "9"


def test_new_options_keep_the_pick(qtbot: Any) -> None:
    part, mains = ParamControl(PART, HEADS), ParamControl(MAINS, MAINS_AT)
    part.set_value(2)
    mains.set_value((1, 3))
    told_ = [told(part), told(mains)]
    part.set_options([("Tail", 2), ("Wing", 3)])
    mains.set_options([("one", 1), ("three", 3), ("five", 5)])
    assert part.get_value() == 2 and part.ctl.count() == 3  # none, Tail, Wing
    assert mains.get_value() == (1, 3) and told_ == [[], []]
    assert mains.ctl.text() == "one, three"


def test_the_multi_select_says_how_many_it_holds(qtbot: Any) -> None:
    mains = ParamControl(MAINS, MAINS_AT)
    assert mains.ctl.text() == "-"
    mains.set_value((1,))
    assert mains.ctl.text() == "one"
    mains.set_value((1, 2, 3))
    assert mains.ctl.text() == "3 picked"
    assert mains.same([3, 2, 1])


def detail(**kw: Any) -> Detail:
    base: dict[str, Any] = {
        "id": "b1",
        "title": "Check",
        "tip": "checks",
        "params": (OP, HP, PART),
        "values": {"op": "above", "hp": 0.5, "part": 2},
        "options": {"part": PARTS},
        "label": "a note",
    }
    return Detail(**(base | kw))


def test_the_bar_is_empty_until_it_is_shown_a_node(qtbot: Any) -> None:
    bar = PropertyBar()
    qtbot.addWidget(bar)
    assert bar.controls == {} and bar.note is None
    bar.show_detail(detail())
    assert list(bar.controls) == ["op", "hp", "part"] and bar.note is not None
    bar.show_detail(None)
    assert bar.controls == {} and bar.note is None


def test_the_bar_takes_choices_from_the_param(qtbot: Any) -> None:
    bar = PropertyBar()
    qtbot.addWidget(bar)
    bar.show_detail(detail())
    combo = bar.controls["op"].ctl
    assert [combo.itemText(i) for i in range(combo.count())] == ["below", "above"]
    assert bar.controls["op"].get_value() == "above" and bar.controls["part"].get_value() == 2
    assert bar.note is not None and bar.note.get_value() == "a note"


def test_the_bar_tells_edits_with_the_node(qtbot: Any) -> None:
    bar = PropertyBar()
    qtbot.addWidget(bar)
    bar.show_detail(detail())
    params: list[tuple[str, str, object]] = []
    notes: list[tuple[str, str]] = []
    bar.param_changed.connect(lambda *a: params.append(a))
    bar.label_changed.connect(lambda *a: notes.append(a))
    bar.controls["op"].ctl.setCurrentIndex(0)
    bar.controls["hp"].ctl.setValue(0.25)
    assert bar.note is not None
    bar.note.ctl.setText("why")
    bar.note.ctl.editingFinished.emit()
    assert params == [("b1", "op", "below"), ("b1", "hp", 0.25)]
    assert notes == [("b1", "why")]


def test_an_unchanged_note_says_nothing(qtbot: Any) -> None:
    bar = PropertyBar()
    qtbot.addWidget(bar)
    bar.show_detail(detail())
    notes: list[tuple[str, str]] = []
    bar.label_changed.connect(lambda *a: notes.append(a))
    assert bar.note is not None
    bar.note.ctl.editingFinished.emit()
    assert notes == []


def test_the_same_node_again_updates_quietly_and_keeps_the_editors(qtbot: Any) -> None:
    bar = PropertyBar()
    qtbot.addWidget(bar)
    bar.show_detail(detail())
    editors = dict(bar.controls)
    heard: list[Any] = []
    bar.param_changed.connect(lambda *a: heard.append(a))
    bar.label_changed.connect(lambda *a: heard.append(a))
    bar.show_detail(
        detail(values={"op": "below", "hp": None, "part": 1}, label="", options={"part": HEADS})
    )
    assert bar.controls == editors and heard == []
    assert bar.controls["op"].get_value() == "below" and bar.controls["hp"].get_value() is None
    assert bar.controls["part"].get_value() == 1 and bar.note is not None
    assert bar.note.get_value() == ""
    bar.show_detail(detail(id="b2"))
    assert all(bar.controls[k] is not editors[k] for k in editors), "another node: new editors"


def test_a_node_without_params_or_a_note_shows_its_name_only(qtbot: Any) -> None:
    bar = PropertyBar()
    qtbot.addWidget(bar)
    bar.show_detail(Detail("move:x", "x", "own move", note=False))
    assert bar.controls == {} and bar.note is None
    texts = [w.text() for w in bar.findChildren(QLabel)]
    assert texts == ["x", "own move"]


# ---- names and sides ----------------------------------------------------------------------- #

COUNTER = Param("counter", "var", "Counter", tip="which counter")
SIGNAL = Param("name", "signal", "Signal", tip="which signal")
SIDES = Param("sides", "sides", "Sides", choices=("front", "left", "right", "behind"), tip="sides")
NAMES_IN_USE = [("armor", "armor"), ("hits", "hits")]


def named(**kw: Any) -> Detail:
    """A node of a counter, a signal and sides."""
    base: dict[str, Any] = {
        "id": "e",
        "title": "Count",
        "tip": "counts",
        "params": (COUNTER, SIGNAL, SIDES),
        "values": {"counter": "hits", "name": "rage", "sides": ("front",)},
        "options": {"counter": NAMES_IN_USE, "name": [("rage", "rage")]},
    }
    return Detail(**(base | kw))


def shown(qtbot: Any) -> tuple[PropertyBar, list[tuple[str, str, object]]]:
    """A bar showing `named()`, and every edit it tells."""
    bar = PropertyBar()
    qtbot.addWidget(bar)
    bar.show_detail(named())
    got: list[tuple[str, str, object]] = []
    bar.param_changed.connect(lambda *a: got.append(a))
    return bar, got


def combo(bar: PropertyBar, name: str) -> QComboBox:
    ctl = bar.controls[name].ctl
    assert isinstance(ctl, QComboBox)
    return ctl


def test_a_name_is_an_editable_combo_over_the_names_in_use(qtbot: Any) -> None:
    bar, _ = shown(qtbot)
    counter = combo(bar, "counter")
    assert counter.isEditable()
    assert [counter.itemText(i) for i in range(counter.count())] == ["armor", "hits"]
    assert (bar.controls["counter"].get_value(), bar.controls["name"].get_value()) == (
        "hits",
        "rage",
    )
    assert counter.currentIndex() == 1 and counter.currentText() == "hits"
    bar.show_detail(named(values={"counter": "fresh", "name": "rage"}))
    assert bar.controls["counter"].get_value() == "fresh" and counter.currentIndex() == -1


def test_sides_are_a_multi_select_over_the_choices(qtbot: Any) -> None:
    bar, _ = shown(qtbot)
    sides = bar.controls["sides"]
    assert isinstance(sides.ctl, QToolButton) and sides.get_value() == ("front",)
    assert [a.text() for a in sides.ctl.menu().actions()] == ["front", "left", "right", "behind"]
    assert sides.ctl.text() == "front"
    bar.show_detail(named(values={"sides": ("left", "behind")}))
    assert sides.get_value() == ("left", "behind") and sides.ctl.text() == "left, behind"


def test_each_new_control_says_its_edit_once(qtbot: Any) -> None:
    bar, got = shown(qtbot)
    counter, signal = combo(bar, "counter"), combo(bar, "name")
    counter.setCurrentIndex(0)  # a pick
    assert got == [("e", "counter", "armor")]
    got.clear()
    counter.setEditText("fresh_1")  # a new name, finished by Return or by leaving
    edit = counter.lineEdit()
    assert edit is not None
    edit.editingFinished.emit()
    edit.editingFinished.emit()
    assert got == [("e", "counter", "fresh_1")]
    got.clear()
    sig = signal.lineEdit()
    assert sig is not None
    sig.selectAll()
    QTest.keyClicks(sig, "calm")
    QTest.keyClick(sig, Qt.Key.Key_Return)
    assert got == [("e", "name", "calm")]
    got.clear()
    sides = bar.controls["sides"].ctl
    assert isinstance(sides, QToolButton)
    sides.menu().actions()[2].trigger()
    assert got == [("e", "sides", ("front", "right"))]


def test_a_name_that_is_not_a_name_goes_back_unheard(qtbot: Any) -> None:
    bar, got = shown(qtbot)
    counter = combo(bar, "counter")
    edit = counter.lineEdit()
    assert edit is not None
    for bad in ("Hits", "9lives", "two words", "x-y", "", "  "):
        counter.setEditText(bad)
        edit.editingFinished.emit()
        assert bar.controls["counter"].get_value() == "hits" and counter.currentText() == "hits", (
            bad
        )
    assert got == []


def test_a_name_with_spaces_around_it_is_the_name(qtbot: Any) -> None:
    bar, got = shown(qtbot)
    counter = combo(bar, "counter")
    edit = counter.lineEdit()
    assert edit is not None
    counter.setEditText("  fresh ")
    edit.editingFinished.emit()
    assert counter.currentText() == "fresh" and got == [("e", "counter", "fresh")]


def test_a_refused_name_goes_back_and_is_asked_again(qtbot: Any) -> None:
    bar, got = shown(qtbot)
    counter = combo(bar, "counter")
    edit = counter.lineEdit()
    assert edit is not None
    for _ in range(2):
        got.clear()
        counter.setEditText("fresh")
        edit.editingFinished.emit()
        assert got == [("e", "counter", "fresh")]
        bar.show_detail(named())  # the document refused it: shown as it holds it
        assert bar.controls["counter"].get_value() == "hits"


def test_an_accepted_name_stays_and_is_an_option(qtbot: Any) -> None:
    bar, _ = shown(qtbot)
    counter = combo(bar, "counter")
    edit = counter.lineEdit()
    assert edit is not None

    def take(i: str, name: str, value: object) -> None:
        opts = [("armor", "armor"), ("fresh", "fresh"), ("hits", "hits")]
        bar.show_detail(named(values={"counter": value}, options={"counter": opts}))

    bar.param_changed.connect(take)
    counter.setEditText("fresh")
    edit.editingFinished.emit()
    assert bar.controls["counter"].get_value() == "fresh"
    assert counter.currentIndex() == 1 and counter.count() == 3


def test_new_names_keep_what_is_picked(qtbot: Any) -> None:
    bar, _ = shown(qtbot)
    counter = combo(bar, "counter")
    counter.setEditText("half_typ")  # not finished: not held
    bar.show_detail(named(options={"counter": [("hits", "hits"), ("zeta", "zeta")]}))
    assert bar.controls["counter"].get_value() == "hits" and counter.count() == 2


def test_an_unknown_type_raises(qtbot: Any) -> None:
    with pytest.raises(ValueError, match="unknown type 'number'"):
        ParamControl(Param("x", "number", "X"))  # type: ignore[arg-type]
