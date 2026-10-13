# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The property bar under the Behaviour canvas: the selected node's kind, one editor per param
and its note, in one row. The canvas draws a node compact; what it holds is edited here.

A control tells the user's edit and never `set_value`'s; the owner applies it and shows the node
again, which also puts a refused edit back.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from mhfu_port.behaviour import Param
from PySide6.QtCore import QObject, QSignalBlocker, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QSpinBox,
    QToolButton,
    QWidget,
)

from mhfu_studio.ui import kit

Option = tuple[str, object]
NONE = "none"
#: where a number field with no bound ends
_FAR = 10**9
HEIGHT = 40


class ParamControl(QObject):
    """One parameter's control, or the note when `param` is None; `edited` is the user's edit."""

    edited = Signal()

    def __init__(self, param: Param | None, options: Sequence[Option] = ()) -> None:
        super().__init__()
        self.optional = param is not None and param.optional
        self._vals: list[object] = []
        self._opts: list[Option] = []
        self._built = False
        self.ctl = self._make(param)
        if param is not None:
            self.ctl.setToolTip(param.tip)
        self.set_options(options)

    def _make(self, param: Param | None) -> QWidget:
        if param is None:
            line = QLineEdit()
            line.setPlaceholderText("note")
            line.setMinimumWidth(160)
            line.editingFinished.connect(self._changed)
            return line
        if param.type in ("int", "float"):
            return self._number(param)
        if param.type == "mains":
            button = QToolButton()
            button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            button.setMinimumWidth(120)
            menu = QMenu(button)
            menu.triggered.connect(self._changed)  # a click, not a `setChecked`
            button.setMenu(menu)
            return button
        combo = QComboBox()
        combo.setMinimumWidth(120)
        combo.currentIndexChanged.connect(self._changed)
        return combo

    def _number(self, param: Param) -> QWidget:
        lo = -_FAR if param.lo is None else param.lo
        hi = _FAR if param.hi is None else param.hi
        box: QSpinBox | QDoubleSpinBox
        if param.type == "int":
            box = QSpinBox()
            box.setRange(int(lo) - param.optional, int(hi))
        else:
            box = QDoubleSpinBox()
            step = 0.1 if hi - lo >= 1 else 0.01
            box.setDecimals(3)
            box.setSingleStep(step)
            box.setRange(lo - step * param.optional, hi)
        if param.optional:
            box.setSpecialValueText(NONE)  # the least value shows as "none"
        box.setKeyboardTracking(False)  # one change per edit, not per keystroke
        box.setMinimumWidth(80)
        box.valueChanged.connect(self._changed)
        return box

    # -- the value -- #

    def get_value(self) -> object:
        ctl = self.ctl
        if isinstance(ctl, QLineEdit):
            return ctl.text()
        if isinstance(ctl, QSpinBox | QDoubleSpinBox):
            if self.optional and ctl.value() == ctl.minimum():
                return None
            return int(ctl.value()) if isinstance(ctl, QSpinBox) else float(ctl.value())
        if isinstance(ctl, QComboBox):
            return (
                self._vals[ctl.currentIndex()]
                if 0 <= ctl.currentIndex() < len(self._vals)
                else None
            )
        return tuple(
            v for (_, v), a in zip(self._opts, self._actions(), strict=True) if a.isChecked()
        )

    def set_value(self, value: object) -> None:
        ctl = self.ctl
        with QSignalBlocker(ctl):
            if isinstance(ctl, QLineEdit):
                ctl.setText("" if value is None else str(value))
            elif isinstance(ctl, QSpinBox | QDoubleSpinBox):
                ctl.setValue(cast(Any, ctl.minimum() if value is None else value))
            elif isinstance(ctl, QComboBox):
                if value not in self._vals:  # a value the options lack still shows
                    self._vals.append(value)
                    ctl.addItem(str(value))
                ctl.setCurrentIndex(self._vals.index(value))
            else:
                picked = list(cast(Any, value or ()))
                for (_, v), a in zip(self._opts, self._actions(), strict=True):
                    a.setChecked(v in picked)
                self._summary()

    def same(self, value: object) -> bool:
        """Whether the control already shows `value` (numbers to the box's own rounding)."""
        have = self.get_value()
        if isinstance(have, float) and isinstance(value, int | float):
            return abs(have - value) < 5e-4
        if isinstance(self.ctl, QToolButton):
            return set(cast(Any, have)) == set(cast(Any, value or ()))
        return have == value

    def set_options(self, options: Sequence[Option]) -> None:
        """Replaces a combo's or a multi-select's entries, keeping what is picked."""
        ctl = self.ctl
        if not isinstance(ctl, QComboBox | QToolButton):
            return
        built = self._built
        if built and list(options) == self._opts:
            return
        keep = self.get_value() if built else None
        self._built, self._opts = True, list(options)
        if isinstance(ctl, QComboBox):
            none = self.optional and all(v is not None for _, v in self._opts)
            self._vals = ([None] if none else []) + [v for _, v in self._opts]
            with QSignalBlocker(ctl):
                ctl.clear()
                ctl.addItems([NONE] * none + [label for label, _ in self._opts])
        else:
            menu = ctl.menu()
            menu.clear()
            for label, _ in self._opts:
                menu.addAction(label).setCheckable(True)
        if built:
            self.set_value(keep)
        elif isinstance(ctl, QToolButton):
            self._summary()

    def _actions(self) -> list[Any]:
        return list(cast(QToolButton, self.ctl).menu().actions())

    def _summary(self) -> None:
        names = [
            label
            for (label, _), a in zip(self._opts, self._actions(), strict=True)
            if a.isChecked()
        ]
        text = ", ".join(names) if 0 < len(names) <= 2 else f"{len(names)} picked"
        cast(QToolButton, self.ctl).setText(text if names else (NONE if self.optional else "-"))

    def _changed(self, *_: object) -> None:
        if isinstance(self.ctl, QToolButton):
            self._summary()
        self.edited.emit()


@dataclass(frozen=True)
class Detail:
    """What the bar shows of one node: its kind, params with their `values` and `options` (a
    combo's or a multi-select's (label, value) list, else the param's `choices`), its note.
    A node with no params and no `note` shows only its title and tip."""

    id: str
    title: str
    tip: str = ""
    params: tuple[Param, ...] = ()
    values: Mapping[str, object] = field(default_factory=dict)
    options: Mapping[str, Sequence[Option]] = field(default_factory=dict)
    label: str = ""
    note: bool = True


class PropertyBar(QWidget):
    """One row of editors for one node; empty for none."""

    param_changed = Signal(str, str, object)
    label_changed = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(HEIGHT)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(2, 0, 2, 0)
        self._lay.setSpacing(8)
        self._shape: tuple[object, ...] | None = None
        self._id = ""
        #: the note as the document holds it: an edit that leaves it so says nothing
        self._label = ""
        self.controls: dict[str, ParamControl] = {}
        self.note: ParamControl | None = None
        self._owned: list[QWidget] = []

    def show_detail(self, d: Detail | None) -> None:
        """The editors of `d`; the same node again only brings their values up to date, so a
        refused edit goes back and an accepted one shows as the document holds it."""
        if d is None:
            self._clear()
            return
        shape = (d.id, d.title, d.note, tuple((p.name, p.type) for p in d.params))
        if shape != self._shape:
            self._clear()
            self._build(d)
            self._shape = shape
        self._id, self._label = d.id, d.label
        for p in d.params:
            c = self.controls[p.name]
            c.set_options(self._options(d, p))
            want = d.values.get(p.name)
            if (want is not None or c.optional) and not c.same(want):
                c.set_value(want)
        if self.note is not None and not self.note.same(d.label):
            self.note.set_value(d.label)

    @staticmethod
    def _options(d: Detail, p: Param) -> list[Option]:
        got = d.options.get(p.name)
        return list(got) if got is not None else [(c, c) for c in p.choices]

    def _noted(self, text: str) -> None:
        if text != self._label:
            self.label_changed.emit(self._id, text)

    def _clear(self) -> None:
        for w in self._owned:
            self._lay.removeWidget(w)
            w.deleteLater()
        for c in [*self.controls.values(), *([self.note] if self.note else [])]:
            c.deleteLater()
        self._owned, self.controls, self.note, self._shape = [], {}, None, None

    def _add(self, w: QWidget, stretch: int = 0) -> None:
        self._lay.addWidget(w, stretch)
        self._owned.append(w)

    def _build(self, d: Detail) -> None:
        self._id = d.id
        title = kit.label(d.title, role="title", wrap=False)
        title.setToolTip(d.tip)
        self._add(title)
        for p in d.params:
            self._add(kit.label(p.title, role="muted", wrap=False))
            c = ParamControl(p, self._options(d, p))
            c.edited.connect(
                lambda c=c, n=p.name: self.param_changed.emit(self._id, n, c.get_value())
            )
            self.controls[p.name] = c
            self._add(c.ctl)
        if d.note:
            self._add(kit.label("Note", role="muted", wrap=False))
            note = self.note = ParamControl(None)
            note.ctl.setToolTip("A note on this block, kept in the port manifest")
            note.edited.connect(lambda: self._noted(str(note.get_value())))
            self._add(note.ctl, 1)
        else:
            self._add(kit.label(d.tip.split("\n")[0], role="muted", wrap=False), 1)
