# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The controls every panel is made of. Each one that does something takes `tip=`: what it
does, in words someone new understands; `missing_tips` finds the ones that slipped.

Panels use these instead of raw Qt widgets so they look alike and the theme reaches them.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from PySide6.QtCore import QEvent, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractSpinBox,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mhfu_studio.shell.findings import Level
from mhfu_studio.ui import theme

Role = Literal["normal", "primary", "danger"]
#: a number field's least width; three share a 300 px dock
MIN_FIELD = 56
#: a data-colour square's side
SWATCH = 12
LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
Slot = Callable[..., object]


def _tip(w: QWidget, tip: str) -> None:
    if not tip.strip():
        raise ValueError(f"{type(w).__name__} {w.objectName() or ''}: a control needs a tip")
    w.setToolTip(tip)


def _call(fn: Slot) -> Callable[..., None]:
    def run(*_: object) -> None:
        fn()

    return run


# ---- buttons ----------------------------------------------------------------------------- #


def button(
    text: str, *, tip: str, on: Slot, role: Role = "normal", icon: str | None = None
) -> QPushButton:
    """`on` takes no arguments; wrap it in `studio.act` when it changes the document."""
    b = QPushButton(text)
    _tip(b, tip)
    if role != "normal":
        b.setProperty("role", role)
    if icon:
        theme.bind(b, icon)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.clicked.connect(_call(on))
    return b


def icon_button(icon: str, *, tip: str, on: Slot, checkable: bool = False) -> QToolButton:
    b = QToolButton()
    _tip(b, tip)
    theme.bind(b, icon)
    b.setCheckable(checkable)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.clicked.connect(_call(on))
    return b


def check(text: str, *, tip: str, on: Callable[[bool], object], checked: bool = False) -> QCheckBox:
    b = QCheckBox(text)
    _tip(b, tip)
    b.setChecked(checked)
    b.toggled.connect(on)
    return b


class Segmented(QWidget):
    """A pill row of exclusive choices; `on(id)` fires on a click, not on `set`."""

    def __init__(
        self,
        choices: Sequence[tuple[str, str]],
        *,
        tip: str,
        on: Callable[[str], object],
        current: str | None = None,
        tips: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("Seg")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        _tip(self, tip)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.ids = [c for c, _ in choices]
        self.buttons: dict[str, QToolButton] = {}
        for i, (cid, label) in enumerate(choices):
            b = QToolButton()
            b.setText(label)
            b.setCheckable(True)
            b.setToolTip((tips or {}).get(cid, tip))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.group.addButton(b, i)
            lay.addWidget(b)
            self.buttons[cid] = b
        self.set(current if current is not None else self.ids[0])
        self.group.idClicked.connect(lambda i: on(self.ids[i]))

    def set(self, cid: str) -> None:
        if cid in self.buttons:
            self.buttons[cid].setChecked(True)

    @property
    def value(self) -> str:
        return self.ids[max(self.group.checkedId(), 0)]


# ---- values ------------------------------------------------------------------------------ #


def number(
    *,
    tip: str,
    value: float = 0.0,
    step: float = 1.0,
    decimals: int = 0,
    lo: float = -1e9,
    hi: float = 1e9,
    suffix: str = "",
    on: Callable[[float], object] | None = None,
) -> QDoubleSpinBox:
    """`on` fires when an edit is done (Return, focus out, a step), not on every keystroke."""
    b = QDoubleSpinBox()
    _tip(b, tip)
    b.setRange(lo, hi)
    b.setDecimals(decimals)
    b.setSingleStep(step)
    b.setValue(value)
    b.setSuffix(suffix)
    b.setKeyboardTracking(False)
    b.setAccelerated(True)
    b.setMinimumWidth(MIN_FIELD)  # else the range's widest text sets it, wider than a dock
    if on is not None:
        b.valueChanged.connect(on)
    return b


def integer(
    *,
    tip: str,
    value: int = 0,
    lo: int = -(2**31),
    hi: int = 2**31 - 1,
    step: int = 1,
    on: Callable[[int], object] | None = None,
) -> QSpinBox:
    b = QSpinBox()
    _tip(b, tip)
    b.setRange(lo, hi)
    b.setSingleStep(step)
    b.setValue(value)
    b.setKeyboardTracking(False)
    b.setMinimumWidth(MIN_FIELD)
    if on is not None:
        b.valueChanged.connect(on)
    return b


class Vec3(QWidget):
    """x, y, z fields; `value()` reads them, `reset()` returns to the starting value."""

    def __init__(
        self,
        value: Sequence[float] = (0.0, 0.0, 0.0),
        *,
        tip: str,
        step: float = 1.0,
        decimals: int = 1,
        on: Callable[[list[float]], object] | None = None,
    ) -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.default = [float(v) for v in value]
        self.boxes = []
        for axis, v in zip("xyz", self.default, strict=True):
            b = number(tip=f"{tip} ({axis})", value=v, step=step, decimals=decimals)
            b.setPrefix(f"{axis}  ")
            if on is not None:
                b.valueChanged.connect(lambda _v: on(self.value()))
            lay.addWidget(b, 1)
            self.boxes.append(b)

    def value(self) -> list[float]:
        return [b.value() for b in self.boxes]

    def set(self, v: Sequence[float]) -> None:
        for b, x in zip(self.boxes, v, strict=True):
            b.blockSignals(True)
            b.setValue(float(x))
            b.blockSignals(False)

    def reset(self) -> None:
        self.set(self.default)


class Slider(QWidget):
    """A slider with its value beside it; `on(value)` while dragging."""

    def __init__(
        self,
        lo: float,
        hi: float,
        value: float,
        *,
        tip: str,
        decimals: int = 2,
        on: Callable[[float], object] | None = None,
    ) -> None:
        super().__init__()
        self.lo, self.hi, self.decimals = lo, hi, decimals
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        _tip(self.slider, tip)
        self.slider.setRange(0, 1000)
        self.label = label("", role="muted")
        self.label.setMinimumWidth(48)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.label)
        self.slider.valueChanged.connect(self._moved)
        self._on = on
        self.set(value)

    def _moved(self, i: int) -> None:
        v = self.value()
        self.label.setText(f"{v:.{self.decimals}f}")
        if self._on is not None:
            self._on(v)

    def value(self) -> float:
        return self.lo + (self.hi - self.lo) * self.slider.value() / 1000

    def set(self, v: float) -> None:
        self.slider.blockSignals(True)
        span = (self.hi - self.lo) or 1.0
        self.slider.setValue(round((min(max(v, self.lo), self.hi) - self.lo) / span * 1000))
        self.slider.blockSignals(False)
        self.label.setText(f"{self.value():.{self.decimals}f}")


def choice(
    items: Sequence[tuple[str, str]],
    *,
    tip: str,
    on: Callable[[str], object],
    current: str | None = None,
) -> QComboBox:
    """(id, label) pairs; `on(id)` fires on a user's pick."""
    b = QComboBox()
    _tip(b, tip)
    for cid, text in items:
        b.addItem(text, cid)
    if current is not None:
        b.setCurrentIndex(max(b.findData(current), 0))
    b.activated.connect(lambda i: on(b.itemData(i)))
    return b


def refill(box: QComboBox, items: Sequence[tuple[str, str]], current: str | None = None) -> bool:
    """Sets `box`'s (id, label) items and current id without signals; rebuilds only on change."""
    box.blockSignals(True)
    try:
        now = [(box.itemData(i), box.itemText(i)) for i in range(box.count())]
        changed = now != list(items)
        if changed:
            box.clear()
            for cid, text in items:
                box.addItem(text, cid)
        if current is not None:
            box.setCurrentIndex(max(box.findData(current), 0))
        return changed
    finally:
        box.blockSignals(False)


def text_field(
    *,
    tip: str,
    text: str = "",
    placeholder: str = "",
    on: Callable[[str], object] | None = None,
) -> QLineEdit:
    """`on(text)` fires when editing is done."""
    e = QLineEdit(text)
    _tip(e, tip)
    e.setPlaceholderText(placeholder)
    if on is not None:
        e.editingFinished.connect(lambda: on(e.text()))
    return e


def put(w: QWidget, value: object) -> None:
    """Shows `value` in a control without firing its slot: a `sync()` must never act. A text
    field being typed in keeps what is typed."""
    if isinstance(w, Vec3 | Slider):
        w.set(value)  # type: ignore[arg-type]
        return
    with QSignalBlocker(w):
        if isinstance(w, QSpinBox):
            w.setValue(int(value))  # type: ignore[call-overload]
        elif isinstance(w, QDoubleSpinBox):
            w.setValue(float(value))  # type: ignore[arg-type]
        elif isinstance(w, QAbstractButton):
            w.setChecked(bool(value))
        elif isinstance(w, QLineEdit):
            if not w.hasFocus() and w.text() != str(value):
                w.setText(str(value))
        elif isinstance(w, QComboBox):
            w.setCurrentIndex(max(w.findData(value), 0))
        elif isinstance(w, Segmented):
            w.set(str(value))
        else:
            raise TypeError(f"put: {type(w).__name__}")


# ---- text -------------------------------------------------------------------------------- #

LabelRole = Literal["body", "muted", "title", "caps", "chip", "mono", "hint"]


def label(
    text: str = "", *, role: LabelRole = "body", wrap: bool = True, selectable: bool = False
) -> QLabel:
    w = QLabel(text)
    if role != "body":
        w.setProperty("role", role)
    w.setWordWrap(wrap)
    if selectable:
        w.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return w


class Alert(QLabel):
    """Text in a level's colour (None: its role's), which a theme switch recolours."""

    def __init__(
        self,
        text: str = "",
        level: Level | None = "warning",
        *,
        role: LabelRole = "body",
        wrap: bool = True,
    ) -> None:
        super().__init__(text)
        if role != "body":
            self.setProperty("role", role)
        self.setWordWrap(wrap)
        self.set_level(level)

    @property
    def level(self) -> Level | None:
        lv: Level | None = self.property("level")
        return lv

    def set_level(self, level: Level | None) -> None:
        if self.level != level:
            self.setProperty("level", level)
            self.style().unpolish(self)  # the stylesheet's level rule applies on a re-polish
            self.style().polish(self)


def pill(lv: Level, text: str | None = None) -> Alert:
    """A one-line `Alert` that names its level unless given `text`."""
    return Alert(lv if text is None else text, lv, wrap=False)


def swatch_icon(color: Sequence[float]) -> QIcon:
    """A square of a data colour (RGB or RGBA, drawn opaque): the view's own, so a row and
    what it names in the view match."""
    pm = QPixmap(SWATCH, SWATCH)
    pm.fill(theme.color((float(color[0]), float(color[1]), float(color[2]), 1.0)))
    return QIcon(pm)


class Swatch(QLabel):
    """A data colour beside a heading or a field; `set(None)` hides it."""

    def __init__(self, color: Sequence[float] | None = None, tip: str = "") -> None:
        super().__init__()
        if tip:
            self.setToolTip(tip)
        self.set(color)

    def set(self, color: Sequence[float] | None) -> None:
        self.setVisible(color is not None)
        if color is not None:
            self.setPixmap(swatch_icon(color).pixmap(SWATCH, SWATCH))


# ---- layout ------------------------------------------------------------------------------ #


def row(*widgets: QWidget, stretch: bool = False, spacing: int = 6) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for x in widgets:
        lay.addWidget(x)
    if stretch:
        lay.addStretch(1)
    return w


class Form(QWidget):
    """Label: control rows; the label shows the control's tip too."""

    def __init__(self) -> None:
        super().__init__()
        self.layout_ = QFormLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.layout_.setHorizontalSpacing(10)
        self.layout_.setVerticalSpacing(6)

    def row(self, text: str, widget: QWidget) -> QLabel:
        lb = label(text, role="muted", wrap=False)
        lb.setToolTip(widget.toolTip())
        self.layout_.addRow(lb, widget)
        return lb


class Section(QFrame):
    """A titled group inside a panel; add to `body`."""

    def __init__(self, title: str, *, tip: str = "") -> None:
        super().__init__()
        self.setObjectName("Section")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)
        self.title = label(title, role="caps", wrap=False)
        if tip:
            self.title.setToolTip(tip)
        lay.addWidget(self.title)
        self.body = QVBoxLayout()
        self.body.setSpacing(6)
        lay.addLayout(self.body)


class Pages(QStackedWidget):
    """A panel's page, or its empty state (a `Empty`, or a stack of them); only the one shown
    takes room."""

    def __init__(self, page: QWidget, empty: QWidget) -> None:
        super().__init__()
        self.page, self.empty = page, empty
        self._own = [QSizePolicy(page.sizePolicy()), QSizePolicy(empty.sizePolicy())]
        self.addWidget(page)
        self.addWidget(empty)
        self.show_page(True)

    def show_page(self, on: bool) -> None:
        cur = self.page if on else self.empty
        hidden = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        changed = self.currentWidget() is not cur
        for w, own in zip((self.page, self.empty), self._own, strict=True):
            want = own if w is cur else hidden
            if w.sizePolicy() != want:
                w.setSizePolicy(want)
                changed = True
        if changed:
            self.setCurrentWidget(cur)
            self.updateGeometry()


class Empty(QWidget):
    """What a panel shows when it has nothing: what is missing and the way to get it."""

    def __init__(self, title: str, hint: str, action: tuple[str, str, Slot] | None = None) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 18, 12, 18)
        lay.setSpacing(6)
        lay.addStretch(1)
        self.title = label(title, role="title")
        self.hint = label(hint, role="muted")
        for w in (self.title, self.hint):
            w.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            lay.addWidget(w)
        if action is not None:
            text, tip, on = action
            b = button(text, tip=tip, on=on, role="primary")
            lay.addWidget(b, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(2)

    def say(self, title: str, hint: str) -> None:
        self.title.setText(title)
        self.hint.setText(hint)


# ---- lists ------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Item:
    text: str
    data: Hashable = None
    tip: str = ""
    level: Level | None = None


class Items(QListWidget):
    """A list rebuilt only when its items change; `picked` carries a clicked item's data."""

    picked = Signal(object)

    def __init__(self, *, tip: str, empty: str = "") -> None:
        super().__init__()
        _tip(self, tip)
        self.empty = empty
        self._shown: list[Item] | None = None
        self.itemClicked.connect(self._pick)
        self.itemActivated.connect(self._pick)

    def set_items(self, items: Sequence[Item]) -> bool:
        """True when it rebuilt."""
        items = list(items)
        if items == self._shown:
            return False
        self._shown = items
        self.clear()
        for it in items:
            w = QListWidgetItem(it.text)
            w.setData(Qt.ItemDataRole.UserRole, it.data)
            if it.tip:
                w.setToolTip(it.tip)
            if it.level is not None:
                w.setForeground(theme.level(it.level))
            self.addItem(w)
        if not items and self.empty:
            w = QListWidgetItem(self.empty)
            w.setFlags(Qt.ItemFlag.NoItemFlags)
            self.addItem(w)
        return True

    def changeEvent(self, e: QEvent) -> None:  # noqa: N802
        super().changeEvent(e)
        if e.type() == QEvent.Type.StyleChange:  # a theme switch: level colours follow
            for i, it in enumerate(self._shown or []):
                if it.level is not None:
                    self.item(i).setForeground(theme.level(it.level))

    def _pick(self, w: QListWidgetItem) -> None:
        data = w.data(Qt.ItemDataRole.UserRole)
        if data is not None:
            self.picked.emit(data)


def fit_rows(table: QTableWidget, most: int) -> None:
    """`table` as tall as its rows, up to `most` of them, with room for a sideways scroll bar."""
    n = max(1, min(table.rowCount(), most))
    head = table.horizontalHeader().sizeHint().height()
    bar = table.horizontalScrollBar().sizeHint().height()
    table.setFixedHeight(head + n * table.verticalHeader().defaultSectionSize() + bar + 4)


#: a shown row: cells, data, swatch colour, tip, level
_Row = tuple[tuple[str, ...], Hashable, tuple[float, ...] | None, str, Level | None]


class Table(QTableWidget):
    """Rows of text, rebuilt only when they change; `picked` carries a clicked row's data.

    A row may also carry a data colour (a swatch in `swatch_column`), a tip and a level, whose
    colour follows the theme."""

    picked = Signal(object)

    def __init__(self, headers: Sequence[str], *, tip: str, swatch_column: int = 0) -> None:
        super().__init__(0, len(headers))
        _tip(self, tip)
        self.swatch_column = swatch_column
        self.setHorizontalHeaderLabels(list(headers))
        self.verticalHeader().hide()
        head = self.horizontalHeader()
        head.setStretchLastSection(True)
        head.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        head.setDefaultAlignment(LEFT)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self._shown: list[_Row] | None = None
        self.cellClicked.connect(lambda r, _c: self._pick(r))

    def set_rows(
        self,
        rows: Sequence[Sequence[str]],
        data: Sequence[Hashable] | None = None,
        *,
        colors: Sequence[Sequence[float] | None] | None = None,
        tips: Sequence[str] | None = None,
        levels: Sequence[Level | None] | None = None,
    ) -> bool:
        """True when it rebuilt."""
        keyed: list[_Row] = []
        for i, r in enumerate(rows):
            c = None if colors is None else colors[i]
            d = None if data is None else data[i]
            tip, lv = "" if tips is None else tips[i], None if levels is None else levels[i]
            keyed.append((tuple(r), d, None if c is None else tuple(c), tip, lv))
        if keyed == self._shown:
            return False
        self._shown = keyed
        self.setRowCount(len(keyed))
        for i, (cells, d, color, tip, lv) in enumerate(keyed):
            for j, text in enumerate(cells):
                it = QTableWidgetItem(text)
                it.setData(Qt.ItemDataRole.UserRole, d)
                if tip:
                    it.setToolTip(tip)
                if lv is not None:
                    it.setForeground(theme.level(lv))
                if color is not None and j == self.swatch_column:
                    it.setIcon(swatch_icon(color))
                self.setItem(i, j, it)
        return True

    def fit(self, most: int = 8) -> None:
        fit_rows(self, most)

    def changeEvent(self, e: QEvent) -> None:  # noqa: N802
        super().changeEvent(e)
        if e.type() == QEvent.Type.StyleChange:  # a theme switch: level colours follow
            for i, row in enumerate(self._shown or []):
                lv = row[4]
                if lv is None:
                    continue
                for j in range(self.columnCount()):
                    it = self.item(i, j)
                    if it is not None:
                        it.setForeground(theme.level(lv))

    def select_data(self, d: Hashable) -> None:
        for i, row in enumerate(self._shown or []):
            if row[1] == d:
                self.selectRow(i)
                return
        self.clearSelection()

    def _pick(self, r: int) -> None:
        if self._shown is not None and 0 <= r < len(self._shown):
            self.picked.emit(self._shown[r][1])


class Grid(QTableWidget):
    """Numbers to type into: `edited(row, column, value)` after an edit parses (`0x` hex too)
    and falls in `lo..hi`. Cells are rebuilt only when what they show changes."""

    edited = Signal(int, int, int)

    def __init__(
        self,
        columns: Sequence[str],
        *,
        tip: str,
        lo: int = 0,
        hi: int = 255,
        rows: Sequence[str] = (),
    ) -> None:
        super().__init__(len(rows), len(columns))
        _tip(self, tip)
        self.lo, self.hi = lo, hi
        self.setHorizontalHeaderLabels(list(columns))
        if rows:
            self.setVerticalHeaderLabels(list(rows))
        else:
            self.verticalHeader().hide()
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.horizontalHeader().setStretchLastSection(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        self._shown: object = None
        self.itemChanged.connect(self._changed)

    def set_cells(
        self,
        cells: Sequence[Sequence[str]],
        *,
        editable: bool | Sequence[bool] = True,
        tips: Sequence[str] | None = None,
        headers: Sequence[str] | None = None,
    ) -> bool:
        """Rows of cell text; `editable` for all, or per column. True when it rebuilt."""
        key = (tuple(map(tuple, cells)), editable, None if tips is None else tuple(tips), headers)
        if key == self._shown:
            return False
        self._shown = key
        with QSignalBlocker(self):
            self.setRowCount(len(cells))
            if headers is not None:
                self.setVerticalHeaderLabels(list(headers))
            for r, row in enumerate(cells):
                for c, text in enumerate(row):
                    it = QTableWidgetItem(text)
                    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    on = editable if isinstance(editable, bool) else editable[c]
                    if not on:
                        it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    if tips is not None and tips[r]:
                        it.setToolTip(tips[r])
                    self.setItem(r, c, it)
        return True

    def fit(self, most: int = 10) -> None:
        fit_rows(self, most)

    def _changed(self, it: QTableWidgetItem) -> None:
        try:
            v = int(it.text().strip(), 0)
        except ValueError:
            v = None
        if v is None or not self.lo <= v <= self.hi:
            self._shown = None  # the next sync puts the old value back
            return
        self.edited.emit(it.row(), it.column(), v)


# ---- panels ------------------------------------------------------------------------------ #


class Panel(QWidget):
    """A dock's content: a card on the window gradient. Add to `body`; implement `sync()`."""

    def __init__(self, *, scroll: bool = True) -> None:
        super().__init__()
        self.setObjectName("Card")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        inner = QWidget()
        inner.setObjectName("Body")
        self.body = QVBoxLayout(inner)
        self.body.setContentsMargins(10, 10, 10, 10)
        self.body.setSpacing(8)
        if scroll:
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setFrameShape(QFrame.Shape.NoFrame)
            area.setWidget(inner)
            outer.addWidget(area)
        else:
            outer.addWidget(inner)

    def sync(self) -> None:
        """Re-reads what the panel shows; cheap and idempotent, called after every change."""


# ---- every control explains itself ------------------------------------------------------ #

INTERACTIVE = (QAbstractButton, QAbstractSpinBox, QComboBox, QLineEdit, QSlider, QAbstractItemView)


def missing_tips(root: QWidget) -> list[QWidget]:
    """Controls under `root` (itself included) without a tooltip; a control's own parts
    (a spin box's line edit, a combo box's list) do not count."""
    out: list[QWidget] = []
    for w in [root, *root.findChildren(QWidget)]:
        if not isinstance(w, INTERACTIVE) or w.toolTip().strip():
            continue
        p, inside = w.parentWidget(), False
        while p is not None and p is not root.parentWidget():
            if isinstance(p, INTERACTIVE) or isinstance(p, Segmented):
                inside = True
                break
            p = p.parentWidget()
        if not inside and not _is_chrome(w):
            out.append(w)
    return out


def _is_chrome(w: QWidget) -> bool:
    """Scroll bars, header sections and the like: parts of a container, not controls."""
    name = type(w).__name__
    return name in {"QScrollBar", "QHeaderView", "QTabBar"} or w.objectName().startswith("qt_")
