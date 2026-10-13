# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The property bar and its controls, alone: what each control tells of an edit, and what the
bar does when it is shown a node again."""

from __future__ import annotations

from typing import Any

from mhfu_port.behaviour import Param
from mhfu_studio.monster.panels.properties import Detail, ParamControl, Play, PropertyBar
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


def test_play_is_off_with_its_reason_in_the_tip_and_comes_back(qtbot: Any) -> None:
    bar = PropertyBar()
    qtbot.addWidget(bar)
    asked: list[str] = []
    bar.play_requested.connect(asked.append)
    bar.show_detail(Detail("m", "m", "tip", play=Play("m", "no game running")))
    assert bar.play is not None and not bar.play.isEnabled()
    assert bar.play.toolTip() == "Play in game: no game running"
    bar.play.click()
    bar.show_detail(Detail("m", "m", "tip", play=Play("m")))
    assert bar.play is not None and bar.play.isEnabled() and asked == []
    bar.play.click()
    assert asked == ["m"]
